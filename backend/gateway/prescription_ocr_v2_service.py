#!/usr/bin/env python3
"""K1 v2 prescription OCR lifecycle adapter."""

from __future__ import annotations

import threading
import time

from prescription_correction_db import apply_closed_set_correction
from prescription_parser import parse_prescription_lines
from shared_ocr_runtime import (
    MODE_PRESCRIPTION,
    OcrModeUnavailable,
    SharedOcrRuntime,
)


SESSION_LEASE_SECONDS = 120


class PrescriptionOcrV2Service:
    def __init__(
        self,
        capture_service=None,
        *,
        ocr_runtime: SharedOcrRuntime,
    ) -> None:
        self._capture_service = capture_service
        self._ocr_runtime = ocr_runtime
        self._lock = threading.RLock()
        self._sessions: dict[str, dict] = {}
        self._boot_session_id = "__boot__"
        self._boot_state = "not_started"
        self._boot_error = ""
        self._model_version = "ppocrv5_mobile_rec_prescription_int8_48x320"

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

    def _ready_data(self, session_id: str, page: str, item: dict) -> dict:
        runtime = self._ocr_runtime.status(MODE_PRESCRIPTION)
        return {
            "ready": True,
            "module": "prescription-ocr",
            "sessionId": session_id,
            "page": page,
            "modelVersion": self._model_version,
            "provider": runtime["provider"],
            "detectorInputSize": [544, 960],
            "recognizerInputSize": [320, 48],
            "readyAt": item.get("readyAt"),
            "leaseExpiresInSeconds": self._lease_remaining_seconds(item),
        }

    def preload(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get("sessionId") or "").strip()
        page = str(payload.get("page") or "member-form").strip()
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
                runtime = self._ocr_runtime.status(MODE_PRESCRIPTION)
                if runtime["modelResident"]:
                    self._touch_session(current)
                    return 200, self._ready_data(session_id, page, current), None
            try:
                self._ocr_runtime.preload_mode(MODE_PRESCRIPTION)
            except OcrModeUnavailable as exc:
                return 503, None, {
                    "code": exc.code,
                    "message": str(exc),
                    "retryable": True,
                    "stage": "prescription_model",
                }
            except TimeoutError as exc:
                return 504, None, {
                    "code": "prescription_model_timeout",
                    "message": f"处方 OCR 模型加载超时: {exc}",
                    "retryable": True,
                    "stage": "prescription_model",
                }
            except Exception as exc:
                return 503, None, {
                    "code": "prescription_model_unavailable",
                    "message": f"处方 OCR 模型加载失败: {type(exc).__name__}: {exc}",
                    "retryable": True,
                    "stage": "prescription_model",
                }
            ready_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            self._sessions[session_id] = {
                "ready": True,
                "page": page,
                "readyAt": ready_at,
            }
            self._touch_session(self._sessions[session_id])
            return 200, self._ready_data(session_id, page, self._sessions[session_id]), None

    def status(self, session_id: str = "") -> tuple[int, dict, None]:
        with self._lock:
            self._reap_expired_sessions()
            item = self._sessions.get(session_id) if session_id else None
            active_session = self._active_user_session()
            runtime = self._ocr_runtime.status(MODE_PRESCRIPTION)
            ready = bool(item and item.get("ready")) if session_id else runtime["modelResident"]
            available = bool(runtime["configured"])
            return 200, {
                "module": "prescription-ocr",
                "ready": ready,
                "available": available,
                "configured": runtime["configured"],
                "busy": runtime["busy"],
                "sessionId": session_id or None,
                "modelVersion": self._model_version,
                "provider": runtime["provider"],
                "modelResident": runtime["modelResident"],
                "workerResident": runtime["workerResident"],
                "requestedSessionReady": bool(item and item.get("ready")),
                "bootState": self._boot_state,
                "bootError": self._boot_error,
                "lastError": runtime["lastError"],
                "missingModels": runtime["missingModels"],
                "detectorInputSize": [544, 960],
                "recognizerInputSize": [320, 48],
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
                    "module": "prescription-ocr",
                    "sessionId": session_id,
                    "reason": "boot_session_reserved",
                }, None
            self._sessions.pop(session_id, None)
        return 200, {
            "released": True,
            "alreadyReleased": not existed,
            "module": "prescription-ocr",
            "sessionId": session_id,
        }, None

    def _capture_frame(self):
        if self._capture_service is None:
            raise RuntimeError("ov5647_capture_service_unconfigured")
        return self._capture_service.capture(
            timeout_seconds=120.0,
            require_next_frame=True,
        )

    def recognize(self, payload: dict) -> tuple[int, dict | None, dict | None]:
        session_id = str(payload.get("sessionId") or "").strip()
        source = str(payload.get("source") or "member-form-prescription").strip()
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
                "message": "处方 OCR 尚未预加载完成",
                "retryable": True,
            }

        started = time.perf_counter()
        try:
            capture = self._capture_frame()
            lines = self._ocr_runtime.infer(MODE_PRESCRIPTION, capture.frame)
            parsed = apply_closed_set_correction(lines, parse_prescription_lines(lines))
            source_height, source_width = capture.frame.shape[:2]
            data = {
                "status": "recognized" if lines else "no_text",
                "success": bool(lines),
                "source": source,
                "frameId": capture.frameId,
                "pipelineGeneration": capture.pipelineGeneration,
                "sourceCaptureSize": [int(source_width), int(source_height)],
                "durationMs": round((time.perf_counter() - started) * 1000, 1),
                "lines": lines,
                "medicines": parsed["medicines"],
                "member": parsed["member"],
                "correction": parsed["correction"],
                "warnings": parsed["warnings"],
            }
            return 200, data, None
        except OcrModeUnavailable as exc:
            return 503, None, {
                "code": exc.code,
                "stage": "prescription_model",
                "message": str(exc),
                "retryable": True,
            }
        except TimeoutError as exc:
            runtime = self._ocr_runtime.status(MODE_PRESCRIPTION)
            queued = str(exc) == "prescription_ocr_inference_queue_timeout"
            return 504, None, {
                "code": "recognition_timeout",
                "stage": "prescription_inference",
                "message": "处方 OCR 排队超时，常驻模型未受影响"
                if queued
                else "处方 OCR 推理超时；共享 OCR worker 已回收，下次 preload 将重建",
                "retryable": True,
                "modelResident": runtime["modelResident"],
            }
        except Exception as exc:
            return 503, None, {
                "code": getattr(exc, "code", "prescription_ocr_unavailable"),
                "stage": getattr(exc, "stage", "prescription_inference"),
                "message": f"处方 OCR 运行失败: {type(exc).__name__}: {exc}",
                "retryable": getattr(exc, "retryable", True),
            }

    def close(self) -> None:
        return None
