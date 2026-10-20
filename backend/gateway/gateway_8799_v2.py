#!/usr/bin/env python3
"""Minimal K1 gateway for the deployed services and static UI."""

from __future__ import annotations

import argparse
import http.client
import json
import mimetypes
import os
import posixpath
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from boot_supervisor import BootSupervisor
from ov5647_capture_service import Ov5647CaptureService
from shared_ocr_runtime import (
    DEFAULT_PRESCRIPTION_DET_MODEL,
    DEFAULT_PRESCRIPTION_REC_MODEL,
    SharedOcrRuntime,
)
from task_coordinator import TaskBusyError, TaskCoordinator


QA_PATH = "/api/v1/medicine-qa/messages"
HEALTH_PATH = "/api/v1/health"
ASR_PRELOAD_PATH = "/api/v1/asr/preload"
ASR_RECORDINGS_PATH = "/api/v1/asr/recordings/"
TTS_SYNTHESIZE_PATH = "/api/v1/tts/synthesize"
TTS_WARMUP_PATH = "/api/v1/tts/warmup"
MEDICATION_SCHEDULE_PATH = "/api/v1/medication-schedule/generate"
MEDICINE_BOX_V2_PREFIX = "/api/v2/medicine-box"
PRESCRIPTION_OCR_V2_PREFIX = "/api/v2/prescription-ocr"
FACE_V2_PREFIX = "/api/v2/face"
ASSISTANT_V2_PREFIX = "/api/v2/assistant"
HEALTH_V2_PREFIX = "/api/v2/health"
PILL_COUNT_V2_PREFIX = "/api/v2/pill-count"
SYSTEM_V2_PREFIX = "/api/v2/system"
CABINET_V1_PREFIX = "/api/v1/cabinets"
BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
CABINET_ROOT = BACKEND_ROOT / "cabinet"
CABINET_PULSE_MS = 600
ASSISTANT_LEASE_SECONDS = 180


def request_id() -> str:
    return "req_" + uuid.uuid4().hex[:16]


def envelope(data=None, error=None, request_id_value=None):
    return {"requestId": request_id_value or request_id(), "data": data, "error": error}


def error(code, message, retryable=False, **metadata):
    payload = {"code": code, "message": message, "retryable": bool(retryable)}
    payload.update({key: value for key, value in metadata.items() if value is not None})
    return payload


class CpuUsageSampler:
    """Calculate aggregate CPU usage from consecutive /proc/stat snapshots."""

    def __init__(self, stat_path: Path = Path("/proc/stat")):
        self.stat_path = stat_path
        self._lock = threading.Lock()
        self._previous = None

    def _read_snapshot(self):
        first_line = self.stat_path.read_text(encoding="utf-8").splitlines()[0]
        fields = first_line.split()
        if not fields or fields[0] != "cpu" or len(fields) < 5:
            raise ValueError("invalid_proc_stat")
        values = [int(value) for value in fields[1:9]]
        total = sum(values)
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return total, idle, time.monotonic()

    def sample(self):
        current = self._read_snapshot()
        sampled_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        logical_cores = max(1, int(os.cpu_count() or 1))
        with self._lock:
            previous = self._previous
            self._previous = current
        if previous is None:
            return {
                "cpu": {"usagePercent": None, "logicalCores": logical_cores, "sampleWindowMs": 0, "state": "warming_up"},
                "sampledAt": sampled_at,
            }
        total_delta = current[0] - previous[0]
        idle_delta = current[1] - previous[1]
        if total_delta <= 0 or idle_delta < 0:
            raise ValueError("invalid_cpu_delta")
        usage = max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        return {
            "cpu": {
                "usagePercent": round(usage, 1),
                "logicalCores": logical_cores,
                "sampleWindowMs": max(1, round((current[2] - previous[2]) * 1000)),
                "state": "ready",
            },
            "sampledAt": sampled_at,
        }


class AssistantLeaseRegistry:
    """Track browser ownership without stopping services owned outside the gateway."""

    def __init__(self, lease_seconds: int = ASSISTANT_LEASE_SECONDS):
        self.lease_seconds = lease_seconds
        self._lock = threading.RLock()
        self._leases = {}

    def _reap_locked(self):
        now = time.monotonic()
        expired = [session_id for session_id, expiry in self._leases.items() if expiry <= now]
        for session_id in expired:
            self._leases.pop(session_id, None)

    def _state_locked(self):
        now = time.monotonic()
        return {
            "activeLeaseCount": len(self._leases),
            "expiresInSeconds": max(0, int(max(self._leases.values(), default=now) - now)),
        }

    def acquire(self, session_id: str):
        with self._lock:
            self._reap_locked()
            self._leases[session_id] = time.monotonic() + self.lease_seconds
            return self._state_locked()

    def release(self, session_id: str):
        with self._lock:
            self._reap_locked()
            existed = self._leases.pop(session_id, None) is not None
            return existed, self._state_locked()

    def status(self):
        with self._lock:
            self._reap_locked()
            return self._state_locked()

    def contains(self, session_id: str) -> bool:
        with self._lock:
            self._reap_locked()
            return bool(session_id) and session_id in self._leases

