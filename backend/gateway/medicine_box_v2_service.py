#!/usr/bin/env python3
"""K1 v2 medicine-box OCR lifecycle adapter."""

from __future__ import annotations

import threading
import time
from pathlib import Path

from shared_ocr_runtime import (
    INFERENCE_TIMEOUT_SECONDS,
    MODE_MEDICINE_BOX,
    WORKER_START_TIMEOUT_SECONDS,
    SharedOcrRuntime,
)


SESSION_LEASE_SECONDS = 120
FRAME_PATH = "/tmp/medicine_box_v2_latest.jpg"


class MedicineBoxV2Service:
    def __init__(
        self,
        project_root: str | Path,
        capture_service=None,
        *,
        ocr_runtime: SharedOcrRuntime | None = None,
    ) -> None:
        self.root = Path(project_root).resolve()
        self.model_root = self.root / "models" / "ppocr_v5_mobile_xquant_int8_k1_mixed_20260717"
        self.db_path = self.root / "models" / "medicine_name_database_17.json"
        self._lock = threading.RLock()
        self._capture_service = capture_service
        self._owns_runtime = ocr_runtime is None
        self._ocr_runtime = ocr_runtime or SharedOcrRuntime(
            self.root,
            medicine_model_root=self.model_root,
            medicine_database_path=self.db_path,
        )
        self._sessions: dict[str, dict] = {}
        self._engine_version = "ppocr_v5_mobile_int8_640x352_20260718"
        self._boot_session_id = "__boot__"
        self._boot_thread = None
        self._boot_state = "not_started"
        self._boot_error = ""

    def _touch_session(self, item: dict) -> None:
        item["lastActivityMonotonic"] = time.monotonic()
        item["lastActivityAt"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def _lease_remaining_seconds(self, item: dict, now: float | None = None) -> int:
        now = time.monotonic() if now is None else now
        last_activity = float(item.get("lastActivityMonotonic") or now)
        return max(0, int(SESSION_LEASE_SECONDS - (now - last_activity)))

    def _reap_expired_sessions(self) -> None:
        now = time.monotonic()
        expired = [
            session_id
            for session_id, item in self._sessions.items()
            if session_id != self._boot_session_id
            and item.get("ready")
            and self._lease_remaining_seconds(item, now) == 0
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)

    def _active_user_session(self) -> dict | None:
        for session_id, item in self._sessions.items():
            if session_id != self._boot_session_id and item.get("ready"):
                return item
        return None

    def start_boot_preload(self) -> None:
        self._boot_thread = threading.Thread(
            target=self.boot_preload,
            name="medicine-ocr-boot-preload",
            daemon=True,
        )
        self._boot_thread.start()

    def boot_preload(self):
        with self._lock:
            if self._boot_state in {"loading", "ready"}:
                return self.status("")
            self._boot_state = "loading"
            self._boot_error = ""
        status, data, err = self.preload(
            {"sessionId": self._boot_session_id, "page": "boot"}
        )
        with self._lock:
            if status == 200:
                self._boot_state = "ready"
            else:
                self._boot_state = "error"
                self._boot_error = str((err or {}).get("message") or "model_unavailable")
        return status, data, err

    def _ensure_worker(self) -> None:
        self._ocr_runtime.preload_mode(MODE_MEDICINE_BOX)

    def _run_inference(self, frame):
        return self._ocr_runtime.infer(MODE_MEDICINE_BOX, frame)

    def preload(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get("sessionId") or "").strip()
        page = str(payload.get("page") or "smart-medicine-assistant").strip()
        if not session_id:
            return 400, None, {
                "code": "invalid_request",
                "message": "sessionId 必填",
                "retryable": False,
            }
        with self._lock:
            self._reap_expired_sessions()
            current = self._sessions.get(session_id)
            if current and current.get("ready"):
                runtime = self._ocr_runtime.status(MODE_MEDICINE_BOX)
                if runtime["modelResident"]:
                    self._touch_session(current)
                    return 200, self._ready_data(session_id, page, current), None
            try:
                self._ensure_worker()
            except Exception as exc:
                return 503, None, {
                    "code": "model_unavailable",
                    "message": f"药盒 OCR 模型加载失败: {type(exc).__name__}",
                    "retryable": True,
                }
            ready_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._sessions[session_id] = {
                "ready": True,
                "page": page,
                "readyAt": ready_at,
            }
            self._touch_session(self._sessions[session_id])
            return 200, self._ready_data(session_id, page, self._sessions[session_id]), None

    def _ready_data(self, session_id: str, page: str, item: dict) -> dict:
        runtime = self._ocr_runtime.status(MODE_MEDICINE_BOX)
        return {
            "ready": True,
            "module": "medicine-box",
            "sessionId": session_id,
            "page": page,
            "modelVersion": self._engine_version,
            "provider": runtime["provider"],
            "databaseSize": runtime["databaseSize"],
            "readyAt": item.get("readyAt"),
            "leaseExpiresInSeconds": self._lease_remaining_seconds(item),
        }

    def status(self, session_id: str = "") -> tuple[int, dict, None]:
        with self._lock:
            self._reap_expired_sessions()
            item = self._sessions.get(session_id) if session_id else None
            active_session = self._active_user_session()
            runtime = self._ocr_runtime.status(MODE_MEDICINE_BOX)
            ready = bool(item and item.get("ready")) if session_id else runtime["modelResident"]
            return 200, {
                "module": "medicine-box",
                "ready": ready,
                "busy": runtime["busy"],
                "sessionId": session_id or None,
                "modelVersion": self._engine_version,
                "databaseSize": runtime["databaseSize"],
                "provider": runtime["provider"],
                "modelResident": runtime["modelResident"],
                "requestedSessionReady": bool(item and item.get("ready")),
                "bootState": self._boot_state,
                "bootError": self._boot_error,
                "camera": self._capture_service.status()
                if self._capture_service is not None
                else {"ready": False, "state": "unconfigured"},
                "sessionLease": {
                    "active": bool(active_session),
                    "page": active_session.get("page") if active_session else "",
                    "expiresInSeconds": self._lease_remaining_seconds(active_session)
                    if active_session
                    else 0,
                },
            }, None

    def release(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            return 400, None, {
                "code": "invalid_request",
                "message": "sessionId 必填",
                "retryable": False,
            }
        with self._lock:
            self._reap_expired_sessions()
            existed = session_id in self._sessions
            if session_id == self._boot_session_id:
                return 200, {
                    "released": False,
                    "alreadyReleased": False,
                    "module": "medicine-box",
                    "sessionId": session_id,
                    "reason": "boot_session_reserved",
                }, None
            self._sessions.pop(session_id, None)
        return 200, {
            "released": True,
            "alreadyReleased": not existed,
            "module": "medicine-box",
            "sessionId": session_id,
        }, None

    def _capture_frame(self):
        if self._capture_service is None:
            raise RuntimeError("ov5647_capture_service_unconfigured")
        return self._capture_service.capture(
            timeout_seconds=20.0,
            require_next_frame=True,
        )

    def recognize(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get("sessionId") or "").strip()
        source = str(payload.get("source") or "assistant-photo").strip()
        with self._lock:
            self._reap_expired_sessions()
            item = self._sessions.get(session_id)
            if item and item.get("ready"):
                self._touch_session(item)
        if not session_id:
            return 400, None, {
                "code": "invalid_request",
                "message": "sessionId 必填",
                "retryable": False,
            }
        if not item or not item.get("ready"):
            return 503, None, {
                "code": "module_not_ready",
                "message": "药盒 OCR 尚未预加载完成",
                "retryable": True,
            }
        started = time.perf_counter()
        try:
            capture = self._capture_frame()
            frame_path = ""
            try:
                import cv2

                if cv2.imwrite(FRAME_PATH, capture.frame):
                    frame_path = FRAME_PATH
            except Exception:
                frame_path = ""
            candidates = self._run_inference(capture.frame)
            selected = next((candidate for candidate in candidates if candidate["name"]), None)
            elapsed = round((time.perf_counter() - started) * 1000, 1)
            data = {
                "success": bool(selected),
                "medicineName": selected["name"] if selected else "",
                "source": source,
                "frameId": capture.frameId,
                "pipelineGeneration": capture.pipelineGeneration,
                "framePath": frame_path,
                "durationMs": elapsed,
                "rawText": selected["rawText"] if selected else "",
                "confidence": selected["confidence"] if selected else 0.0,
                "candidates": candidates,
            }
            if not selected:
                data["status"] = "no_match"
                return 200, data, None
            data["status"] = "matched"
            return 200, data, None
        except TimeoutError as exc:
            runtime = self._ocr_runtime.status(MODE_MEDICINE_BOX)
            message = (
                "药盒 OCR 排队超时，未进入模型推理，常驻模型未受影响"
                if str(exc) == "ocr_inference_queue_timeout"
                else "药盒 OCR 单次任务超时；失控 worker 已终止，推理权已安全释放，下次 preload 将重建模型"
            )
            return 504, None, {
                "code": "recognition_timeout",
                "stage": "ocr_inference",
                "message": message,
                "retryable": True,
                "modelResident": runtime["modelResident"],
            }
        except Exception as exc:
            detail = str(exc).strip()
            message = f"药盒 OCR 运行失败: {type(exc).__name__}"
            if detail:
                message = f"{message}: {detail}"
            return 503, None, {
                "code": getattr(exc, "code", "ocr_unavailable"),
                "stage": getattr(exc, "stage", "ocr_inference"),
                "message": message,
                "retryable": getattr(exc, "retryable", True),
            }

    def close(self) -> None:
        if self._owns_runtime:
            self._ocr_runtime.close()
