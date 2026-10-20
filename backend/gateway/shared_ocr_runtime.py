#!/usr/bin/env python3
"""Shared, killable OCR worker for medicine-box and prescription modes."""

from __future__ import annotations

import json
import math
import multiprocessing as mp
import threading
from difflib import SequenceMatcher
from numbers import Integral
from pathlib import Path


MODE_MEDICINE_BOX = "medicine_box"
MODE_PRESCRIPTION = "prescription"
SUPPORTED_MODES = frozenset({MODE_MEDICINE_BOX, MODE_PRESCRIPTION})

# Cold boot may need to compile both static INT8 graphs for SpaceMIT.  Keep
# the shared worker alive for two minutes rather than treating a slow first
# boot as a failed resident preload.
INFERENCE_TIMEOUT_SECONDS = 120
PRESCRIPTION_INFERENCE_TIMEOUT_SECONDS = 120
WORKER_START_TIMEOUT_SECONDS = 120
PRESCRIPTION_PRELOAD_TIMEOUT_SECONDS = 120

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PRESCRIPTION_DET_MODEL = (
    BACKEND_ROOT
    / "medicine_box_recognition/models/ppocr_v5_static_int8_20260718/validation_20260801/"
    "ppocr_v5_mobile_det_int8_audited_portrait_1x3x960x544.onnx"
)
DEFAULT_PRESCRIPTION_REC_MODEL = (
    BACKEND_ROOT
    / "medicine_prescription_ocr/models/ppocrv5_mobile_rec_prescription_int8_1x3x48x320.onnx"
)
PRESCRIPTION_DET_HEIGHT = 960
PRESCRIPTION_DET_WIDTH = 544
PRESCRIPTION_REC_HEIGHT = 48
PRESCRIPTION_REC_WIDTH = 320


class OcrModeUnavailable(RuntimeError):
    def __init__(self, mode: str, code: str, message: str):
        super().__init__(message)
        self.mode = mode
        self.code = code
        self.retryable = True


def _normalize(text: str) -> str:
    return "".join(
        str(text or "")
        .lower()
        .replace("銀", "银")
        .replace("Ⅲ", "III")
        .replace("（", "")
        .replace("）", "")
        .replace("(", "")
        .replace(")", "")
        .split()
    )


def _pin_process_threads(os_module, cpu: int) -> None:
    try:
        task_root = Path("/proc/self/task")
        tids = [int(item.name) for item in task_root.iterdir() if item.name.isdigit()]
        for tid in tids or [0]:
            os_module.sched_setaffinity(tid, {cpu})
    except (AttributeError, OSError):
        pass


def _resolved_shape(input_meta, expected_shape: tuple[int, ...]) -> list[int]:
    """Return a concrete static shape, rejecting metadata that needs inference."""
    shape = list(getattr(input_meta, "shape", ()) or ())
    if len(shape) != len(expected_shape):
        raise RuntimeError(
            f"static_shape_required:{shape};expected_rank={len(expected_shape)}"
        )
    if any(
        isinstance(value, bool) or not isinstance(value, Integral) or value <= 0
        for value in shape
    ):
        raise RuntimeError(f"static_shape_required:{shape}")
    return [int(value) for value in shape]


def _provider_name(providers: dict[str, list[str]] | None) -> str:
    values = [provider for items in (providers or {}).values() for provider in items]
    has_spacemit = any("SpaceMITExecutionProvider" in provider for provider in values)
    has_cpu = any("CPUExecutionProvider" in provider for provider in values)
    if has_spacemit and has_cpu:
        return "SpaceMITExecutionProvider+CPUExecutionProvider"
    if has_spacemit:
        return "SpaceMITExecutionProvider"
    return "CPUExecutionProvider"


def _sort_source_records(records: list[dict]) -> list[dict]:
    records = sorted(records, key=lambda item: (item["top"], item["left"]))
    for index in range(1, len(records)):
        current = index
        while current > 0:
            left_item = records[current - 1]
            right_item = records[current]
            same_row = abs(right_item["top"] - left_item["top"]) <= max(
                10.0,
                min(left_item["height"], right_item["height"]) * 0.5,
            )
            if not same_row or right_item["left"] >= left_item["left"]:
                break
            records[current - 1], records[current] = records[current], records[current - 1]
            current -= 1
    return records


