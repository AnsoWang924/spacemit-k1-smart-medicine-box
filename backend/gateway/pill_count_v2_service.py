"""8799 adapter for the latest K1 pill/sachet INT8 detector.

The public gateway contract stays unchanged (``/api/v2/pill-count``), while
the implementation uses the same one-shot 960x544 OV5647 capture path as
medicine-box OCR, followed by the 640x640 INT8 detector and SpaceMIT EP.
"""
from __future__ import annotations

import os
import importlib.util
import multiprocessing as mp
import threading
import time
from pathlib import Path


INFERENCE_TIMEOUT_SECONDS = 20
WORKER_START_TIMEOUT_SECONDS = 30
SESSION_LEASE_SECONDS = 120

PILL_ROOT = Path(__file__).resolve().parents[1] / "pill_detection"
DETECTOR_SCRIPT = PILL_ROOT / "pill_detection_service.py"
DETECTOR_MODEL = PILL_ROOT / "models/k1_pill_yolov8n_640_static_qdq_convonly_int8_minmax_curated64.onnx"
CAMERA_BASE_JSON = "/usr/share/camera_json/csi1_live.json"
CAPTURE_WIDTH = 960
CAPTURE_HEIGHT = 544
MODEL_INPUT_SIZE = 640
FRAME_PATH = "/tmp/pill_count_v2_latest.jpg"
TCM_BLOCKS = 1


def scaled_decode_thresholds(frame_width: int, frame_height: int) -> dict[str, float]:
    """Preserve the tuned 960x544 filtering semantics at any source resolution."""
    scale_x = max(1, int(frame_width)) / CAPTURE_WIDTH
    scale_y = max(1, int(frame_height)) / CAPTURE_HEIGHT
    scale_area = scale_x * scale_y
    return {
        "min_area": 20.0 * scale_area,
        "max_area": 180000.0 * scale_area,
        "pill_max_area_ratio": 0.0285714286,
        "sachet_min_area": 8000.0 * scale_area,
        "sachet_min_width": 80.0 * scale_x,
        "sachet_min_height": 45.0 * scale_y,
    }


def _pin_process_threads(cpu: int) -> None:
    """Pin provider-created native threads as well as the Python main thread."""
    try:
        tids = [int(item.name) for item in Path("/proc/self/task").iterdir() if item.name.isdigit()]
        for tid in tids or [0]:
            os.sched_setaffinity(tid, {cpu})
    except (AttributeError, OSError):
        pass