class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    qa_url = ""
    asr_url = ""
    tts_url = ""
    ui_root = Path(".")
    shared_ocr_runtime = None
    medicine_box_service = None
    prescription_ocr_service = None
    pill_count_service = None
    face_service = None
    ov5647_capture_service = None
    medication_scheduler = None
    cabinet_lock = threading.Lock()
    task_coordinator = TaskCoordinator()
    boot_supervisor = BootSupervisor()
    asr_tasks = {}
    asr_tasks_lock = threading.RLock()
    assistant_lifecycle_lock = threading.Lock()
    assistant_leases = AssistantLeaseRegistry()
    cpu_usage_sampler = CpuUsageSampler()

    def write_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-Id")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_OPTIONS(self):
        self.write_json(HTTPStatus.OK, envelope({"status": "ok"}))

    def do_GET(self):
        request_path = urlsplit(self.path).path
        path = request_path.rstrip("/") or "/"
        if path == HEALTH_PATH:
            self.proxy_health()
            return
        if path == f"{HEALTH_V2_PREFIX}/live":
            self.health_live()
            return
        if path == f"{HEALTH_V2_PREFIX}/ready":
            self.health_ready()
            return
        if path == f"{SYSTEM_V2_PREFIX}/metrics":
            self.system_metrics()
            return
        if path == "/api/v2/capabilities":
            self.health_capabilities()
            return
        if path == "/api/v2/diagnose/current":
            self.diagnose_current()
            return
        if path == "/api/v2/tasks/status":
            self.write_json(HTTPStatus.OK, envelope(self.task_coordinator.status()))
            return
        if path.startswith("/api/v2/diagnose/tasks/"):
            self.diagnose_task(path.rsplit("/", 1)[-1], deep=False)
            return
        if path.startswith("/api/v2/diagnose/deep/"):
            self.diagnose_task(path.rsplit("/", 1)[-1], deep=True)
            return
        if path == f"{MEDICINE_BOX_V2_PREFIX}/status":
            query = parse_qs(urlsplit(self.path).query)
            self.medicine_box_status((query.get("sessionId") or [""])[0])
            return
        if path == f"{PRESCRIPTION_OCR_V2_PREFIX}/status":
            query = parse_qs(urlsplit(self.path).query)
            self.prescription_ocr_status((query.get("sessionId") or [""])[0])
            return
        if path == f"{PILL_COUNT_V2_PREFIX}/status":
            query = parse_qs(urlsplit(self.path).query)
            self.pill_count_status((query.get("sessionId") or [""])[0])
            return
        if path == f"{FACE_V2_PREFIX}/status":
            query = parse_qs(urlsplit(self.path).query)
            self.face_status((query.get("sessionId") or [""])[0])
            return
        if path == f"{ASSISTANT_V2_PREFIX}/status":
            self.assistant_status()
            return
        cabinet_id = self.cabinet_id_from_path(path, "state")
        if cabinet_id is not None:
            self.cabinet_state(cabinet_id)
            return
        if request_path == "/ui" or request_path.startswith("/ui/"):
            self.serve_ui(request_path)
            return
        if path.startswith(ASR_RECORDINGS_PATH):
            self.asr_status(path.removeprefix(ASR_RECORDINGS_PATH))
            return
        self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "接口不存在")))

    def do_POST(self):
        path = urlsplit(self.path).path.rstrip("/")
        if path == MEDICATION_SCHEDULE_PATH:
            self.medication_schedule_generate()
            return
        if path == QA_PATH:
            self.proxy_qa()
            return
        if path == f"{MEDICINE_BOX_V2_PREFIX}/preload":
            self.medicine_box_preload()
            return
        if path == f"{MEDICINE_BOX_V2_PREFIX}/recognitions":
            self.medicine_box_recognize()
            return
        if path == f"{MEDICINE_BOX_V2_PREFIX}/release":
            self.medicine_box_release()
            return
        if path == f"{PRESCRIPTION_OCR_V2_PREFIX}/preload":
            self.prescription_ocr_preload()
            return
        if path == f"{PRESCRIPTION_OCR_V2_PREFIX}/recognitions":
            self.prescription_ocr_recognize()
            return
        if path == f"{PRESCRIPTION_OCR_V2_PREFIX}/release":
            self.prescription_ocr_release()
            return
        if path == f"{PILL_COUNT_V2_PREFIX}/preload":
            self.pill_count_preload()
            return
        if path == f"{PILL_COUNT_V2_PREFIX}/recognitions":
            self.pill_count_recognize()
            return
        if path == f"{PILL_COUNT_V2_PREFIX}/release":
            self.pill_count_release()
            return
        if path == f"{FACE_V2_PREFIX}/preload":
            self.face_preload()
            return
        if path == f"{FACE_V2_PREFIX}/register":
            self.face_register()
            return
        if path == f"{FACE_V2_PREFIX}/verify":
            self.face_verify()
            return
        if path == f"{FACE_V2_PREFIX}/remove":
            self.face_remove()
            return
        if path == f"{FACE_V2_PREFIX}/reset":
            self.face_reset()
            return
        if path == f"{FACE_V2_PREFIX}/release":
            self.face_release()
            return
        if path == f"{ASSISTANT_V2_PREFIX}/preload":
            self.assistant_preload()
            return
        if path == f"{ASSISTANT_V2_PREFIX}/release":
            self.assistant_release()
            return
        cabinet_id = self.cabinet_id_from_path(path, "open")
        if cabinet_id is not None:
            self.cabinet_open(cabinet_id)
            return
        if path == ASR_PRELOAD_PATH:
            self.asr_preload()
            return
        if path.startswith(ASR_RECORDINGS_PATH) and path.endswith("/stop"):
            self.asr_stop(path.removeprefix(ASR_RECORDINGS_PATH).removesuffix("/stop"))
            return
        if path == ASR_RECORDINGS_PATH.rstrip("/"):
            self.asr_start()
            return
        if path == TTS_SYNTHESIZE_PATH:
            self.tts_synthesize()
            return
        if path == TTS_WARMUP_PATH:
            self.tts_warmup()
            return
        self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "接口不存在")))

    def read_request_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length < 0 or length > 16 * 1024:
            raise ValueError
        raw = self.rfile.read(length) if length else b"{}"
        parsed = json.loads(raw.decode("utf-8"))
        return parsed.get("data", parsed) if isinstance(parsed, dict) else {}

    def medication_schedule_generate(self):
        """Return a rule-based preview without modifying members or hardware."""
        request_id_value = self.headers.get("X-Request-Id") or request_id()
        if self.medication_scheduler is None:
            self.write_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                envelope(
                    error=error("scheduler_unavailable", "服药方案引擎不可用", True),
                    request_id_value=request_id_value,
                ),
            )
            return

        try:
            payload = self.read_request_json()
            if not isinstance(payload, dict):
                raise ValueError("请求体必须是对象")
            result = self.medication_scheduler.schedule(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
            # Invalid or incomplete prescription data is a review outcome, not a
            # gateway failure. The page can keep the user in the review flow.
            self.write_json(
                HTTPStatus.OK,
                envelope(
                    {
                        "status": "needs_review",
                        "reason": str(exc),
                        "reasons": ["invalid_or_incomplete_prescription"],
                        "ruleVersion": getattr(self.medication_scheduler, "rule_version", "unknown"),
                    },
                    request_id_value=request_id_value,
                ),
            )
            return
        except Exception as exc:
            self.write_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                envelope(
                    error=error("scheduler_error", f"服药方案引擎执行失败: {type(exc).__name__}", True),
                    request_id_value=request_id_value,
                ),
            )
            return

        self.write_json(HTTPStatus.OK, envelope(result, request_id_value=request_id_value))

    def internal_json(self, url, method="GET", payload=None, timeout=10):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            url,
            data=body,
            method=method,
            headers={"Content-Type": "application/json"} if body is not None else {},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status, raw = response.status, response.read()
        except urllib.error.HTTPError as exc:
            status, raw = exc.code, exc.read()
        return status, json.loads(raw.decode("utf-8"))

    def backend_unavailable(self, code, message):
        self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error(code, message, True)))

    @staticmethod
    def cabinet_id_from_path(path, action):
        prefix = f"{CABINET_V1_PREFIX}/"
        suffix = f"/{action}"
        if not path.startswith(prefix) or not path.endswith(suffix):
            return None
        raw_id = path[len(prefix):-len(suffix)]
        try:
            return int(raw_id)
        except ValueError:
            return 0

    def validate_cabinet_id(self, cabinet_id):
        if 1 <= cabinet_id <= 12:
            return True
        self.write_json(
            HTTPStatus.BAD_REQUEST,
            envelope(error=error("invalid_cabinet_id", "柜号必须是 1 至 12", False)),
        )
        return False

    @staticmethod
    def create_cabinet_controller():
        cabinet_root = str(CABINET_ROOT)
        if cabinet_root not in sys.path:
            sys.path.insert(0, cabinet_root)
        from cabinet_api import MedicineCabinet
        return MedicineCabinet(bus=3, pulse_ms=CABINET_PULSE_MS)

    def cabinet_open(self, cabinet_id):
        try:
            self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(
                HTTPStatus.BAD_REQUEST,
                envelope(error=error("invalid_request", "请求 JSON 格式不正确", False)),
            )
            return
        if not self.validate_cabinet_id(cabinet_id):
            return

        cabinet = None
        try:
            with self.cabinet_lock:
                cabinet = self.create_cabinet_controller()
                result = cabinet.open_door(cabinet_id, pulse_ms=CABINET_PULSE_MS, wait_closed=False)
                door = result.status_after_open.to_dict()
            self.write_json(
                HTTPStatus.OK,
                envelope({
                    "cabinetId": cabinet_id,
                    "opened": True,
                    "pulseMs": result.pulse_ms,
                    "closed": door["closed"],
                    "state": door["status"],
                    "door": door,
                }),
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            self.backend_unavailable("cabinet_unavailable", "药柜硬件暂不可用")
        finally:
            if cabinet is not None:
                cabinet.close()

    def cabinet_state(self, cabinet_id):
        if not self.validate_cabinet_id(cabinet_id):
            return

        cabinet = None
        try:
            with self.cabinet_lock:
                cabinet = self.create_cabinet_controller()
                door = cabinet.get_door_status(cabinet_id).to_dict()
            self.write_json(
                HTTPStatus.OK,
                envelope({
                    "cabinetId": cabinet_id,
                    "closed": door["closed"],
                    "state": door["status"],
                    "status": door["status"],
                    "raw": door["raw"],
                    "source": "gpio",
                }),
            )
        except (ImportError, OSError, RuntimeError, ValueError):
            self.backend_unavailable("cabinet_unavailable", "药柜硬件暂不可用")
        finally:
            if cabinet is not None:
                cabinet.close()

    def _medicine_box_payload(self):
        try:
            return self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return None

    def _medicine_box_result(self, result, *, request_id_value=None, task_id=None):
        status, data, err = result
        rid = request_id_value or self.headers.get("X-Request-Id") or request_id()
        if task_id and data is not None:
            data = {**data, "taskId": task_id}
        if task_id and err is not None:
            err = {**err, "taskId": task_id, "requestId": rid, "released": True}
        self.write_json(status, envelope(data=data, error=err, request_id_value=rid))

    def _run_coordinated_service(self, task, payload, callback, *, run_seconds, wait_seconds=2.0):
        rid = self.headers.get("X-Request-Id") or request_id()
        session_id = str(payload.get("sessionId") or payload.get("flowId") or "")
        purpose = str(payload.get("purpose") or payload.get("source") or task)
        try:
            record = self.task_coordinator.acquire(
                task,
                session_id=session_id,
                purpose=purpose,
                request_id=rid,
                wait_seconds=wait_seconds,
                run_seconds=run_seconds,
            )
        except TaskBusyError as exc:
            self.write_json(HTTPStatus.CONFLICT, envelope(
                error=error("inference_busy", "另一项推理尚未真实结束", True, stage="queue", activeTask=exc.active),
                request_id_value=rid,
            ))
            return
        try:
            result = callback(payload)
            status, _data, err = result
            if status < 400 and err is None:
                self.task_coordinator.finish(record.taskId, state="completed")
            else:
                self.task_coordinator.finish(
                    record.taskId,
                    state="timed_out" if status == HTTPStatus.GATEWAY_TIMEOUT else "failed",
                    error_code=str((err or {}).get("code") or "task_failed"),
                    error_message=str((err or {}).get("message") or "task failed"),
                )
            self._medicine_box_result(result, request_id_value=rid, task_id=record.taskId)
        except Exception as exc:
            self.task_coordinator.finish(record.taskId, state="failed", error_code=type(exc).__name__, error_message=str(exc))
            self.write_json(HTTPStatus.INTERNAL_SERVER_ERROR, envelope(
                error=error("task_failed", str(exc), True, stage=task, taskId=record.taskId, released=True),
                request_id_value=rid,
            ))

    def medicine_box_preload(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.medicine_box_service is not None:
            self._medicine_box_result(self.medicine_box_service.preload(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药盒 OCR adapter 未就绪", True)))

    def medicine_box_recognize(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.medicine_box_service is not None:
            self._run_coordinated_service("ocr", payload, self.medicine_box_service.recognize, run_seconds=50)
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药盒 OCR adapter 未就绪", True)))

    def medicine_box_release(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.medicine_box_service is not None:
            self._medicine_box_result(self.medicine_box_service.release(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药盒 OCR adapter 未就绪", True)))

    def medicine_box_status(self, session_id):
        if self.medicine_box_service is None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药盒 OCR adapter 未就绪", True)))
            return
        self._medicine_box_result(self.medicine_box_service.status(session_id))

    def prescription_ocr_preload(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.prescription_ocr_service is not None:
            self._run_coordinated_service(
                "prescription_ocr",
                payload,
                self.prescription_ocr_service.preload,
                run_seconds=65,
            )
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(
                error=error("model_unavailable", "处方 OCR adapter 未就绪", True)
            ))

    def prescription_ocr_recognize(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.prescription_ocr_service is not None:
            self._run_coordinated_service(
                "prescription_ocr",
                payload,
                self.prescription_ocr_service.recognize,
                run_seconds=65,
            )
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(
                error=error("model_unavailable", "处方 OCR adapter 未就绪", True)
            ))

    def prescription_ocr_release(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.prescription_ocr_service is not None:
            self._medicine_box_result(self.prescription_ocr_service.release(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(
                error=error("model_unavailable", "处方 OCR adapter 未就绪", True)
            ))

    def prescription_ocr_status(self, session_id):
        if self.prescription_ocr_service is None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(
                error=error("model_unavailable", "处方 OCR adapter 未就绪", True)
            ))
            return
        self._medicine_box_result(self.prescription_ocr_service.status(session_id))

    def pill_count_preload(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.pill_count_service is not None:
            self._medicine_box_result(self.pill_count_service.preload(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药丸识别 adapter 未就绪", True)))

    def pill_count_recognize(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.pill_count_service is not None:
            self._run_coordinated_service("yolo", payload, self.pill_count_service.recognize, run_seconds=45)
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药丸识别 adapter 未就绪", True)))

    def pill_count_release(self):
        payload = self._medicine_box_payload()
        if payload is not None and self.pill_count_service is not None:
            self._medicine_box_result(self.pill_count_service.release(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药丸识别 adapter 未就绪", True)))

    def pill_count_status(self, session_id):
        if self.pill_count_service is None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "药丸识别 adapter 未就绪", True)))
            return
        self._medicine_box_result(self.pill_count_service.status(session_id))

    def _face_payload(self):
        try:
            return self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return None

    def _face_result(self, result):
        status, data, err = result
        rid = self.headers.get("X-Request-Id") or request_id()
        self.write_json(status, envelope(data=data, error=err, request_id_value=rid))

    def face_preload(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            self._face_result(self.face_service.preload(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def _run_face_with_camera_handoff(self, payload, callback):
        capture = self.ov5647_capture_service
        if capture is None:
            return callback(payload)
        owner = f"face-{payload.get('purpose') or 'task'}-{uuid.uuid4().hex[:12]}"
        try:
            paused = capture.pause_for_handoff(owner, reason="face-imx219")
        except Exception as exc:
            return 503, None, error(
                getattr(exc, "code", "camera_handoff_failed"),
                f"failed to release OV5647 before Face task: {exc}",
                True,
                stage=getattr(exc, "stage", "camera_handoff"),
            )

        face_result = None
        face_exception = None
        try:
            face_result = callback(payload)
        except Exception as exc:
            face_exception = exc

        resume_error = None
        resumed = None
        try:
            resumed = capture.resume_after_handoff(owner)
        except Exception as exc:
            resume_error = exc

        if face_exception is not None:
            if resume_error is not None:
                print(f"OV5647 resume failed after Face exception: {resume_error}", file=sys.stderr)
            raise face_exception
        if resume_error is not None:
            face_error = face_result[2] if face_result is not None else None
            return 503, None, error(
                getattr(resume_error, "code", "camera_resume_failed"),
                f"Face task ended but OV5647 did not recover: {resume_error}",
                True,
                stage=getattr(resume_error, "stage", "camera_handoff"),
                faceError=face_error,
            )

        status, data, err = face_result
        handoff = {
            "pausedGeneration": paused.get("pipelineGeneration"),
            "resumedGeneration": resumed.get("pipelineGeneration"),
            "handoffCount": resumed.get("handoffCount"),
        }
        if data is not None:
            data = {**data, "cameraHandoff": handoff}
        if err is not None:
            err = {**err, "cameraHandoff": handoff}
        return status, data, err

    def face_register(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            coordinated = {**payload, "purpose": "register"}
            self._run_coordinated_service(
                "face",
                coordinated,
                lambda item: self._run_face_with_camera_handoff(item, self.face_service.register),
                run_seconds=85,
            )
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def face_verify(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            coordinated = {**payload, "purpose": "verify"}
            self._run_coordinated_service(
                "face",
                coordinated,
                lambda item: self._run_face_with_camera_handoff(item, self.face_service.verify),
                run_seconds=55,
            )
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def face_remove(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            self._face_result(self.face_service.remove(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def face_reset(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            self._face_result(self.face_service.reset(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def face_release(self):
        payload = self._face_payload()
        if payload is not None and self.face_service is not None:
            self._face_result(self.face_service.release(payload))
        elif payload is not None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))

    def face_status(self, session_id):
        if self.face_service is None:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("model_unavailable", "face adapter 未就绪", True)))
            return
        self._face_result(self.face_service.status(session_id))

    def _service_ready(self, url: str, path: str, timeout: float = 90.0) -> tuple[bool, dict]:
        deadline = time.monotonic() + timeout
        last = {}
        while time.monotonic() < deadline:
            try:
                status, raw = self.internal_json(url + path, timeout=3)
                last = raw
                if status < 400:
                    if path == "/api/asr/status":
                        model = raw.get("model") or {}
                        if model.get("ready") is True or model.get("state") == "ready":
                            return True, raw
                    elif raw.get("status") == "ok" or raw.get("ready") is True or (
                        isinstance(raw.get("data"), dict) and raw["data"].get("status") in {"ok", "ready"}
                    ):
                        return True, raw
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
                pass
            time.sleep(1)
        return False, last

    def assistant_preload(self):
        payload = self._medicine_box_payload()
        if payload is None:
            return
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "sessionId 必填")))
            return
        started = time.perf_counter()
        asr_ready, _ = self._service_ready(self.asr_url, "/api/asr/status", 1)
        llm_ready, _ = self._service_ready("http://127.0.0.1:8080", "/health", 3)
        qa_ready, _ = self._service_ready(self.qa_url, "/api/v1/health", timeout=3)
        if not llm_ready or not qa_ready:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error(
                "assistant_not_ready",
                "开机常驻的 LLM/问药服务尚未就绪",
                True,
                stage="assistant_preload",
                llmReady=llm_ready,
                qaReady=qa_ready,
            )))
            return
        with self.assistant_lifecycle_lock:
            lease = self.assistant_leases.acquire(session_id)
        data = {
            "ready": True,
            "sessionId": session_id,
            "lease": lease,
            "tts": {"resident": False},
            "asr": {"ready": asr_ready},
            "llm": {"ready": True},
            "qa": {"ready": True},
            "durationMs": round((time.perf_counter() - started) * 1000, 1),
        }
        self.write_json(HTTPStatus.OK, envelope(data))

    def assistant_release(self):
        payload = self._medicine_box_payload()
        if payload is None:
            return
        session_id = str(payload.get("sessionId") or "").strip()
        if not session_id:
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "sessionId 必填")))
            return
        with self.assistant_lifecycle_lock:
            existed, lease = self.assistant_leases.release(session_id)
            # Page switches must not unload resident assistant models; services stop only at system shutdown.
        self.write_json(HTTPStatus.OK, envelope({
            "released": True,
            "alreadyReleased": not existed,
            "sessionId": session_id,
            "asrReleased": False,
            "qaReleased": False,
            "llmReleased": False,
            "keptWarm": True,
            "lease": lease,
            "tts": {"resident": False},
        }))

    def assistant_status(self):
        asr_ready, _asr = self._service_ready(self.asr_url, "/api/asr/status", 1)
        llm_ready = self._llm_ready()
        qa_ready, _qa = self._service_ready(self.qa_url, "/api/v1/health", 1)
        self.write_json(HTTPStatus.OK, envelope({"asr": {"ready": asr_ready}, "llm": {"ready": llm_ready}, "qa": {"ready": qa_ready}, "lease": self.assistant_leases.status(), "tts": {"resident": False}}))

    @staticmethod
    def _llm_ready():
        try:
            with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("status") == "ok" or payload.get("ready") is True
        except Exception:
            return False

    @staticmethod
    def _hard_recover_assistant_timeout() -> str:
        """Terminate potentially active QA/LLM cgroups before releasing inference."""
        errors = []
        for unit in ("smart-medicine-qa.service", "smart-medicine-llm.service"):
            try:
                result = subprocess.run(
                    ["systemctl", "--user", "kill", "--kill-who=all", "--signal=SIGKILL", unit],
                    check=False, capture_output=True, text=True, timeout=5,
                )
                if result.returncode not in {0, 1}:
                    errors.append(f"kill:{unit}:{result.returncode}")
            except Exception as exc:
                errors.append(f"kill:{unit}:{type(exc).__name__}")
        for unit in ("smart-medicine-llm.service", "smart-medicine-qa.service"):
            try:
                subprocess.run(
                    ["systemctl", "--user", "start", unit],
                    check=False, capture_output=True, text=True, timeout=8,
                )
            except Exception as exc:
                errors.append(f"start:{unit}:{type(exc).__name__}")
        return ";".join(errors) if errors else "qa_llm_cgroups_restarted"

    def health_live(self):
        self.write_json(HTTPStatus.OK, envelope({"status": "live", "module": "gateway", "port": 8799}))

    def system_metrics(self):
        try:
            self.write_json(HTTPStatus.OK, envelope(self.cpu_usage_sampler.sample()))
        except (OSError, ValueError, IndexError) as exc:
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error(
                "system_metrics_unavailable",
                "CPU 指标暂不可用",
                True,
                causeCode=type(exc).__name__,
            )))

    def health_ready(self):
        ocr = self._status_from_service(self.medicine_box_service)
        prescription_ocr = self._status_from_service(self.prescription_ocr_service)
        yolo = self._status_from_service(self.pill_count_service)
        face = self._status_from_service(self.face_service)
        camera = self.ov5647_capture_service.status() if self.ov5647_capture_service is not None else {"ready": False}
        boot = self.boot_supervisor.status()
        boot_modules = boot.get("modules") or {}

        # The K1 status page reports cold-boot outcomes, not transient page
        # sessions (for example an idle face preview).  Each module's boot
        # supervisor result is therefore the authoritative readiness signal.
        def boot_ready(name: str) -> bool:
            return (boot_modules.get(name) or {}).get("state") == "ready"

        components = {
            "gateway": {"ready": True},
            "asr": {"ready": boot_ready("asr")},
            "qa": {"ready": boot_ready("qa")},
            "llm": {"ready": boot_ready("llm")},
            "ocr": {"ready": boot_ready("ocr"), "status": ocr},
            "prescriptionOcr": {
                "ready": boot_ready("ocr"),
                "required": bool(prescription_ocr.get("configured")),
                "status": prescription_ocr,
            },
            "yolo": {"ready": boot_ready("yolo"), "status": yolo},
            "face": {"ready": boot_ready("face"), "status": face},
            "ov5647": {"ready": boot_ready("ov5647"), "status": camera},
            "bootSupervisor": {"ready": bool(boot.get("ready")), "status": boot},
        }
        ready = all(
            component["ready"]
            for component in components.values()
            if component.get("required", True)
        )
        if ready:
            self.write_json(HTTPStatus.OK, envelope({"status": "ready", "components": components}))
            return
        self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(
            {"status": "not_ready", "components": components},
            error=error("gateway_not_ready", "Gateway dependencies are not ready", True),
        ))

    def health_capabilities(self):
        prescription = self._status_from_service(self.prescription_ocr_service)
        prescription_available = bool(prescription.get("configured"))
        prescription_reason = None
        if self.prescription_ocr_service is None:
            prescription_reason = "adapter_unavailable"
        elif not prescription_available:
            prescription_reason = "prescription_models_missing"
        self.write_json(HTTPStatus.OK, envelope({
            "gateway": {"version": "8799-v2"},
            "modules": {
                "qa": {"available": True},
                "asr": {"available": True},
                "cabinet": {"available": True},
                "face": {"available": self.face_service is not None},
                "medicineBox": {"available": self.medicine_box_service is not None},
                "prescriptionOcr": {
                    "available": prescription_available,
                    "ready": bool(prescription.get("modelResident")),
                    "reason": prescription_reason,
                },
                "pillCount": {"available": self.pill_count_service is not None},
                "tts": {"available": False, "reason": "tts_disabled"},
                "assistant": {"available": True, "leaseRequired": True},
            },
        }))

    @staticmethod
    def _status_from_service(service):
        if service is None:
            return {"ready": False, "available": False, "error": "adapter_unavailable"}
        try:
            status, data, err = service.status("")
        except Exception as exc:
            return {"ready": False, "available": False, "error": type(exc).__name__}
        result = dict(data or {})
        result.setdefault("available", status < 500 and err is None)
        if err:
            result["error"] = err
        return result

    @staticmethod
    def _diagnose_grade(task, status_data):
        last_error = status_data.get("lastError") or {}
        if isinstance(last_error, dict) and last_error.get("code"):
            age = int(last_error.get("ageSeconds") or 0)
            if age <= 300:
                return "ERROR", last_error.get("code"), last_error.get("message") or "任务最近失败", last_error.get("suggestion") or "按最近失败详情排查"
        if not status_data.get("available", True):
            return "ERROR", "TASK_ADAPTER_UNAVAILABLE", "任务适配器不可用", "请等待系统加载完成，或检查正式网关 adapter 初始化日志"
        if not status_data.get("ready", False):
            return "WARNING", "TASK_NOT_READY", "任务尚未常驻", "请等待开机 preload 完成后重试"
        if status_data.get("busy", False):
            return "WARNING", "TASK_BUSY", "任务正在运行或设备被占用", "请等待当前识别结束后重试"
        active = GatewayHandler.task_coordinator.status().get("activeTask")
        if active:
            return "WARNING", "TASK_COORDINATOR_BUSY", "推理调度器正被占用", f"当前任务：{active.get('task')}"
        return "OK", None, "任务状态正常", "可以发起热调用"

    def _assistant_diag_status(self):
        asr_ready = False
        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/status", timeout=0.2)
            model = raw.get("model") or {}
            asr_ready = status < 400 and (model.get("ready") is True or model.get("state") == "ready" or raw.get("ok") is True)
        except Exception:
            asr_ready = False
        return {"ready": self._llm_ready(), "available": True, "asrReady": asr_ready, "tcmPolicy": "disable_or_heap_fallback"}

    def _asr_diag_status(self):
        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/status", timeout=0.3)
        except Exception as exc:
            return {"ready": False, "available": False, "error": type(exc).__name__}
        model = raw.get("model") or {}
        model_state = str(model.get("state") or raw.get("model_state") or raw.get("state") or "")
        ready = bool(raw.get("ok", False)) and (model.get("ready") is True or model_state in {"ready", "loaded"})
        return {"ready": ready, "available": status < 500, "modelState": model_state, "rawOk": raw.get("ok", False), "resourcePolicy": "original"}

    def _task_diag(self, task):
        task = str(task or "").strip().lower()
        if task in {"llm", "assistant"}:
            data = self._assistant_diag_status()
            normalized = "llm"
        elif task == "asr":
            data = self._asr_diag_status()
            normalized = "asr"
        elif task in {"ocr", "medicine-box", "medicine_box"}:
            data = self._status_from_service(self.medicine_box_service)
            normalized = "ocr"
        elif task in {"prescription", "prescription-ocr", "prescription_ocr"}:
            data = self._status_from_service(self.prescription_ocr_service)
            normalized = "prescription_ocr"
        elif task in {"yolo", "pill", "pill-count", "pill_count"}:
            data = self._status_from_service(self.pill_count_service)
            normalized = "yolo"
        elif task == "face":
            data = self._status_from_service(self.face_service)
            normalized = "face"
        else:
            return None
        level, code, message, suggestion = self._diagnose_grade(normalized, data)
        return {
            "task": normalized,
            "level": level,
            "code": code,
            "message": message,
            "suggestion": suggestion,
            "status": data,
            "checksSkipped": ["no_model_test", "no_service_restart", "no_model_release", "no_affinity_change", "no_full_log_scan"],
        }

    def diagnose_current(self):
        tasks = {
            name: self._task_diag(name)
            for name in ["llm", "asr", "ocr", "prescription_ocr", "yolo", "face"]
        }
        levels = [item["level"] for item in tasks.values() if item]
        system_level = "ERROR" if "ERROR" in levels else "WARNING" if "WARNING" in levels else "OK"
        self.write_json(HTTPStatus.OK, envelope({
            "policy": {
                "defaultMode": "read_only_lightweight",
                "noAutoRestart": True,
                "noAutoRelease": True,
                "noAutoModelTest": True,
                "singleGatewayAuthoritative": True,
                "deepDiagnosisRequiresManualTrigger": True,
            },
            "gateway": {"port": self.server.server_address[1]},
            "scheduler": self.task_coordinator.status(),
            "boot": self.boot_supervisor.status(),
            "ov5647": self.ov5647_capture_service.status() if self.ov5647_capture_service is not None else {"ready": False},
            "level": system_level,
            "tasks": tasks,
        }))

    def diagnose_task(self, task, deep=False):
        result = self._task_diag(task)
        if result is None:
            self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("task_not_found", "Unknown diagnostic task", False)))
            return
        if deep:
            result["deepDiagnosis"] = {
                "executed": False,
                "reason": "深度诊断必须由前端人工确认后再执行；当前接口只返回轻量状态，不跑模型测试",
                "wouldCheck": ["single_task_test_input", "recent_task_log", "latency_retest", "resource_snapshot"],
            }
        self.write_json(HTTPStatus.OK, envelope(result))

    def asr_error(self, status, raw):
        reason = str(raw.get("error") or "asr_failed")
        code = {
            "model_not_ready": "asr_model_loading",
            "already_recording": "asr_busy",
            "recognition_running": "asr_busy",
            "not_recording": "asr_state_conflict",
            "job_mismatch": "asr_state_conflict",
        }.get(reason, "asr_failed")
        self.write_json(status, envelope(error=error(code, reason, status >= 500 or code in {"asr_model_loading", "asr_busy"})))

    def asr_preload(self):
        try:
            self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return
        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/preload", method="POST", payload={})
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            self.backend_unavailable("asr_unavailable", "语音识别服务不可用")
            return
        if status >= 400 or not raw.get("ok", False):
            self.asr_error(status, raw)
            return
        state = raw.get("model_state", "loading")
        self.write_json(HTTPStatus.OK, envelope({"state": state, "model": {"ready": state == "ready", "name": "SenseVoice"}}))

    def asr_start(self):
        try:
            payload = self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return
        rid = self.headers.get("X-Request-Id") or request_id()
        try:
            record = self.task_coordinator.acquire(
                "asr", session_id=str(payload.get("sessionId") or ""), purpose="record-and-recognize",
                request_id=rid, wait_seconds=1.0, run_seconds=75,
            )
        except TaskBusyError as exc:
            self.write_json(HTTPStatus.CONFLICT, envelope(
                error=error("inference_busy", "上一项推理尚未真实结束", True, stage="queue", activeTask=exc.active),
                request_id_value=rid,
            ))
            return
        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/record/start", method="POST", payload={})
        except (urllib.error.URLError, TimeoutError):
            self.task_coordinator.finish(record.taskId, state="failed", error_code="asr_unavailable", error_message="ASR unavailable")
            self.backend_unavailable("asr_unavailable", "语音识别服务不可用")
            return
        if status >= 400 or not raw.get("ok", False):
            self.task_coordinator.finish(record.taskId, state="failed", error_code=str(raw.get("error") or "asr_failed"), error_message=str(raw.get("error") or "ASR start failed"))
            self.asr_error(status, raw)
            return
        job_id = str(raw.get("job_id") or "")
        if not job_id:
            self.task_coordinator.finish(record.taskId, state="failed", error_code="asr_job_missing", error_message="ASR returned no job id")
            self.write_json(HTTPStatus.BAD_GATEWAY, envelope(
                error=error("asr_job_missing", "语音识别服务未返回任务编号", True, stage="asr_start"),
                request_id_value=rid,
            ))
            return
        with self.asr_tasks_lock:
            self.asr_tasks[job_id] = record.taskId
        # Start supervision at recording start, not stop.  The ASR backend can
        # auto-stop at its maximum duration, and a page can disappear without
        # ever sending /stop; neither case may strand inference ownership.
        threading.Thread(target=self._watch_asr_completion, args=(job_id, record.taskId), name=f"asr-watch-{job_id[:8]}", daemon=True).start()
        self.write_json(HTTPStatus.OK, envelope({"jobId": job_id, "taskId": record.taskId, "state": "recording"}, request_id_value=rid))

    def asr_stop(self, job_id):
        try:
            self.read_request_json()
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return
        if not job_id or "/" in job_id:
            self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "录音任务不存在")))
            return

        with self.asr_tasks_lock:
            task_id = self.asr_tasks.get(job_id, "")
        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/record/stop", method="POST", payload={"job_id": job_id})
        except (urllib.error.URLError, TimeoutError):
            if task_id:
                self.task_coordinator.finish(task_id, state="failed", error_code="asr_unavailable", error_message="ASR unavailable while stopping")
            self.backend_unavailable("asr_unavailable", "语音识别服务不可用")
            return
        if status >= 400 or not raw.get("ok", False):
            if task_id:
                self.task_coordinator.finish(task_id, state="failed", error_code=str(raw.get("error") or "asr_failed"), error_message=str(raw.get("error") or "ASR stop failed"))
            self.asr_error(status, raw)
            return
        if raw.get("job_id") != job_id:
            if task_id:
                self.task_coordinator.finish(task_id, state="failed", error_code="asr_state_conflict", error_message="ASR job mismatch")
            self.write_json(HTTPStatus.CONFLICT, envelope(error=error("asr_state_conflict", "录音任务不匹配", True)))
            return
        self.write_json(HTTPStatus.OK, envelope({"jobId": job_id, "taskId": task_id, "state": "recognizing"}))

    @classmethod
    def _watch_asr_completion(cls, job_id, task_id):
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline:
            try:
                request = urllib.request.Request(f"{cls.asr_url}/api/asr/status", method="GET")
                with urllib.request.urlopen(request, timeout=1) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                recognition = raw.get("recognition") or {}
                if recognition.get("job_id") != job_id:
                    time.sleep(0.2)
                    continue
                state = recognition.get("state")
                if state == "done":
                    cls.task_coordinator.finish(task_id, state="completed")
                    break
                if state == "error":
                    cls.task_coordinator.finish(task_id, state="failed", error_code="asr_recognition_failed", error_message=str(recognition.get("error") or "ASR failed"))
                    break
            except Exception:
                pass
            time.sleep(0.2)
        else:
            # A timed-out in-process ASR inference cannot be cancelled safely.
            # Kill the service cgroup first; only then release coordinator
            # ownership.  systemd restarts the resident model for the next use.
            recovery = "asr service cgroup terminated"
            try:
                subprocess.run(
                    ["systemctl", "--user", "kill", "--kill-who=all", "--signal=SIGKILL", "smart-medicine-asr.service"],
                    check=False, capture_output=True, text=True, timeout=5,
                )
                subprocess.run(
                    ["systemctl", "--user", "start", "smart-medicine-asr.service"],
                    check=False, capture_output=True, text=True, timeout=8,
                )
            except Exception as exc:
                recovery = f"asr recovery failed: {type(exc).__name__}"
            cls.task_coordinator.finish(task_id, state="timed_out", error_code="asr_timeout", error_message=f"ASR completion deadline exceeded; {recovery}")
        with cls.asr_tasks_lock:
            cls.asr_tasks.pop(job_id, None)

    def asr_status(self, job_id):
        if not job_id or "/" in job_id:
            self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "录音任务不存在")))
            return

        try:
            status, raw = self.internal_json(f"{self.asr_url}/api/asr/status")
        except (urllib.error.URLError, TimeoutError):
            self.backend_unavailable("asr_unavailable", "语音识别服务不可用")
            return
        recognition = raw.get("recognition") or {}
        if status >= 400 or not raw.get("ok", False):
            self.asr_error(status, raw)
            return
        if recognition.get("job_id") != job_id:
            self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "录音任务不存在")))
            return
        state = {"running": "recognizing", "recording": "recording"}.get(recognition.get("state"), recognition.get("state", "unknown"))
        with self.asr_tasks_lock:
            task_id = self.asr_tasks.get(job_id, "")
        data = {"jobId": job_id, "taskId": task_id, "state": state}
        if state == "done":
            data["text"] = recognition.get("text", "")
            if task_id:
                self.task_coordinator.finish(task_id, state="completed")
        if state == "error":
            data["errorCode"] = "asr_recognition_failed"
            if task_id:
                self.task_coordinator.finish(task_id, state="failed", error_code="asr_recognition_failed", error_message=str(recognition.get("error") or "ASR failed"))
        self.write_json(HTTPStatus.OK, envelope(data))

    def tts_synthesize(self):
        self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("tts_disabled", "TTS 已按系统策略禁用")))

    def tts_warmup(self):
        self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("tts_disabled", "TTS 已按系统策略禁用")))

    def proxy_health(self):
        try:
            with urllib.request.urlopen(f"{self.qa_url}/api/v1/health", timeout=3) as response:
                payload = json.loads(response.read().decode("utf-8"))
            self.write_json(HTTPStatus.OK, envelope({"status": "ok", "medicineQa": payload.get("data", payload)}))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error("qa_service_unavailable", "问药服务未就绪", True)))

    def proxy_qa(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16 * 1024:
                raise ValueError
            body = self.rfile.read(length)
            input_payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.write_json(HTTPStatus.BAD_REQUEST, envelope(error=error("invalid_request", "请求 JSON 或字段格式不正确")))
            return

        qa_input = input_payload.get("data", input_payload) if isinstance(input_payload, dict) else {}
        session_id = str(qa_input.get("sessionId") or "").strip()
        if not self.assistant_leases.contains(session_id):
            self.write_json(HTTPStatus.CONFLICT, envelope(error=error(
                "assistant_session_not_ready", "助手会话未建立或已过期，请重新连接", True,
                stage="assistant_session", sessionId=session_id,
            )))
            return

        request_id_value = self.headers.get("X-Request-Id") or request_id()
        request = urllib.request.Request(
            f"{self.qa_url}{QA_PATH}",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "X-Request-Id": request_id_value,
            },
        )

        try:
            record = self.task_coordinator.acquire(
                "llm", session_id=session_id, purpose="medicine-qa",
                request_id=request_id_value, wait_seconds=2.0, run_seconds=42,
            )
        except TaskBusyError as exc:
            self.write_json(HTTPStatus.CONFLICT, envelope(
                error=error("inference_busy", "上一项推理尚未真实结束", True, stage="queue", activeTask=exc.active),
                request_id_value=request_id_value,
            ))
            return
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                response_body = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            response_body = exc.read()
            status = exc.code
        except (urllib.error.URLError, http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, TimeoutError) as exc:
            is_timeout = isinstance(exc, TimeoutError) or isinstance(getattr(exc, "reason", None), TimeoutError) or "timed out" in str(exc).lower()
            if is_timeout:
                recovery = self._hard_recover_assistant_timeout()
                self.task_coordinator.finish(record.taskId, state="timed_out", error_code="qa_service_timeout", error_message=f"{exc}; {recovery}")
                self.write_json(HTTPStatus.GATEWAY_TIMEOUT, envelope(error=error(
                    "qa_service_timeout", "问药服务未在期限内结束，失控进程已隔离并开始恢复", True,
                    stage="llm", taskId=record.taskId, released=True, recovery=recovery,
                ), request_id_value=request_id_value))
            else:
                self.task_coordinator.finish(record.taskId, state="failed", error_code="qa_service_unavailable", error_message=str(exc))
                self.write_json(HTTPStatus.SERVICE_UNAVAILABLE, envelope(error=error(
                    "qa_service_unavailable", "问药服务不可用", True,
                    stage="qa", taskId=record.taskId, released=True,
                ), request_id_value=request_id_value))
            return
        try:
            payload = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self.task_coordinator.finish(record.taskId, state="failed", error_code="qa_invalid_response", error_message="invalid JSON")
            self.write_json(HTTPStatus.BAD_GATEWAY, envelope(error=error("qa_invalid_response", "问药服务返回格式无效", True, taskId=record.taskId), request_id_value=request_id_value))
            return
        if isinstance(payload.get("data"), dict):
            payload["data"]["taskId"] = record.taskId
        if isinstance(payload.get("error"), dict):
            payload["error"]["taskId"] = record.taskId
            payload["error"]["requestId"] = request_id_value
        if status < 400 and not payload.get("error"):
            self.task_coordinator.finish(record.taskId, state="completed")
        else:
            downstream_error = payload.get("error") or {}
            downstream_code = str(downstream_error.get("code") or "qa_failed")
            if status == HTTPStatus.GATEWAY_TIMEOUT or downstream_code == "model_timeout":
                recovery = self._hard_recover_assistant_timeout()
                self.task_coordinator.finish(record.taskId, state="timed_out", error_code=downstream_code, error_message=f"{downstream_error.get('message') or 'QA timed out'}; {recovery}")
                if isinstance(payload.get("error"), dict):
                    payload["error"]["recovery"] = recovery
            else:
                self.task_coordinator.finish(record.taskId, state="failed", error_code=downstream_code, error_message=str(downstream_error.get("message") or "QA failed"))
            if isinstance(payload.get("error"), dict):
                payload["error"]["released"] = True
        self.write_json(status, payload)
    def serve_ui(self, request_path):
        relative = unquote(request_path.removeprefix("/ui/"))
        if not relative:
            relative = "index.html"
        relative = posixpath.normpath(relative).lstrip("/")
        if relative.startswith(".."):
            self.write_json(HTTPStatus.FORBIDDEN, envelope(error=error("forbidden", "非法路径")))
            return
        file_path = (self.ui_root / relative).resolve()
        if self.ui_root not in file_path.parents and file_path != self.ui_root:
            self.write_json(HTTPStatus.FORBIDDEN, envelope(error=error("forbidden", "非法路径")))
            return
        if not file_path.is_file():
            self.write_json(HTTPStatus.NOT_FOUND, envelope(error=error("not_found", "页面文件不存在")))
            return
        content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_path.stat().st_size))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.end_headers()
        with file_path.open("rb") as source:
            shutil.copyfileobj(source, self.wfile)

    def log_message(self, _format, *_args):
        return