def _rotated_ccw_to_camera_points(points, *, camera_width: int, camera_height: int, np_module):
    """Map points from a 90-degree CCW image back to the camera frame."""
    rotated = np_module.asarray(points, dtype=np_module.float32)
    camera = np_module.empty_like(rotated)
    camera[..., 0] = camera_width - 1 - rotated[..., 1]
    camera[..., 1] = rotated[..., 0]
    camera[..., 0] = np_module.clip(camera[..., 0], 0, camera_width - 1)
    camera[..., 1] = np_module.clip(camera[..., 1], 0, camera_height - 1)
    return camera


class _PrescriptionEngine:
    """Static-shape prescription Det/Rec sessions owned inside the OCR worker."""

    def __init__(
        self,
        medicine_engine,
        det_path: Path,
        rec_path: Path,
        *,
        ort_module,
        np_module,
        cv2_module,
        official_module,
        letterbox_image,
        strict_providers,
    ) -> None:
        self._np = np_module
        self._cv2 = cv2_module
        self._official = official_module
        self._letterbox_image = letterbox_image
        self.chars = list(medicine_engine.chars)
        self.det_thresh = float(medicine_engine.det_thresh)
        self.box_thresh = float(medicine_engine.box_thresh)
        self.unclip_ratio = float(medicine_engine.unclip_ratio)

        options = ort_module.SessionOptions()
        options.graph_optimization_level = ort_module.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self.det = ort_module.InferenceSession(
            str(det_path), sess_options=options, providers=strict_providers(False)
        )
        self.rec = ort_module.InferenceSession(
            str(rec_path), sess_options=options, providers=strict_providers(True)
        )
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name

        det_expected_shape = [1, 3, PRESCRIPTION_DET_HEIGHT, PRESCRIPTION_DET_WIDTH]
        rec_expected_shape = [1, 3, PRESCRIPTION_REC_HEIGHT, PRESCRIPTION_REC_WIDTH]
        det_shape = _resolved_shape(self.det.get_inputs()[0], tuple(det_expected_shape))
        rec_shape = _resolved_shape(self.rec.get_inputs()[0], tuple(rec_expected_shape))
        if det_shape != det_expected_shape:
            raise RuntimeError(f"prescription_det_shape_mismatch:{det_shape}")
        if rec_shape != rec_expected_shape:
            raise RuntimeError(f"prescription_rec_shape_mismatch:{rec_shape}")
        output_classes = self.rec.get_outputs()[0].shape[-1]
        if isinstance(output_classes, int) and output_classes != len(self.chars):
            raise RuntimeError(
                f"prescription_rec_dictionary_mismatch:{output_classes}!={len(self.chars)}"
            )

        self.det.run(None, {self.det_input: self._np.zeros(det_shape, dtype=self._np.float32)})
        self.rec.run(None, {self.rec_input: self._np.zeros(rec_shape, dtype=self._np.float32)})

    def providers(self) -> dict[str, list[str]]:
        return {"det": self.det.get_providers(), "rec": self.rec.get_providers()}

    def _det_preprocess(self, frame):
        canvas, transform = self._letterbox_image(
            frame,
            PRESCRIPTION_DET_WIDTH,
            PRESCRIPTION_DET_HEIGHT,
        )
        data = canvas.astype(self._np.float32) / 255.0
        data = (data - self._np.array([0.485, 0.456, 0.406], dtype=self._np.float32)) / self._np.array(
            [0.229, 0.224, 0.225], dtype=self._np.float32
        )
        return data.transpose(2, 0, 1)[None].astype(self._np.float32), transform

    @staticmethod
    def _rec_preprocess(cv2_module, np_module, image):
        height, width = PRESCRIPTION_REC_HEIGHT, PRESCRIPTION_REC_WIDTH
        source_height, source_width = image.shape[:2]
        resized_width = min(
            width,
            max(1, int(math.ceil(height * source_width / float(max(1, source_height))))),
        )
        resized = cv2_module.resize(image, (resized_width, height)).astype(np_module.float32)
        resized = resized.transpose(2, 0, 1) / 255.0
        resized = (resized - 0.5) / 0.5
        padded = np_module.zeros((3, height, width), dtype=np_module.float32)
        padded[:, :, :resized_width] = resized
        return padded[None]

    def _decode(self, prediction) -> tuple[str, float]:
        indices = prediction.argmax(axis=1)
        probabilities = prediction.max(axis=1)
        text: list[str] = []
        confidence: list[float] = []
        previous = -1
        for raw_index, probability in zip(indices, probabilities):
            index = int(raw_index)
            if index != 0 and index != previous and index < len(self.chars):
                text.append(self.chars[index])
                confidence.append(float(probability))
            previous = index
        return "".join(text), float(self._np.mean(confidence)) if confidence else 0.0

    def recognize(self, frame) -> list[dict]:
        camera_height, camera_width = frame.shape[:2]
        rotated_frame = self._cv2.rotate(frame, self._cv2.ROTATE_90_COUNTERCLOCKWISE)
        tensor, transform = self._det_preprocess(rotated_frame)
        prediction = self._np.asarray(self.det.run(None, {self.det_input: tensor})[0])
        while prediction.ndim > 2 and prediction.shape[0] == 1:
            prediction = prediction[0]
        if prediction.ndim == 3:
            prediction = prediction[0]
        if prediction.ndim != 2:
            raise RuntimeError(f"prescription_det_output_shape:{list(prediction.shape)}")

        bitmap = (prediction > self.det_thresh).astype(self._np.uint8) * 255
        contours_result = self._cv2.findContours(
            bitmap, self._cv2.RETR_LIST, self._cv2.CHAIN_APPROX_SIMPLE
        )
        contours = contours_result[-2]
        records: list[dict] = []
        for contour in contours[:1000]:
            box, side = self._official.get_mini_boxes(contour)
            if side < 3:
                continue
            score = self._official.box_score_fast(prediction, box)
            if score < self.box_thresh:
                continue
            expanded = self._official.unclip(box, self.unclip_ratio)
            if expanded is None:
                continue
            box, side = self._official.get_mini_boxes(expanded)
            if side < 5:
                continue
            rotated_box = transform.inverse_points(box)
            left = float(rotated_box[:, 0].min())
            top = float(rotated_box[:, 1].min())
            right = float(rotated_box[:, 0].max())
            bottom = float(rotated_box[:, 1].max())
            if right - left < 2 or bottom - top < 2:
                continue
            records.append(
                {
                    "rotatedBox": rotated_box,
                    "detConfidence": float(score),
                    "left": left,
                    "top": top,
                    "height": bottom - top,
                    "bbox": [left, top, right, bottom],
                }
            )

        lines: list[dict] = []
        for record in _sort_source_records(records):
            crop = self._official.get_rotate_crop_image(
                rotated_frame,
                record["rotatedBox"],
            )
            if crop.size == 0:
                continue
            rec_tensor = self._rec_preprocess(self._cv2, self._np, crop)
            output = self.rec.run(None, {self.rec_input: rec_tensor})[0][0]
            text, confidence = self._decode(output)
            if not text.strip():
                continue
            camera_box = _rotated_ccw_to_camera_points(
                record["rotatedBox"],
                camera_width=camera_width,
                camera_height=camera_height,
                np_module=self._np,
            )
            camera_bbox = [
                float(camera_box[:, 0].min()),
                float(camera_box[:, 1].min()),
                float(camera_box[:, 0].max()),
                float(camera_box[:, 1].max()),
            ]
            lines.append(
                {
                    "index": len(lines),
                    "text": text,
                    "confidence": round(confidence, 4),
                    "detConfidence": round(record["detConfidence"], 4),
                    "box": [int(round(value)) for value in camera_bbox],
                    "polygon": self._np.round(camera_box, 1).tolist(),
                    "rotatedBox": [int(round(value)) for value in record["bbox"]],
                    "rotatedPolygon": self._np.round(record["rotatedBox"], 1).tolist(),
                }
            )
        return lines