def _load_detector_module(script_path: str):
    spec = importlib.util.spec_from_file_location("k1_pill_detector_latest", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _worker(model_path: str, connection) -> None:
    """Own ONNX/SpaceMIT state in a killable child process."""
    try:
        _pin_process_threads(2)
        import numpy as np
        import onnxruntime as ort
        import spacemit_ort  # noqa: F401

        os.environ.setdefault("PILL_SPACEMIT_AFFINITY", "2")
        module = _load_detector_module(str(DETECTOR_SCRIPT))

        session_options = ort.SessionOptions()
        session_options.intra_op_num_threads = 1
        session_options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(model_path),
            sess_options=session_options,
            providers=["SpaceMITExecutionProvider", "CPUExecutionProvider"],
            provider_options=[
                {
                    "SPACEMIT_EP_INTRA_THREAD_NUM": str(TCM_BLOCKS),
                    "SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD": "1",
                    "SPACEMIT_EP_INTRA_THREAD_AFFINITY": os.environ.get("PILL_SPACEMIT_AFFINITY", "2"),
                },
                {},
            ],
        )
        input_meta = session.get_inputs()[0]
        input_name = input_meta.name
        input_size = int(input_meta.shape[2]) if len(input_meta.shape) >= 4 and isinstance(input_meta.shape[2], int) else MODEL_INPUT_SIZE
        batch = int(input_meta.shape[0]) if isinstance(input_meta.shape[0], int) and input_meta.shape[0] > 0 else 1

        # Match the handoff configuration exactly.
        class DecodeArgs:
            conf = 0.15
            sachet_conf = 0.75
            iou = 0.45
            min_area = 20.0
            max_area = 180000.0
            pill_max_area_ratio = 0.0285714286
            sachet_min_area = 8000.0
            sachet_min_width = 80.0
            sachet_min_height = 45.0

        session.run(None, {input_name: np.zeros((batch, 3, input_size, input_size), dtype=np.float32)})
        try:
            os.sched_setaffinity(0, {2})
        except (AttributeError, OSError):
            pass
        connection.send({
            "type": "ready",
            "providers": session.get_providers(),
            "model": Path(model_path).name,
            "inputSize": input_size,
            "tcmBlocks": TCM_BLOCKS,
        })

        while True:
            request = connection.recv()
            if request.get("type") == "stop":
                return
            if request.get("type") != "recognize":
                continue

            _pin_process_threads(2)
            frame = request["frame"]  # RGB at the shared camera source resolution
            decode_args = DecodeArgs()
            use_scaled_thresholds = os.environ.get("YOLO_REFERENCE_SCALED_THRESHOLDS", "1").strip().lower() not in {"0", "false", "no"}
            if use_scaled_thresholds:
                decode_thresholds = scaled_decode_thresholds(frame.shape[1], frame.shape[0])
                for threshold_name, threshold_value in decode_thresholds.items():
                    setattr(decode_args, threshold_name, threshold_value)
            else:
                decode_thresholds = {
                    name: float(getattr(decode_args, name))
                    for name in ("min_area", "max_area", "pill_max_area_ratio", "sachet_min_area", "sachet_min_width", "sachet_min_height")
                }
            tensor, scale, pad_x, pad_y = module.letterbox_rgb(frame, input_size)
            output = session.run(None, {input_name: tensor})
            detections = module.postprocess(
                output,
                scale,
                pad_x,
                pad_y,
                frame.shape[1],
                frame.shape[0],
                decode_args,
            )
            items = []
            for box, score, class_id in detections:
                box_values = [float(value) for value in box]
                items.append({
                    "classId": int(class_id),
                    "className": module.CLASS_NAMES[int(class_id)] if 0 <= int(class_id) < len(module.CLASS_NAMES) else str(class_id),
                    "score": float(score),
                    "box": box_values,
                    "area": float(max(0.0, box_values[2] - box_values[0]) * max(0.0, box_values[3] - box_values[1])),
                })
            pill_count = sum(item["classId"] == 0 for item in items)
            sachet_count = sum(item["classId"] == 1 for item in items)
            connection.send({
                "type": "result",
                "items": items,
                "pillCount": int(pill_count),
                "sachetCount": int(sachet_count),
                "totalCount": int(pill_count + sachet_count),
                "sourceCaptureSize": [int(frame.shape[1]), int(frame.shape[0])],
                "decodeThresholds": decode_thresholds,
            })
    except Exception as exc:
        try:
            connection.send({"type": "error", "error": type(exc).__name__, "detail": str(exc)[:160]})
        except Exception:
            pass
    finally:
        connection.close()