def wait_external_boot_service(name: str, url: str, *, timeout_seconds: float = 90.0) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
            data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
            if name == "asr":
                model = data.get("model") or {}
                ready = model.get("ready") is True or model.get("state") == "ready"
            else:
                ready = data.get("ready") is True or data.get("status") in {"ok", "ready"}
            if ready:
                return {"ready": True, "service": name, "status": data.get("status") or "ready"}
        except urllib.error.HTTPError as exc:
            last_error = f"HTTP {exc.code}"
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.5)
    raise TimeoutError(f"{name} boot readiness timeout: {last_error}")


def boot_shared_ocr_services():
    """Warm both OCR modes sequentially through the one shared worker."""
    if GatewayHandler.medicine_box_service is None:
        return 503, None, error(
            "model_unavailable",
            "药盒 OCR adapter 未就绪",
            True,
        )
    status, medicine_data, medicine_error = GatewayHandler.medicine_box_service.boot_preload()
    if status >= 400 or medicine_error is not None:
        return status, medicine_data, medicine_error

    prescription_result = {
        "ready": False,
        "available": False,
        "reason": "adapter_unavailable",
    }
    if GatewayHandler.prescription_ocr_service is not None:
        prescription_status, prescription_data, prescription_error = (
            GatewayHandler.prescription_ocr_service.boot_preload()
        )
        prescription_result = {
            "ready": prescription_status < 400 and prescription_error is None,
            "available": prescription_status < 500,
            "data": prescription_data,
            "error": prescription_error,
        }
        if prescription_status >= 400 or prescription_error is not None:
            recovery_status, recovery_data, recovery_error = (
                GatewayHandler.medicine_box_service.preload(
                    {"sessionId": "__boot__", "page": "boot"}
                )
            )
            if recovery_status >= 400 or recovery_error is not None:
                return recovery_status, recovery_data, recovery_error
    return 200, {
        "ready": True,
        "medicineBox": medicine_data,
        "prescriptionOcr": prescription_result,
    }, None