def _ocr_worker(
    root_text: str,
    medicine_model_root_text: str,
    db_path_text: str,
    prescription_det_path_text: str,
    prescription_rec_path_text: str,
    connection,
) -> None:
    """Own all OCR provider sessions so a hung call has one kill boundary."""
    try:
        import os
        import sys

        try:
            os.sched_setaffinity(0, {1})
        except (AttributeError, OSError):
            pass
        import cv2
        import numpy as np
        import onnxruntime as ort

        root = Path(root_text)
        gateway_root = Path(__file__).resolve().parent
        sys.path.insert(0, str(gateway_root))
        sys.path.insert(0, str(root / "scripts"))
        os.environ.pop("PPOCR_CPU_ONLY", None)
        os.environ.setdefault("OCR_SPACEMIT_AFFINITY", "1")
        from bench_v5_int8_k1_mixed_opt import Int8V5
        import k1_live_yolo_v5ocr_preview_640 as base
        from frame_geometry import letterbox_image

        data = json.loads(Path(db_path_text).read_text(encoding="utf-8"))
        raw_names = data.get("medicine_names", []) if isinstance(data, dict) else data
        names = [str(name) for name in raw_names if str(name).strip()]
        if len(names) < 17:
            raise RuntimeError(f"medicine database incomplete: {len(names)}")

        medicine_engine = Int8V5(
            Path(medicine_model_root_text), require_spacemit=True, cpu_only=False
        )
        det_shape = _resolved_shape(medicine_engine.det.get_inputs()[0], (1, 3, 352, 640))
        rec_shape = _resolved_shape(medicine_engine.rec.get_inputs()[0], (1, 3, 48, 320))
        medicine_engine.det.run(
            None, {medicine_engine.det_input: np.zeros(det_shape, dtype=np.float32)}
        )
        medicine_engine.rec.run(
            None, {medicine_engine.rec_input: np.zeros(rec_shape, dtype=np.float32)}
        )
        _pin_process_threads(os, 1)

        prescription_det_path = Path(prescription_det_path_text)
        prescription_rec_path = Path(prescription_rec_path_text)
        missing_models = [
            str(path)
            for path in (prescription_det_path, prescription_rec_path)
            if not path.is_file()
        ]
        medicine_providers = {
            "det": medicine_engine.det.get_providers(),
            "rec": medicine_engine.rec.get_providers(),
        }
        connection.send(
            {
                "type": "ready",
                "modes": {
                    MODE_MEDICINE_BOX: {
                        "configured": True,
                        "modelResident": True,
                        "providers": medicine_providers,
                        "databaseSize": len(names),
                    },
                    MODE_PRESCRIPTION: {
                        "configured": not missing_models,
                        "modelResident": False,
                        "missingModels": missing_models,
                    },
                },
            }
        )

        prescription_engine = None
        while True:
            request = connection.recv()
            request_type = request.get("type")
            mode = request.get("mode")
            if request_type == "stop":
                return
            if mode not in SUPPORTED_MODES:
                connection.send(
                    {
                        "type": "error",
                        "mode": mode,
                        "error": "unsupported_ocr_mode",
                        "detail": str(mode),
                    }
                )
                continue

            if request_type == "preload":
                if mode == MODE_MEDICINE_BOX:
                    connection.send(
                        {
                            "type": "mode_ready",
                            "mode": mode,
                            "providers": medicine_providers,
                            "databaseSize": len(names),
                        }
                    )
                    continue
                if prescription_engine is not None:
                    connection.send(
                        {
                            "type": "mode_ready",
                            "mode": mode,
                            "providers": prescription_engine.providers(),
                        }
                    )
                    continue
                missing_models = [
                    str(path)
                    for path in (prescription_det_path, prescription_rec_path)
                    if not path.is_file()
                ]
                if missing_models:
                    connection.send(
                        {
                            "type": "mode_error",
                            "mode": mode,
                            "code": "prescription_model_missing",
                            "detail": "; ".join(missing_models),
                            "missingModels": missing_models,
                        }
                    )
                    continue
                try:
                    from bench_v5_int8_k1_mixed_opt import strict_providers
                    import eval_ppocr_v5_mobile_fp32_official as official

                    prescription_engine = _PrescriptionEngine(
                        medicine_engine,
                        prescription_det_path,
                        prescription_rec_path,
                        ort_module=ort,
                        np_module=np,
                        cv2_module=cv2,
                        official_module=official,
                        letterbox_image=letterbox_image,
                        strict_providers=strict_providers,
                    )
                    _pin_process_threads(os, 1)
                    connection.send(
                        {
                            "type": "mode_ready",
                            "mode": mode,
                            "providers": prescription_engine.providers(),
                        }
                    )
                except Exception as exc:
                    prescription_engine = None
                    connection.send(
                        {
                            "type": "mode_error",
                            "mode": mode,
                            "code": "prescription_model_unavailable",
                            "error": type(exc).__name__,
                            "detail": str(exc)[:300],
                        }
                    )
                continue

            if request_type != "recognize":
                continue
            try:
                _pin_process_threads(os, 1)
                frame = request["frame"]
                if mode == MODE_MEDICINE_BOX:
                    use_source_recrop = os.environ.get(
                        "OCR_SOURCE_RESOLUTION_RECROP", "1"
                    ).strip().lower() not in {"0", "false", "no"}
                    if use_source_recrop:
                        crop, det_transform = letterbox_image(frame, 640, 352)
                    else:
                        crop, det_transform = base.det_ready_roi(frame), None
                    detections = sorted(
                        medicine_engine.detect(crop),
                        key=lambda item: base.text_box_height(item["box"]),
                        reverse=True,
                    )[:3]
                    candidates = []
                    for detection in detections:
                        source_box = (
                            det_transform.inverse_points(detection["box"])
                            if det_transform is not None
                            else detection["box"]
                        )
                        word = base.upright_text_crop(
                            frame if det_transform is not None else crop,
                            source_box,
                        )
                        if word.size == 0:
                            continue
                        output = medicine_engine.rec.run(
                            None,
                            {medicine_engine.rec_input: medicine_engine.rec_preprocess(word)},
                        )[0][0]
                        raw_text, ocr_confidence = medicine_engine.decode(output)
                        normalized = _normalize(raw_text)
                        best_name, best_score = "", 0.0
                        for name in names:
                            score = SequenceMatcher(
                                None, normalized, _normalize(name)
                            ).ratio()
                            if _normalize(name) in normalized:
                                score = 1.0
                            if score > best_score:
                                best_name, best_score = name, score
                        candidates.append(
                            {
                                "name": best_name if best_score >= 0.70 else "",
                                "rawText": raw_text,
                                "confidence": round(best_score, 4),
                                "ocrConfidenceDebug": round(float(ocr_confidence), 4),
                            }
                        )
                        if best_score >= 0.70:
                            break
                    connection.send(
                        {
                            "type": "result",
                            "mode": mode,
                            "candidates": candidates,
                        }
                    )
                else:
                    if prescription_engine is None:
                        connection.send(
                            {
                                "type": "error",
                                "mode": mode,
                                "error": "prescription_mode_not_preloaded",
                                "detail": "prescription mode must be preloaded",
                            }
                        )
                        continue
                    connection.send(
                        {
                            "type": "result",
                            "mode": mode,
                            "lines": prescription_engine.recognize(frame),
                        }
                    )
            except Exception as exc:
                connection.send(
                    {
                        "type": "error",
                        "mode": mode,
                        "error": type(exc).__name__,
                        "detail": str(exc)[:300],
                    }
                )
    except Exception as exc:
        try:
            connection.send(
                {"type": "error", "error": type(exc).__name__, "detail": str(exc)[:300]}
            )
        except Exception:
            pass
    finally:
        connection.close()


