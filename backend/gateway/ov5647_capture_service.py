from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np


CAMERA_WIDTH = 960
CAMERA_HEIGHT = 544
CAMERA_FORMAT = "NV12"
DEFAULT_CAMERA_JSON = "/usr/share/camera_json/csi1_camera_auto.json"
DEFAULT_STABILIZATION_SECONDS = 15.0


@dataclass
class CaptureResult:
    frame: np.ndarray
    frameId: int
    capturedAt: float
    pipelineGeneration: int


class Ov5647CaptureError(RuntimeError):
    def __init__(self, code: str, message: str, *, stage: str, retryable: bool = True):
        super().__init__(message)
        self.code = code
        self.stage = stage
        self.retryable = retryable


class Ov5647CaptureService:
    """Own the only long-running OV5647 pipeline used by OCR and YOLO.

    The sensor/ISP remains PLAYING, while NV12-to-BGR conversion happens only
    when a caller asks for a frame. appsink keeps a one-frame dropping queue so
    idle operation does not continuously run videoconvert or steal LLM CPU.
    """

    def __init__(
        self,
        *,
        camera_json: str = DEFAULT_CAMERA_JSON,
        width: int = CAMERA_WIDTH,
        height: int = CAMERA_HEIGHT,
        stabilization_seconds: float = DEFAULT_STABILIZATION_SECONDS,
        gst_loader: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.camera_json = camera_json
        self._active_camera_json = camera_json
        self.width = int(width)
        self.height = int(height)
        self.stabilization_seconds = max(0.0, float(stabilization_seconds))
        self._gst_loader = gst_loader or self._load_gst
        self._clock = clock
        self._lock = threading.RLock()
        self._pipeline = None
        self._sink = None
        self._gst = None
        self._generation = 0
        self._frame_id = 0
        self._state = "not_started"
        self._last_error: dict | None = None
        self._started_at = 0.0
        self._last_frame_at = 0.0
        self._last_frame_shape: list[int] | None = None
        self._restart_count = 0
        self._handoff_count = 0
        self._pause_owner = ""
        self._pause_reason = ""

    @staticmethod
    def _load_gst():
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        return Gst

    def _pipeline_text(self) -> str:
        return (
            f"spacemitsrc name=src location={self._active_camera_json} close-dmabuf=1 "
            f"! video/x-raw,width={self.width},height={self.height},format={CAMERA_FORMAT},framerate=30/1 "
            "! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
        )

    def _prepare_camera_json(self) -> str:
        source = Path(self.camera_json)
        if not source.is_file():
            return self.camera_json
        data = json.loads(source.read_text(encoding="utf-8"))
        data["show_fps"] = 0
        data["tuning_server_enable"] = 0
        data["cpp_node"][0]["size_width"] = self.width
        data["cpp_node"][0]["size_height"] = self.height
        data["cpp_node"][0]["src_from_file"] = 0
        data["isp_node"][0]["out_width"] = self.width
        data["isp_node"][0]["out_height"] = self.height
        data["isp_node"][0]["sensor_work_mode"] = 0
        runtime = Path(os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"))
        target = runtime / f"smart-medicine-ov5647-{self.width}x{self.height}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".tmp-{os.getpid()}")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, target)
        return str(target)

    def _set_error(self, code: str, message: str, stage: str) -> None:
        self._state = "error"
        self._last_error = {
            "code": code,
            "message": message,
            "stage": stage,
            "retryable": True,
            "occurredAt": self._clock(),
        }

    def _stop_locked(self, *, strict: bool = False, timeout_seconds: float = 2.0) -> None:
        stop_error: Exception | None = None
        if self._pipeline is not None and self._gst is not None:
            try:
                change = self._pipeline.set_state(self._gst.State.NULL)
                state_change, state, _pending = self._pipeline.get_state(
                    int(max(0.1, timeout_seconds) * self._gst.SECOND)
                )
                if change == self._gst.StateChangeReturn.FAILURE or state_change == self._gst.StateChangeReturn.FAILURE:
                    raise Ov5647CaptureError(
                        "camera_stop_failed", "OV5647 failed to enter NULL", stage="camera_handoff"
                    )
                if state != self._gst.State.NULL:
                    raise Ov5647CaptureError(
                        "camera_stop_timeout", "OV5647 did not reach NULL", stage="camera_handoff"
                    )
            except Exception as exc:
                stop_error = exc
            finally:
                self._pipeline = None
                self._sink = None
        if strict and stop_error is not None:
            if isinstance(stop_error, Ov5647CaptureError):
                raise stop_error
            raise Ov5647CaptureError("camera_stop_failed", str(stop_error), stage="camera_handoff") from stop_error

    def pause_for_handoff(self, owner: str, *, reason: str = "face", timeout_seconds: float = 5.0) -> dict:
        owner = str(owner or "").strip()
        if not owner:
            raise ValueError("camera handoff owner is required")
        with self._lock:
            if self._pause_owner:
                if self._pause_owner == owner:
                    return self.status()
                raise Ov5647CaptureError(
                    "camera_handoff_busy",
                    f"OV5647 is paused by {self._pause_owner}",
                    stage="camera_handoff",
                )
            self._state = "pausing"
            try:
                self._stop_locked(strict=True, timeout_seconds=timeout_seconds)
            except Exception as exc:
                self._set_error(
                    getattr(exc, "code", "camera_stop_failed"),
                    str(exc),
                    getattr(exc, "stage", "camera_handoff"),
                )
                raise
            self._pause_owner = owner
            self._pause_reason = str(reason or "face")
            self._handoff_count += 1
            self._state = "paused"
            self._last_error = None
            return self.status()

    def resume_after_handoff(self, owner: str, *, timeout_seconds: float = 20.0) -> dict:
        owner = str(owner or "").strip()
        with self._lock:
            if not self._pause_owner:
                return self.start(timeout_seconds=timeout_seconds)
            if self._pause_owner != owner:
                raise Ov5647CaptureError(
                    "camera_handoff_owner_mismatch",
                    f"OV5647 is paused by {self._pause_owner}, not {owner}",
                    stage="camera_handoff",
                    retryable=False,
                )
            self._pause_owner = ""
            self._pause_reason = ""
            self._state = "stopped"
            return self.start(timeout_seconds=timeout_seconds)

    def start(self, *, timeout_seconds: float = 20.0) -> dict:
        with self._lock:
            if self._pause_owner:
                return self.status()
            if self._state == "ready" and self._pipeline is not None and self._sink is not None:
                return self.status()
            self._state = "starting"
            self._last_error = None
            try:
                self._active_camera_json = self._prepare_camera_json()
                self._gst = self._gst_loader()
                self._pipeline = self._gst.parse_launch(self._pipeline_text())
                self._sink = self._pipeline.get_by_name("sink")
                if self._sink is None:
                    raise Ov5647CaptureError("camera_appsink_unavailable", "OV5647 appsink unavailable", stage="camera_start")
                change = self._pipeline.set_state(self._gst.State.PLAYING)
                if change == self._gst.StateChangeReturn.FAILURE:
                    raise Ov5647CaptureError("camera_start_failed", "OV5647 failed to enter PLAYING", stage="camera_start")
                state_change, state, _pending = self._pipeline.get_state(int(max(0.1, timeout_seconds) * self._gst.SECOND))
                if state_change == self._gst.StateChangeReturn.FAILURE or state != self._gst.State.PLAYING:
                    raise Ov5647CaptureError("camera_start_timeout", "OV5647 did not reach PLAYING", stage="camera_start")
                self._generation += 1
                self._started_at = self._clock()
                self._state = "ready"
                return self.status()
            except Ov5647CaptureError as exc:
                self._set_error(exc.code, str(exc), exc.stage)
                self._stop_locked()
                raise
            except Exception as exc:
                self._set_error("camera_start_failed", str(exc), "camera_start")
                self._stop_locked()
                raise Ov5647CaptureError("camera_start_failed", str(exc), stage="camera_start") from exc

    def _sample_to_bgr(self, sample) -> np.ndarray:
        buffer = sample.get_buffer()
        ok, mapped = buffer.map(self._gst.MapFlags.READ)
        if not ok:
            raise Ov5647CaptureError("camera_frame_map_failed", "OV5647 frame map failed", stage="camera_frame")
        try:
            array = np.frombuffer(mapped.data, dtype=np.uint8)
            expected = self.width * self.height * 3 // 2
            if array.size < expected:
                raise Ov5647CaptureError("camera_frame_invalid", f"OV5647 NV12 frame too small: {array.size}", stage="camera_frame")
            nv12 = array[:expected].reshape((self.height * 3 // 2, self.width))
            # Imported lazily so an idle camera owner does not initialize any
            # OpenCV worker pool during gateway startup.
            import cv2
            return cv2.cvtColor(nv12, cv2.COLOR_YUV2BGR_NV12)
        finally:
            buffer.unmap(mapped)

    def _pull_locked(self, timeout_seconds: float):
        timeout_ns = int(max(0.05, timeout_seconds) * self._gst.SECOND)
        return self._sink.emit("try-pull-sample", timeout_ns)

    def capture(self, *, timeout_seconds: float = 3.0, require_next_frame: bool = True) -> CaptureResult:
        deadline = self._clock() + max(0.1, timeout_seconds)
        last_error: Exception | None = None
        for attempt in range(2):
            with self._lock:
                try:
                    if self._pause_owner:
                        raise Ov5647CaptureError(
                            "camera_handoff_busy",
                            f"OV5647 is paused for {self._pause_reason or 'hardware handoff'}",
                            stage="camera_handoff",
                        )
                    if self._state != "ready" or self._pipeline is None:
                        self.start(timeout_seconds=min(20.0, max(0.1, deadline - self._clock())))
                    remaining = deadline - self._clock()
                    if remaining <= 0:
                        raise Ov5647CaptureError("camera_timeout", "OV5647 frame deadline exceeded", stage="camera_frame")
                    if require_next_frame:
                        self._pull_locked(min(0.25, remaining))
                        remaining = deadline - self._clock()
                    sample = self._pull_locked(max(0.05, remaining))
                    if sample is None:
                        raise Ov5647CaptureError("camera_frame_timeout", "OV5647 returned no fresh frame", stage="camera_frame")
                    frame = self._sample_to_bgr(sample)
                    self._last_frame_shape = [int(value) for value in frame.shape]
                    self._frame_id += 1
                    self._last_frame_at = self._clock()
                    self._state = "ready"
                    self._last_error = None
                    return CaptureResult(frame, self._frame_id, self._last_frame_at, self._generation)
                except Exception as exc:
                    last_error = exc
                    if getattr(exc, "code", "") == "camera_handoff_busy":
                        raise
                    code = getattr(exc, "code", "camera_unavailable")
                    stage = getattr(exc, "stage", "camera_frame")
                    self._set_error(code, str(exc), stage)
                    self._stop_locked()
                    if attempt == 0 and self._clock() < deadline:
                        self._restart_count += 1
                        continue
        if isinstance(last_error, Ov5647CaptureError):
            raise last_error
        raise Ov5647CaptureError("camera_unavailable", str(last_error or "OV5647 unavailable"), stage="camera_frame")

    def close(self) -> None:
        with self._lock:
            self._stop_locked()
            self._pause_owner = ""
            self._pause_reason = ""
            self._state = "stopped"

    def status(self) -> dict:
        with self._lock:
            available = self._state == "ready" and self._pipeline is not None
            pipeline_age = max(0.0, self._clock() - self._started_at) if self._started_at else 0.0
            stable = available and pipeline_age >= self.stabilization_seconds
            return {
                "ready": available or self._state == "paused",
                "available": available,
                "stable": stable,
                "stabilizing": available and not stable,
                "stabilizationSeconds": self.stabilization_seconds,
                "pipelineAgeSeconds": round(pipeline_age, 3),
                "state": self._state,
                "owner": "gateway-ov5647-shared",
                "sensorWorkMode": 0,
                "format": CAMERA_FORMAT,
                "requestedSize": [self.width, self.height],
                "lastFrameShape": self._last_frame_shape,
                "activeCameraJson": self._active_camera_json,
                "pauseOwner": self._pause_owner or None,
                "pauseReason": self._pause_reason or None,
                "handoffCount": self._handoff_count,
                "pipelineGeneration": self._generation,
                "pipelineRestartCount": self._restart_count,
                "frameId": self._frame_id,
                "startedAt": self._started_at,
                "lastFrameAt": self._last_frame_at,
                "lastError": self._last_error,
            }