def start_parallel_boot_preload() -> None:
    jobs = [
        ("asr", lambda: wait_external_boot_service("asr", f"{GatewayHandler.asr_url}/api/asr/status"), True),
        ("llm", lambda: wait_external_boot_service("llm", "http://127.0.0.1:8080/health"), True),
        ("qa", lambda: wait_external_boot_service("qa", f"{GatewayHandler.qa_url}/api/v1/health"), True),
    ]
    if GatewayHandler.ov5647_capture_service is not None:
        jobs.append(("ov5647", GatewayHandler.ov5647_capture_service.start, True))
    if GatewayHandler.medicine_box_service is not None:
        jobs.append(("ocr", boot_shared_ocr_services, True))
    if GatewayHandler.pill_count_service is not None:
        jobs.append(("yolo", GatewayHandler.pill_count_service.boot_preload, True))
    if GatewayHandler.face_service is not None:
        jobs.append(("face", lambda: GatewayHandler.face_service.preload({"sessionId": "__boot__", "page": "boot"}), True))
    GatewayHandler.boot_supervisor.start(jobs, attempts=3)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8799)
    parser.add_argument("--qa-url", default="http://127.0.0.1:8790")
    parser.add_argument("--asr-url", default="http://127.0.0.1:8765")
    parser.add_argument("--tts-url", default="http://127.0.0.1:8788")
    parser.add_argument("--medicine-box-root", default=str(BACKEND_ROOT / "medicine_box_recognition"))
    parser.add_argument("--prescription-det-model", default=str(DEFAULT_PRESCRIPTION_DET_MODEL))
    parser.add_argument("--prescription-rec-model", default=str(DEFAULT_PRESCRIPTION_REC_MODEL))
    parser.add_argument("--face-root", default=str(BACKEND_ROOT / "face"))
    parser.add_argument("--ov5647-width", type=int, default=1920)
    parser.add_argument("--ov5647-height", type=int, default=1080)
    parser.add_argument("--ov5647-stabilization-seconds", type=float, default=15.0)
    parser.add_argument("--ui-root", default=str(PROJECT_ROOT / "frontend"))
    args = parser.parse_args()

    GatewayHandler.qa_url = args.qa_url.rstrip("/")
    GatewayHandler.asr_url = args.asr_url.rstrip("/")
    GatewayHandler.tts_url = args.tts_url.rstrip("/")
    GatewayHandler.ui_root = Path(args.ui_root).resolve()
    GatewayHandler.ov5647_capture_service = Ov5647CaptureService(
        width=args.ov5647_width,
        height=args.ov5647_height,
        stabilization_seconds=args.ov5647_stabilization_seconds,
    )
    GatewayHandler.shared_ocr_runtime = SharedOcrRuntime(
        args.medicine_box_root,
        prescription_det_path=args.prescription_det_model,
        prescription_rec_path=args.prescription_rec_model,
    )
    try:
        from medicine_box_v2_service import MedicineBoxV2Service
        GatewayHandler.medicine_box_service = MedicineBoxV2Service(
            args.medicine_box_root,
            GatewayHandler.ov5647_capture_service,
            ocr_runtime=GatewayHandler.shared_ocr_runtime,
        )
    except Exception as exc:
        raise SystemExit(f"medicine-box adapter unavailable: {exc}")
    try:
        from prescription_ocr_v2_service import PrescriptionOcrV2Service
        GatewayHandler.prescription_ocr_service = PrescriptionOcrV2Service(
            GatewayHandler.ov5647_capture_service,
            ocr_runtime=GatewayHandler.shared_ocr_runtime,
        )
    except Exception as exc:
        raise SystemExit(f"prescription-ocr adapter unavailable: {exc}")
    try:
        from pill_count_v2_service import PillCountV2Service
        GatewayHandler.pill_count_service = PillCountV2Service(GatewayHandler.ov5647_capture_service)
    except Exception as exc:
        raise SystemExit(f"pill-count adapter unavailable: {exc}")
    try:
        from face_v2_service import FaceV2Service
        GatewayHandler.face_service = FaceV2Service(args.face_root)
    except Exception as exc:
        raise SystemExit(f"face adapter unavailable: {exc}")
    scheduler_root = BACKEND_ROOT / "scheduler"
    try:
        if str(scheduler_root) not in sys.path:
            sys.path.insert(0, str(scheduler_root))
        from medication_scheduler import MedicationScheduler

        GatewayHandler.medication_scheduler = MedicationScheduler.from_file(
            scheduler_root / "medicine_scheduler_rules_v1.json"
        )
    except Exception as exc:
        print(f"medication scheduler unavailable: {exc}", file=sys.stderr)
        GatewayHandler.medication_scheduler = None
    if not GatewayHandler.ui_root.is_dir():
        raise SystemExit(f"UI root does not exist: {GatewayHandler.ui_root}")
    # Parallel boot preload keeps models resident without running recognition.
    start_parallel_boot_preload()
    server = ThreadingHTTPServer((args.host, args.port), GatewayHandler)
    try:
        server.serve_forever()
    finally:
        if GatewayHandler.ov5647_capture_service is not None:
            GatewayHandler.ov5647_capture_service.close()
        if GatewayHandler.medicine_box_service is not None and hasattr(GatewayHandler.medicine_box_service, "close"):
            GatewayHandler.medicine_box_service.close()
        if GatewayHandler.prescription_ocr_service is not None and hasattr(GatewayHandler.prescription_ocr_service, "close"):
            GatewayHandler.prescription_ocr_service.close()
        if GatewayHandler.pill_count_service is not None and hasattr(GatewayHandler.pill_count_service, "close"):
            GatewayHandler.pill_count_service.close()
        if GatewayHandler.face_service is not None and hasattr(GatewayHandler.face_service, "close"):
            GatewayHandler.face_service.close()
        if GatewayHandler.shared_ocr_runtime is not None:
            GatewayHandler.shared_ocr_runtime.close()


if __name__ == "__main__":
    main()