class SharedOcrRuntime:
    """Parent-side owner of the single OCR process, Pipe, and inference lock."""

    def __init__(
        self,
        project_root: str | Path,
        *,
        medicine_model_root: str | Path | None = None,
        medicine_database_path: str | Path | None = None,
        prescription_det_path: str | Path = DEFAULT_PRESCRIPTION_DET_MODEL,
        prescription_rec_path: str | Path = DEFAULT_PRESCRIPTION_REC_MODEL,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.medicine_model_root = Path(
            medicine_model_root
            or self.root / "models" / "ppocr_v5_mobile_xquant_int8_k1_mixed_20260717"
        )
        self.medicine_database_path = Path(
            medicine_database_path
            or self.root / "models" / "medicine_name_database_17.json"
        )
        self.prescription_det_path = Path(prescription_det_path)
        self.prescription_rec_path = Path(prescription_rec_path)
        self._inference_lock = threading.Lock()
        self._worker_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._worker_process = None
        self._worker_connection = None
        self._modes = {
            MODE_MEDICINE_BOX: self._new_mode_state(configured=True),
            MODE_PRESCRIPTION: self._new_mode_state(configured=False),
        }
        self._database_size = 0
        self._refresh_prescription_configuration_locked()

    @staticmethod
    def _new_mode_state(*, configured: bool) -> dict:
        return {
            "configured": configured,
            "modelResident": False,
            "providers": {"det": [], "rec": []},
            "lastError": None,
            "missingModels": [],
        }

    def _refresh_prescription_configuration_locked(self) -> None:
        missing = [
            str(path)
            for path in (self.prescription_det_path, self.prescription_rec_path)
            if not path.is_file()
        ]
        with self._state_lock:
            state = self._modes[MODE_PRESCRIPTION]
            state["configured"] = not missing
            state["missingModels"] = missing
            if missing and not state["modelResident"]:
                state["lastError"] = {
                    "code": "prescription_model_missing",
                    "message": "; ".join(missing),
                }
            elif not missing and (state.get("lastError") or {}).get("code") == "prescription_model_missing":
                state["lastError"] = None

    def _stop_worker_locked(self) -> None:
        with self._state_lock:
            connection, process = self._worker_connection, self._worker_process
            self._worker_connection = None
            self._worker_process = None
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
        with self._state_lock:
            for state in self._modes.values():
                state["modelResident"] = False
                state["providers"] = {"det": [], "rec": []}
        self._refresh_prescription_configuration_locked()

    def _ensure_worker_locked(self) -> None:
        with self._state_lock:
            process_snapshot = self._worker_process
            connection_snapshot = self._worker_connection
        if (
            process_snapshot is not None
            and process_snapshot.is_alive()
            and connection_snapshot is not None
        ):
            return
        self._stop_worker_locked()
        context = mp.get_context("spawn")
        parent, child = context.Pipe()
        process = context.Process(
            target=_ocr_worker,
            args=(
                str(self.root),
                str(self.medicine_model_root),
                str(self.medicine_database_path),
                str(self.prescription_det_path),
                str(self.prescription_rec_path),
                child,
            ),
            name="shared-ocr-worker",
            daemon=True,
        )
        process.start()
        child.close()
        if not parent.poll(WORKER_START_TIMEOUT_SECONDS):
            process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            parent.close()
            raise TimeoutError("ocr_worker_start_timeout")
        message = parent.recv()
        if message.get("type") != "ready":
            process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=2)
            parent.close()
            raise RuntimeError(
                f"ocr_worker_start_failed:{message.get('detail') or message.get('error', 'unknown')}"
            )
        with self._state_lock:
            self._worker_process = process
            self._worker_connection = parent
            for mode, details in (message.get("modes") or {}).items():
                if mode not in self._modes:
                    continue
                self._modes[mode].update(details or {})
                self._modes[mode]["lastError"] = None
            medicine = (message.get("modes") or {}).get(MODE_MEDICINE_BOX) or {}
            self._database_size = int(medicine.get("databaseSize") or 0)

    def _ensure_prescription_mode_locked(self) -> None:
        self._refresh_prescription_configuration_locked()
        with self._state_lock:
            model_resident = self._modes[MODE_PRESCRIPTION]["modelResident"]
            configured = self._modes[MODE_PRESCRIPTION]["configured"]
            connection = self._worker_connection
        if model_resident:
            return
        if not configured:
            raise OcrModeUnavailable(
                MODE_PRESCRIPTION,
                "prescription_model_missing",
                "处方 OCR 模型文件尚未部署",
            )
        if connection is None:
            raise RuntimeError("ocr_worker_unavailable")
        connection.send({"type": "preload", "mode": MODE_PRESCRIPTION})
        if not connection.poll(PRESCRIPTION_PRELOAD_TIMEOUT_SECONDS):
            self._stop_worker_locked()
            raise TimeoutError("prescription_ocr_worker_start_timeout")
        message = connection.recv()
        if message.get("type") == "mode_error":
            with self._state_lock:
                state = self._modes[MODE_PRESCRIPTION]
                state["modelResident"] = False
                state["lastError"] = {
                    "code": str(message.get("code") or "prescription_model_unavailable"),
                    "message": str(message.get("detail") or "处方 OCR 模型不可用"),
                }
                state["missingModels"] = list(message.get("missingModels") or [])
                last_error = dict(state["lastError"])
            raise OcrModeUnavailable(
                MODE_PRESCRIPTION,
                last_error["code"],
                last_error["message"],
            )
        if message.get("type") != "mode_ready" or message.get("mode") != MODE_PRESCRIPTION:
            detail = message.get("detail") or message.get("error") or "unknown"
            self._stop_worker_locked()
            raise RuntimeError(f"prescription_ocr_worker_start_failed:{detail}")
        with self._state_lock:
            state = self._modes[MODE_PRESCRIPTION]
            state["modelResident"] = True
            state["providers"] = message.get("providers") or {"det": [], "rec": []}
            state["lastError"] = None
            state["missingModels"] = []

    def preload_mode(self, mode: str) -> dict:
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported OCR mode: {mode}")
        timeout = (
            PRESCRIPTION_PRELOAD_TIMEOUT_SECONDS
            if mode == MODE_PRESCRIPTION
            else WORKER_START_TIMEOUT_SECONDS
        )
        if not self._inference_lock.acquire(timeout=timeout):
            raise TimeoutError(f"{mode}_preload_queue_timeout")
        try:
            with self._worker_lock:
                self._ensure_worker_locked()
                if mode == MODE_PRESCRIPTION:
                    self._ensure_prescription_mode_locked()
                return self.status(mode)
        finally:
            self._inference_lock.release()

    def infer(self, mode: str, frame):
        if mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported OCR mode: {mode}")
        inference_timeout = (
            PRESCRIPTION_INFERENCE_TIMEOUT_SECONDS
            if mode == MODE_PRESCRIPTION
            else INFERENCE_TIMEOUT_SECONDS
        )
        queue_error = (
            "prescription_ocr_inference_queue_timeout"
            if mode == MODE_PRESCRIPTION
            else "ocr_inference_queue_timeout"
        )
        inference_error = (
            "prescription_ocr_inference_timeout"
            if mode == MODE_PRESCRIPTION
            else "ocr_inference_timeout"
        )
        if not self._inference_lock.acquire(timeout=inference_timeout):
            raise TimeoutError(queue_error)
        try:
            with self._worker_lock:
                self._ensure_worker_locked()
                if mode == MODE_PRESCRIPTION:
                    self._ensure_prescription_mode_locked()
                with self._state_lock:
                    connection = self._worker_connection
                if connection is None:
                    raise RuntimeError("ocr_worker_unavailable")
                connection.send({"type": "recognize", "mode": mode, "frame": frame})
                if not connection.poll(inference_timeout):
                    self._stop_worker_locked()
                    raise TimeoutError(inference_error)
                message = connection.recv()
                if message.get("type") != "result" or message.get("mode") != mode:
                    detail = message.get("detail") or message.get("error") or "unknown"
                    self._stop_worker_locked()
                    raise RuntimeError(f"ocr_worker_failed:{detail}")
                return (
                    message.get("candidates", [])
                    if mode == MODE_MEDICINE_BOX
                    else message.get("lines", [])
                )
        finally:
            self._inference_lock.release()

    def status(self, mode: str | None = None) -> dict:
        if mode is not None and mode not in SUPPORTED_MODES:
            raise ValueError(f"unsupported OCR mode: {mode}")
        self._refresh_prescription_configuration_locked()
        with self._state_lock:
            worker_resident = bool(
                self._worker_process is not None and self._worker_process.is_alive()
            )
            if mode is None:
                return {
                    "workerResident": worker_resident,
                    "busy": self._inference_lock.locked(),
                    "databaseSize": self._database_size,
                    "modes": {
                        name: {
                            **details,
                            "provider": _provider_name(details.get("providers")),
                        }
                        for name, details in self._modes.items()
                    },
                }
            details = self._modes[mode]
            return {
                "mode": mode,
                "configured": bool(details["configured"]),
                "modelResident": worker_resident and bool(details["modelResident"]),
                "workerResident": worker_resident,
                "busy": self._inference_lock.locked(),
                "providers": details.get("providers") or {"det": [], "rec": []},
                "provider": _provider_name(details.get("providers")),
                "lastError": details.get("lastError"),
                "missingModels": list(details.get("missingModels") or []),
                "databaseSize": self._database_size,
            }

    def boot_preload(self) -> dict:
        results = {}
        for mode in (MODE_MEDICINE_BOX, MODE_PRESCRIPTION):
            try:
                results[mode] = {"ready": True, "status": self.preload_mode(mode)}
            except Exception as exc:
                results[mode] = {
                    "ready": False,
                    "error": {
                        "code": getattr(exc, "code", type(exc).__name__),
                        "message": str(exc),
                    },
                    "status": self.status(mode),
                }
        return results

    def close(self) -> None:
        with self._worker_lock:
            self._stop_worker_locked()