class PillCountV2Service:
    def __init__(self, capture_service=None) -> None:
        self._capture_service = capture_service
        self._lock = threading.RLock()
        self._inference_lock = threading.Lock()
        self._worker_lock = threading.RLock()
        self._process = None
        self._connection = None
        self._providers = []
        self._sessions: dict[str, dict] = {}
        self._boot_session_id = "__boot__"
        self._boot_state = "not_started"
        self._boot_error = ""

    def _capture_size(self) -> list[int]:
        width = int(getattr(self._capture_service, "width", CAPTURE_WIDTH))
        height = int(getattr(self._capture_service, "height", CAPTURE_HEIGHT))
        return [width, height]

    def _stop_worker_locked(self) -> None:
        connection, process = self._connection, self._process
        self._connection = self._process = None
        if connection is not None:
            try:
                connection.send({"type": "stop"})
            except Exception:
                pass
            connection.close()
        if process is not None and process.is_alive():
            process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
        self._providers = []

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._process is not None and self._process.is_alive() and self._connection is not None:
                return
            missing = [str(path) for path in (DETECTOR_SCRIPT, DETECTOR_MODEL) if not path.is_file()]
            if missing:
                raise FileNotFoundError(f"missing pill-count resource: {', '.join(missing)}")
            self._stop_worker_locked()
            context = mp.get_context("spawn")
            parent, child = context.Pipe()
            process = context.Process(target=_worker, args=(str(DETECTOR_MODEL), child), daemon=True)
            process.start()
            child.close()
            if not parent.poll(WORKER_START_TIMEOUT_SECONDS):
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                parent.close()
                raise TimeoutError("pill_worker_start_timeout")
            message = parent.recv()
            if message.get("type") != "ready":
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=2)
                parent.close()
                error_name = str(message.get("error") or "pill_worker_start_failed")
                detail = str(message.get("detail") or "").replace("\n", " ")[:180]
                raise RuntimeError(f"{error_name}: {detail}" if detail else error_name)
            self._process, self._connection = process, parent
            self._providers = message.get("providers") or []

    def _touch(self, item: dict) -> None:
        item["last"] = time.monotonic()

    def _reap(self) -> None:
        now = time.monotonic()
        expired = [
            key for key, value in self._sessions.items()
            if key != self._boot_session_id and now - value.get("last", now) >= SESSION_LEASE_SECONDS
        ]
        for key in expired:
            self._sessions.pop(key, None)

    def start_boot_preload(self) -> None:
        threading.Thread(target=self.boot_preload, name="pill-count-boot-preload", daemon=True).start()

    def boot_preload(self):
        with self._lock:
            if self._boot_state in {"loading", "ready"}:
                return self.status("")
            self._boot_state = "loading"
            self._boot_error = ""
        status, data, err = self.preload({"sessionId": self._boot_session_id, "page": "boot"})
        with self._lock:
            if status == 200:
                self._boot_state = "ready"
            else:
                self._boot_state = "error"
                self._boot_error = str((err or {}).get("message") or "model_unavailable")
        return status, data, err

    def preload(self, payload: dict):
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            return 400, None, {"code": "invalid_request", "message": "sessionId 必填", "retryable": False}
        with self._lock:
            self._reap()
            if session_id in self._sessions:
                self._touch(self._sessions[session_id])
                return 200, self._ready(session_id), None
            try:
                self._ensure_worker()
            except Exception as exc:
                detail = str(exc).replace("\n", " ")[:220]
                return 503, None, {
                    "code": "model_unavailable",
                    "stage": "pill_worker_start",
                    "message": f"药丸识别模型加载失败: {type(exc).__name__}{(': ' + detail) if detail else ''}",
                    "retryable": True,
                }
            self._sessions[session_id] = {"last": time.monotonic()}
            return 200, self._ready(session_id), None

    def _ready(self, session_id: str) -> dict:
        return {
            "ready": True,
            "module": "pill-count",
            "sessionId": session_id,
            "provider": self._providers,
            "modelVersion": DETECTOR_MODEL.name,
            "captureSize": self._capture_size(),
            "inputSize": MODEL_INPUT_SIZE,
            "tcmBlocks": TCM_BLOCKS,
            "cpuAffinity": [2],
            "quantization": "INT8 static QDQ",
            "thresholds": {"pillConf": 0.15, "sachetConf": 0.75},
            "leaseExpiresInSeconds": SESSION_LEASE_SECONDS,
        }

    def release(self, payload: dict):
        session_id = str(payload.get("sessionId") or "").strip()
        if session_id == self._boot_session_id:
            return 200, {"released": False, "alreadyReleased": False, "sessionId": session_id, "reason": "boot_session_reserved"}, None
        if not session_id:
            return 400, None, {"code": "invalid_request", "message": "sessionId 必填", "retryable": False}
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        return 200, {"released": True, "alreadyReleased": not existed, "sessionId": session_id}, None

    def status(self, session_id: str = ""):
        with self._lock:
            self._reap()
            item = self._sessions.get(session_id) if session_id else None
            worker_resident = bool(self._process is not None and self._process.is_alive())
            return 200, {
                "module": "pill-count",
                "ready": bool(item) if session_id else worker_resident,
                "busy": self._inference_lock.locked(),
                "sessionId": session_id or None,
                "requestedSessionReady": bool(item),
                "modelResident": worker_resident,
                "cpuAffinity": [2],
                "modelVersion": DETECTOR_MODEL.name,
                "bootState": self._boot_state,
                "bootError": self._boot_error,
                "camera": self._capture_service.status() if self._capture_service is not None else {"ready": False, "state": "unconfigured"},
            }, None

    def _capture_frame(self):
        if self._capture_service is None:
            raise RuntimeError("ov5647_capture_service_unconfigured")
        return self._capture_service.capture(timeout_seconds=20.0, require_next_frame=True)

    def recognize(self, payload: dict):
        session_id = str(payload.get("sessionId") or "").strip()
        with self._lock:
            self._reap()
            session = self._sessions.get(session_id)
            if session:
                self._touch(session)
        if not session_id:
            return 400, None, {"code": "invalid_request", "message": "sessionId 必填", "retryable": False}
        if not session:
            return 503, None, {"code": "module_not_ready", "message": "药丸识别尚未预加载完成", "retryable": True}
        if not self._inference_lock.acquire(timeout=INFERENCE_TIMEOUT_SECONDS):
            return 504, None, {"code": "recognition_timeout", "message": "药丸识别排队超时，推理权未获取，模型保持常驻", "retryable": True}
        try:
            started = time.perf_counter()
            import cv2
            capture = self._capture_frame()
            bgr_frame = capture.frame
            frame = cv2.cvtColor(bgr_frame, cv2.COLOR_BGR2RGB)
            cv2.imwrite(FRAME_PATH, bgr_frame)
            self._ensure_worker()
            with self._worker_lock:
                self._connection.send({"type": "recognize", "frame": frame})
                if not self._connection.poll(INFERENCE_TIMEOUT_SECONDS):
                    self._stop_worker_locked()
                    raise TimeoutError()
                result = self._connection.recv()
            if result.get("type") != "result":
                detail = result.get("detail") or result.get("error") or "pill_worker_failed"
                raise RuntimeError(detail)
            total = int(result.get("totalCount", 0))
            return 200, {
                "count": total,
                "detectedCount": total,
                "pillCount": int(result.get("pillCount", 0)),
                "sachetCount": int(result.get("sachetCount", 0)),
                "totalCount": total,
                "items": result.get("items", []),
                "decodeThresholds": result.get("decodeThresholds", {}),
                "sourceCaptureSize": result.get("sourceCaptureSize", [int(bgr_frame.shape[1]), int(bgr_frame.shape[0])]),
                "source": "ov5647-yolo-latest",
                "modelVersion": DETECTOR_MODEL.name,
                "provider": self._providers,
                "tcmBlocks": TCM_BLOCKS,
                "captureSize": self._capture_size(),
                "inputSize": MODEL_INPUT_SIZE,
                "framePath": FRAME_PATH,
                "frameId": capture.frameId,
                "pipelineGeneration": capture.pipelineGeneration,
                "durationMs": round((time.perf_counter() - started) * 1000, 1),
            }, None
        except TimeoutError:
            return 504, None, {"code": "recognition_timeout", "stage": "pill_inference",
                               "message": "药丸识别单次任务超时；失控 worker 已终止，推理权已安全释放，下次 preload 将重建模型",
                               "retryable": True, "modelResident": False}
        except Exception as exc:
            detail = str(exc).replace("\n", " ")[:180]
            return 503, None, {"code": getattr(exc, "code", "pill_model_unavailable"),
                               "stage": getattr(exc, "stage", "pill_inference"),
                               "message": f"药丸识别运行失败: {type(exc).__name__}{(': ' + detail) if detail else ''}",
                               "retryable": getattr(exc, "retryable", True)}
        finally:
            self._inference_lock.release()

    def close(self) -> None:
        with self._worker_lock:
            self._stop_worker_locked()
