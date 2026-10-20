#!/usr/bin/env python3
"""Live face recognition for the K1 IMX219 MIPI camera.

Camera path:
  spacemitsrc(csi3_camera_auto.json) -> NV12 appsink -> OpenCV BGR

Display path:
  OpenCV BGR annotations -> appsrc -> videoconvert -> waylandsink

The recognition models and face bank come from the official competition
face_det_rec package already installed on the K1 board.
"""

from __future__ import annotations

import argparse
import json
import fcntl
import os
import queue
import signal
import struct
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import Condition, Event, Lock, RLock
from threading import Thread
from typing import Iterable
import select

import cv2
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OFFICIAL_FACE_ROOT = Path(os.environ.get("FACE_DET_REC_ROOT", str(Path(__file__).resolve().parent / "vendor" / "face_det_rec")))
MODEL_ROOT = OFFICIAL_FACE_ROOT / "onnx_model"
STATIC_INT8_DET_ROOT = MODEL_ROOT / "static_int8_20260718_batch2_det"
STATIC_INT8_REC_ROOT = MODEL_ROOT / "static_int8_20260717"
FACE_BANK = Path(os.environ.get("FACE_BANK_DIR", str(PROJECT_ROOT / "data" / "faces")))


def path_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


DEFAULT_DET_MODEL = STATIC_INT8_DET_ROOT / "yolov5n-face_320_cut_static_qdq_int8.onnx"
DEFAULT_REC_MODEL = STATIC_INT8_REC_ROOT / "arcface_mobilefacenet_cut_static_qdq_int8.onnx"
if not path_exists(DEFAULT_DET_MODEL):
    DEFAULT_DET_MODEL = MODEL_ROOT / "yolov5n-face_320_cut.q.onnx"
if not path_exists(DEFAULT_REC_MODEL):
    DEFAULT_REC_MODEL = MODEL_ROOT / "arcface_mobilefacenet_cut.q.onnx"
DET_MODEL = Path(os.environ.get("FACE_DET_MODEL", str(DEFAULT_DET_MODEL)))
REC_MODEL = Path(os.environ.get("FACE_REC_MODEL", str(DEFAULT_REC_MODEL)))

IMX219_JSON = "/usr/share/camera_json/csi3_camera_auto.json"
CAM_W = 1920
CAM_H = 1080

DISPLAY_W = 1024
DISPLAY_H = 600
DISPLAY_FPS = 30
DEFAULT_TARGET_FPS = 15.0

DEFAULT_THRESHOLD = 0.40
DEFAULT_DET_CONF = 0.20
DEFAULT_DETECT_INTERVAL = 1.00
DEFAULT_MIN_FACE_AREA_RATIO = 0.015
DEFAULT_CPU_AFFINITY = "3"
DEFAULT_MAX_TEMP_C = 82.0
DEFAULT_CONTROL_FILE = "/tmp/smart_medicine_face/control.txt"
DEFAULT_MENU_CONTROL_FILE = "/tmp/smart_medicine_face/menu_control.txt"
DEFAULT_TOUCH_DEVICE = "auto"

EV_KEY = 0x01
EV_ABS = 0x03
ABS_X = 0x00
ABS_Y = 0x01
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
BTN_TOUCH = 0x14A


def _ioc(direction: int, kind: int, nr: int, size: int) -> int:
    return (direction << 30) | (size << 16) | (kind << 8) | nr


def _ior(kind: int, nr: int, size: int) -> int:
    return _ioc(2, kind, nr, size)


def eviocgabs(abs_code: int) -> int:
    return _ior(ord("E"), 0x40 + abs_code, struct.calcsize("iiiiii"))

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJKsc-Regular.otf",
]


@dataclass
class FaceResult:
    name: str
    score: float
    box: tuple[int, int, int, int]
    known: bool
    det_score: float = 0.0


class InferenceRight:
    def __init__(self) -> None:
        self._cond = Condition(RLock())
        self._owner: str | None = None
        self._priority_waiter = False

    @contextmanager
    def acquire(self, owner: str, *, priority: bool = False):
        with self._cond:
            if priority:
                self._priority_waiter = True
                print(f"[infer-right] {owner} requested priority inference right")
            while self._owner is not None or (self._priority_waiter and not priority):
                self._cond.wait()
            self._owner = owner
            if priority:
                self._priority_waiter = False
            print(f"[infer-right] acquired by {owner}")
        try:
            yield
        finally:
            with self._cond:
                if self._owner == owner:
                    self._owner = None
                    print(f"[infer-right] released by {owner}")
                    self._cond.notify_all()


INFERENCE_RIGHT = InferenceRight()


@dataclass
class RegisterQuality:
    ok: bool
    message: str
    box: tuple[int, int, int, int] | None
    face_index: int | None
    det_score: float = 0.0


@dataclass
class RuntimeParams:
    threshold: float
    detect_interval: float
    target_fps: float
    min_face_area_ratio: float
    brightness: float
    contrast: float
    gamma: float
    lock: Lock


@dataclass
class LiveStats:
    preview_frames: int = 0
    inference_jobs: int = 0
    inference_faces: int = 0
    inference_time_ms_total: float = 0.0
    last_report_ts: float = 0.0
    last_thermal_check_ts: float = 0.0
    last_submit_ts: float = 0.0
    cached_temp_c: float | None = None


def require_official_package() -> None:
    missing = [p for p in [DET_MODEL, REC_MODEL, FACE_BANK] if not p.exists()]
    if missing:
        for p in missing:
            print(f"[face] missing: {p}", file=sys.stderr)
        raise SystemExit(2)
    sys.path.insert(0, str(OFFICIAL_FACE_ROOT))


def load_thermal_celsius() -> float | None:
    values: list[float] = []
    for path in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
        try:
            raw = float(path.read_text(encoding="ascii").strip())
        except Exception:
            continue
        values.append(raw / 1000.0 if raw > 1000 else raw)
    return max(values) if values else None


def apply_cpu_affinity(cpu_affinity: str) -> None:
    cpus = str(cpu_affinity or "").strip()
    if not cpus:
        return
    try:
        cpu_set: set[int] = set()
        for part in cpus.split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                start, end = [int(v) for v in part.split("-", 1)]
                cpu_set.update(range(start, end + 1))
            else:
                cpu_set.add(int(part))
        if cpu_set:
            os.sched_setaffinity(0, cpu_set)
            print(f"[resource] cpu_affinity={sorted(cpu_set)}")
    except Exception as exc:
        print(f"[resource] cpu_affinity_failed={cpus}: {exc}", file=sys.stderr)


def refresh_thermal(stats: LiveStats, now: float, interval: float) -> float | None:
    if interval <= 0:
        stats.cached_temp_c = load_thermal_celsius()
        stats.last_thermal_check_ts = now
        return stats.cached_temp_c
    if stats.cached_temp_c is None or now - stats.last_thermal_check_ts >= interval:
        stats.cached_temp_c = load_thermal_celsius()
        stats.last_thermal_check_ts = now
    return stats.cached_temp_c


def camera_pipeline(camera_json: str, width: int, height: int) -> str:
    return (
        f"spacemitsrc location={camera_json} close-dmabuf=1 "
        f"! video/x-raw,format=NV12,width={width},height={height} "
        "! appsink drop=true max-buffers=1 sync=false"
    )


def display_pipeline(width: int, height: int, fps: int, sink: str = "wayland") -> str:
    render_rect = f'render-rectangle=<0,0,{width},{height}>'
    if sink == "gl":
        sink_element = f"glimagesink sync=false force-aspect-ratio=false {render_rect}"
    else:
        sink_element = f"waylandsink sync=false fullscreen=true {render_rect}"
    return (
        "appsrc is-live=true block=false format=time "
        f"caps=video/x-raw,format=BGR,width={width},height={height},framerate={fps}/1 "
        "! queue max-size-buffers=2 leaky=downstream "
        "! videoconvert "
        f"! {sink_element}"
    )


class GstCamera:
    def __init__(self, camera_json: str, width: int, height: int):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        self.width = width
        self.height = height
        self.pipeline = Gst.parse_launch(
            f"spacemitsrc location={camera_json} close-dmabuf=1 "
            f"! video/x-raw,format=NV12,width={width},height={height} "
            "! appsink name=sink emit-signals=false drop=true max-buffers=1 sync=false"
        )
        self.sink = self.pipeline.get_by_name("sink")
        self.pipeline.set_state(Gst.State.PLAYING)

    def isOpened(self) -> bool:
        return self.sink is not None

    def read(self):
        sample = self.sink.emit("try-pull-sample", self.Gst.SECOND)
        if sample is None:
            return False, None
        buf = sample.get_buffer()
        caps = sample.get_caps().get_structure(0)
        width = int(caps.get_value("width") or self.width)
        height = int(caps.get_value("height") or self.height)
        data = buf.extract_dup(0, buf.get_size())
        frame = np.frombuffer(data, dtype=np.uint8)
        expected = width * height * 3 // 2
        if frame.size < expected:
            return False, None
        return True, frame[:expected].reshape((height * 3 // 2, width))

    def release(self) -> None:
        self.pipeline.set_state(self.Gst.State.NULL)


class GstDisplay:
    def __init__(self, width: int, height: int, fps: int, sink: str = "wayland"):
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        Gst.init(None)
        self.Gst = Gst
        self.width = width
        self.height = height
        self.fps = max(1, fps)
        self.frame_index = 0
        self.duration = Gst.SECOND // self.fps
        render_rect = f"render-rectangle=<0,0,{width},{height}>"
        if sink == "gl":
            sink_element = f"glimagesink sync=false force-aspect-ratio=false {render_rect}"
        else:
            sink_element = f"waylandsink sync=false fullscreen=true {render_rect}"
        self.pipeline = Gst.parse_launch(
            "appsrc name=src is-live=true block=false format=time "
            f"caps=video/x-raw,format=BGR,width={width},height={height},framerate={self.fps}/1 "
            "! queue max-size-buffers=2 leaky=downstream "
            f"! videoconvert ! {sink_element}"
        )
        self.src = self.pipeline.get_by_name("src")
        self.pipeline.set_state(Gst.State.PLAYING)

    def isOpened(self) -> bool:
        return self.src is not None

    def write(self, frame_bgr: np.ndarray) -> None:
        if frame_bgr.shape[1] != self.width or frame_bgr.shape[0] != self.height:
            frame_bgr = cv2.resize(frame_bgr, (self.width, self.height), interpolation=cv2.INTER_AREA)
        frame_bgr = np.ascontiguousarray(frame_bgr)
        buf = self.Gst.Buffer.new_allocate(None, frame_bgr.nbytes, None)
        buf.fill(0, frame_bgr.tobytes())
        buf.pts = self.frame_index * self.duration
        buf.duration = self.duration
        self.frame_index += 1
        self.src.emit("push-buffer", buf)

    def release(self) -> None:
        if self.src is not None:
            self.src.emit("end-of-stream")
        self.pipeline.set_state(self.Gst.State.NULL)


def open_camera(args: argparse.Namespace):
    pipe = camera_pipeline(args.camera_json, args.width, args.height)
    print(f"[camera] {pipe}")
    cap = cv2.VideoCapture(pipe, cv2.CAP_GSTREAMER)
    if cap.isOpened():
        return cap
    print("[camera] OpenCV GStreamer unavailable, falling back to gi.repository.Gst appsink", file=sys.stderr)
    cap = GstCamera(args.camera_json, args.width, args.height)
    if not cap.isOpened():
        raise RuntimeError("failed to open IMX219 GStreamer appsink pipeline")
    return cap


def open_display(args: argparse.Namespace):
    if args.display == "none":
        return None
    if args.display in ("gst", "gst-gl"):
        sink = "gl" if args.display == "gst-gl" else "wayland"
        display_fps = max(1, int(round(args.target_fps or DISPLAY_FPS)))
        pipe = display_pipeline(args.display_width, args.display_height, display_fps, sink=sink)
        print(f"[display] {pipe}")
        writer = cv2.VideoWriter(
            pipe,
            cv2.CAP_GSTREAMER,
            0,
            display_fps,
            (args.display_width, args.display_height),
            True,
        )
        if writer.isOpened():
            return writer
        print("[display] OpenCV GStreamer unavailable, falling back to gi.repository.Gst appsrc", file=sys.stderr)
        writer = GstDisplay(args.display_width, args.display_height, display_fps, sink=sink)
        if writer.isOpened():
            return writer
        print("[display] GI GStreamer display failed, falling back to OpenCV imshow", file=sys.stderr)

    cv2.namedWindow("Smart Medicine Face ID - IMX219", cv2.WINDOW_NORMAL)
    cv2.resizeWindow("Smart Medicine Face ID - IMX219", args.display_width, args.display_height)
    return None


def read_bgr(cap: cv2.VideoCapture, width: int, height: int) -> np.ndarray | None:
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    if frame.ndim == 2:
        expected_h = int(height * 1.5)
        if frame.shape[0] != expected_h or frame.shape[1] != width:
            print(f"[camera] unexpected NV12 frame shape: {frame.shape}", file=sys.stderr)
            return None
        return cv2.cvtColor(frame, cv2.COLOR_YUV2BGR_NV12)
    return frame


def enhance_frame(frame: np.ndarray, brightness: float, contrast: float, gamma: float) -> np.ndarray:
    # 镜像后再推理和显示，保证画面、检测框、采样坐标一致。
    out = frame
    if abs(contrast - 1.0) > 0.01 or abs(brightness) > 0.5:
        out = cv2.convertScaleAbs(out, alpha=contrast, beta=brightness)
    if abs(gamma - 1.0) > 0.01:
        gamma = max(0.2, min(3.0, gamma))
        lut = np.array([((i / 255.0) ** gamma) * 255 for i in range(256)], dtype=np.uint8)
        out = cv2.LUT(out, lut)
    return out


def load_font(size: int):
    try:
        from PIL import ImageFont
    except Exception:
        return None
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    return None


def draw_texts(frame_bgr: np.ndarray, items: Iterable[tuple[str, tuple[int, int], tuple[int, int, int], int]]) -> np.ndarray:
    items = list(items)
    if not items:
        return frame_bgr

    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        for text, xy, color, _size in items:
            cv2.putText(frame_bgr, text, xy, cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
        return frame_bgr

    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    font_cache: dict[int, object] = {}

    for text, (x, y), color_bgr, size in items:
        if size not in font_cache:
            font_cache[size] = load_font(size) or ImageFont.load_default()
        font = font_cache[size]
        color_rgb = (color_bgr[2], color_bgr[1], color_bgr[0])
        bbox = draw.textbbox((x, y), text, font=font)
        pad = 5
        draw.rectangle(
            (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
            fill=(18, 22, 28),
        )
        draw.text((x, y), text, font=font, fill=color_rgb)

    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def compare_face(face_vector: np.ndarray, face_bank: dict[str, np.ndarray], threshold: float) -> tuple[str, float, bool]:
    best_name = "Unknown"
    best_score = 0.0
    for name, stored in face_bank.items():
        score = float(face_vector.flatten() @ stored.flatten().T)
        if score > best_score:
            best_score = score
            best_name = name
    if best_score < threshold:
        return "未识别", best_score, False
    return best_name, best_score, True


def clip_box(
    box: np.ndarray,
    width: int,
    height: int,
    min_face_area_ratio: float = 0.0,
) -> tuple[int, int, int, int] | None:
    x1, y1, x2, y2 = [int(v) for v in box[:4]]
    x1 = max(0, min(x1, width - 1))
    y1 = max(0, min(y1, height - 1))
    x2 = max(0, min(x2, width - 1))
    y2 = max(0, min(y2, height - 1))
    area = (x2 - x1) * (y2 - y1)
    if x2 - x1 < 24 or y2 - y1 < 24:
        return None
    if min_face_area_ratio > 0 and area < width * height * min_face_area_ratio:
        return None
    return x1, y1, x2, y2


def face_inside_verify_guide(
    box: tuple[int, int, int, int],
    width: int,
    height: int,
    center_tolerance: float = 0.08,
) -> bool:
    x1, y1, x2, y2 = box
    gx1 = int(width * 0.33)
    gx2 = int(width * 0.67)
    gy1 = int(height * 0.16)
    gy2 = int(height * 0.84)
    margin_x = int(width * center_tolerance)
    margin_y = int(height * center_tolerance)
    return x1 >= gx1 - margin_x and x2 <= gx2 + margin_x and y1 >= gy1 - margin_y and y2 <= gy2 + margin_y


def evaluate_register_quality(
    frame: np.ndarray,
    boxes: list[np.ndarray] | None,
    args: argparse.Namespace,
) -> RegisterQuality:
    if not boxes:
        return RegisterQuality(False, "请正对摄像头", None, None)

    candidates: list[tuple[int, int, tuple[int, int, int, int]]] = []
    for idx, box in enumerate(boxes):
        clipped = clip_box(box, frame.shape[1], frame.shape[0], 0.0)
        if clipped is None:
            continue
        x1, y1, x2, y2 = clipped
        det_score = float(box[4]) if len(box) > 4 else 0.0
        candidates.append(((x2 - x1) * (y2 - y1), idx, clipped, det_score))

    if not candidates:
        return RegisterQuality(False, "人脸太小或不完整", None, None)

    candidates.sort(reverse=True)
    area, idx, box, det_score = candidates[0]
    x1, y1, x2, y2 = box
    width = frame.shape[1]
    height = frame.shape[0]
    area_ratio = area / float(width * height)
    if area_ratio < args.register_min_face_area_ratio:
        return RegisterQuality(False, "请靠近一点", box, idx)
    if area_ratio > args.register_max_face_area_ratio:
        return RegisterQuality(False, "请离远一点", box, idx)

    gx1 = int(width * 0.33)
    gx2 = int(width * 0.67)
    gy1 = int(height * 0.16)
    gy2 = int(height * 0.84)
    margin_x = int(width * args.center_tolerance)
    margin_y = int(height * args.center_tolerance)
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    if not (gx1 - margin_x <= cx <= gx2 + margin_x and gy1 - margin_y <= cy <= gy2 + margin_y):
        return RegisterQuality(False, "请把脸移到引导框中心", box, idx)
    if x1 < gx1 - margin_x or x2 > gx2 + margin_x or y1 < gy1 - margin_y or y2 > gy2 + margin_y:
        return RegisterQuality(False, "请让整张脸在框内", box, idx)

    crop = frame[y1:y2, x1:x2]
    if crop.size == 0:
        return RegisterQuality(False, "请保持正脸", box, idx)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    mean_luma = float(gray.mean())
    if mean_luma < args.face_brightness_min:
        return RegisterQuality(False, "光线偏暗", box, idx)
    if mean_luma > args.face_brightness_max:
        return RegisterQuality(False, "光线过亮", box, idx)

    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if sharpness < args.sharpness_threshold:
        return RegisterQuality(False, "请保持稳定，画面有点糊", box, idx)

    return RegisterQuality(True, "保持稳定，正在采集", box, idx)


def recognize_faces(
    det,
    rec,
    frame: np.ndarray,
    threshold: float,
    min_face_area_ratio: float = 0.0,
    main_face_only: bool = False,
    verify_center_gate: bool = True,
    center_tolerance: float = 0.08,
) -> list[FaceResult]:
    with INFERENCE_RIGHT.acquire("face-det"):
        face_imgs, boxes = det.infer_face(frame)
    if not face_imgs or boxes is None:
        return []

    pairs = list(zip(face_imgs, boxes))
    if main_face_only and pairs:
        def face_area(item):
            _face_img, box = item
            clipped = clip_box(box, frame.shape[1], frame.shape[0], min_face_area_ratio)
            if clipped is None:
                return -1
            x1, y1, x2, y2 = clipped
            return (x2 - x1) * (y2 - y1)
        pairs = [max(pairs, key=face_area)]

    results: list[FaceResult] = []
    for face_img, box in pairs:
        clipped = clip_box(box, frame.shape[1], frame.shape[0], min_face_area_ratio)
        if clipped is None or face_img is None or face_img.size == 0:
            continue
        if verify_center_gate and not face_inside_verify_guide(clipped, frame.shape[1], frame.shape[0], center_tolerance):
            continue
        with INFERENCE_RIGHT.acquire("face-rec", priority=True):
            if getattr(rec, "_lazy_provider", None) == "spacemit" and not getattr(rec, "_lazy_loaded", False):
                rec.sess = rec._lazy_make_session(rec._lazy_model_path, rec._lazy_provider)
                rec._lazy_loaded = True
                print(f"[model] recognizer providers: {rec.sess.get_providers()}")
            vector = rec.infer(face_img)
        name, score, known = compare_face(vector, rec.face_bank, threshold)
        det_score = float(box[4]) if len(box) > 4 else 0.0
        results.append(FaceResult(name=name, score=score, box=clipped, known=known, det_score=det_score))
    return results


def best_success_result(results: list[FaceResult], expected_name: str | None) -> FaceResult | None:
    candidates = [r for r in results if r.known]
    if expected_name:
        candidates = [r for r in candidates if r.name == expected_name]
    if not candidates:
        return None
    return max(candidates, key=lambda r: r.score)


def write_result_json(path: str | None, payload: dict) -> None:
    if not path:
        return
    result_path = Path(path)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def annotate(
    frame: np.ndarray,
    results: list[FaceResult],
    fps: float,
    users: list[str],
    args: argparse.Namespace,
    status: str,
    params: RuntimeParams | None = None,
    stats: LiveStats | None = None,
    inference_fps: float = 0.0,
    last_infer_ms: float = 0.0,
) -> np.ndarray:
    out = cv2.resize(frame, (args.display_width, args.display_height), interpolation=cv2.INTER_AREA)
    sx = args.display_width / args.width
    sy = args.display_height / args.height
    text_items: list[tuple[str, tuple[int, int], tuple[int, int, int], int]] = []

    for result in results:
        x1, y1, x2, y2 = result.box
        dx1, dy1 = int(x1 * sx), int(y1 * sy)
        dx2, dy2 = int(x2 * sx), int(y2 * sy)
        color = (40, 220, 70) if result.known else (40, 70, 235)
        cv2.rectangle(out, (dx1, dy1), (dx2, dy2), color, 3)
        cv2.putText(out, f"det:{result.det_score:.2f}", (dx1, max(24, dy1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2)
        label = f"{result.name} {result.score:.2f}" if result.known else f"未识别 {result.score:.2f}"
        text_items.append((label, (dx1, max(10, dy1 - 34)), color, 28))

    if params is not None:
        with params.lock:
            threshold = params.threshold
            interval = params.detect_interval
            target_fps = params.target_fps
        header = (
            f"人脸验证 | 用户:{len(users)} | 预览:{fps:.1f}/{target_fps:.1f} "
            f"| 推理:{inference_fps:.1f} | 耗时:{last_infer_ms:.1f}ms "
            f"| 阈值:{threshold:.2f} | 间隔:{interval:.1f}s"
        )
    else:
        header = f"人脸验证 | 用户:{len(users)} | 预览:{fps:.1f} | 推理:{inference_fps:.1f}"
    cv2.rectangle(out, (0, 0), (args.display_width, 44), (18, 22, 28), -1)
    cv2.putText(out, header, (18, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.72, (235, 235, 235), 2)

    if not results:
        text_items.append(("未检测到人脸", (24, 70), (220, 220, 220), 28))

    if results:
        best = max(results, key=lambda item: item.score)
        if best.known:
            feedback = f"验证成功：{best.name}  分数 {best.score:.2f}"
            feedback_color = (40, 220, 70)
        else:
            feedback = f"验证失败：未识别  分数 {best.score:.2f}"
            feedback_color = (40, 70, 235)
    else:
        feedback = "等待验证：请正对摄像头"
        feedback_color = (80, 180, 240)
    cv2.rectangle(out, (0, args.display_height - 54), (args.display_width, args.display_height), (18, 22, 28), -1)
    cv2.putText(out, feedback, (24, args.display_height - 18), cv2.FONT_HERSHEY_SIMPLEX, 0.82, feedback_color, 2)

    return draw_texts(out, text_items)


def annotate_live_fast(
    frame: np.ndarray,
    results: list[FaceResult],
    fps: float,
    users: list[str],
    args: argparse.Namespace,
    status: str,
    params: RuntimeParams,
    inference_fps: float = 0.0,
    last_infer_ms: float = 0.0,
) -> np.ndarray:
    out = cv2.resize(frame, (args.display_width, args.display_height), interpolation=cv2.INTER_AREA)
    with params.lock:
        threshold = params.threshold
        interval = params.detect_interval
        target_fps = params.target_fps

    best = best_success_result(results, getattr(args, "expected_name", ""))
    if best is not None:
        state = f"OK {best.name} {best.score:.2f}"
        color = (40, 220, 70)
    elif results:
        top = max(results, key=lambda item: item.score)
        state = f"NO MATCH {top.name} {top.score:.2f}"
        color = (40, 70, 235)
    else:
        state = "MOVE INTO FRAME" if getattr(args, "verify_center_gate", True) else "SEARCHING"
        color = (80, 180, 240)

    cv2.rectangle(out, (0, 0), (args.display_width, 42), (18, 22, 28), -1)
    cv2.putText(
        out,
        f"Face verify | users:{len(users)} | preview:{fps:.1f}/{target_fps:.1f} | infer:{inference_fps:.1f} | {last_infer_ms:.0f}ms | int:{interval:.1f}s | th:{threshold:.2f}",
        (14, 28),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.62,
        (235, 235, 235),
        2,
    )
    gx1 = int(args.display_width * 0.33)
    gx2 = int(args.display_width * 0.67)
    gy1 = int(args.display_height * 0.16)
    gy2 = int(args.display_height * 0.84)
    cv2.rectangle(out, (gx1, gy1), (gx2, gy2), color, 3)
    cv2.line(out, ((gx1 + gx2) // 2, gy1), ((gx1 + gx2) // 2, gy1 + 28), color, 2)
    cv2.line(out, ((gx1 + gx2) // 2, gy2 - 28), ((gx1 + gx2) // 2, gy2), color, 2)
    cv2.line(out, (gx1, (gy1 + gy2) // 2), (gx1 + 28, (gy1 + gy2) // 2), color, 2)
    cv2.line(out, (gx2 - 28, (gy1 + gy2) // 2), (gx2, (gy1 + gy2) // 2), color, 2)
    cv2.rectangle(out, (0, args.display_height - 46), (args.display_width, args.display_height), (18, 22, 28), -1)
    cv2.putText(out, f"{state} | {status}", (18, args.display_height - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2)
    return out


def draw_register_guide(
    frame: np.ndarray,
    args: argparse.Namespace,
    quality: RegisterQuality,
    stable_count: int,
) -> np.ndarray:
    # Kept for the richer UI paths; registration uses the faster OpenCV-only renderer below.
    sx = args.display_width / args.width
    sy = args.display_height / args.height
    gx1 = int(args.display_width * 0.33)
    gx2 = int(args.display_width * 0.67)
    gy1 = int(args.display_height * 0.16)
    gy2 = int(args.display_height * 0.84)
    color = (40, 210, 80) if quality.ok else (40, 170, 245)
    cv2.rectangle(frame, (gx1, gy1), (gx2, gy2), color, 3)
    cv2.line(frame, ((gx1 + gx2) // 2, gy1), ((gx1 + gx2) // 2, gy1 + 28), color, 2)
    cv2.line(frame, ((gx1 + gx2) // 2, gy2 - 28), ((gx1 + gx2) // 2, gy2), color, 2)
    cv2.line(frame, (gx1, (gy1 + gy2) // 2), (gx1 + 28, (gy1 + gy2) // 2), color, 2)
    cv2.line(frame, (gx2 - 28, (gy1 + gy2) // 2), (gx2, (gy1 + gy2) // 2), color, 2)

    if quality.box is not None:
        x1, y1, x2, y2 = quality.box
        cv2.rectangle(
            frame,
            (int(x1 * sx), int(y1 * sy)),
            (int(x2 * sx), int(y2 * sy)),
            color,
            2,
        )

    text = f"{quality.message}  {stable_count}/{args.stable_frames}"
    return draw_texts(frame, [(text, (32, args.display_height - 88), color, 28)])


def annotate_register_fast(
    frame: np.ndarray,
    args: argparse.Namespace,
    quality: RegisterQuality,
    stable_count: int,
    samples: int,
    total_samples: int,
    infer_ms: float,
    captured_count: int = 0,
    capture_age: float = 999.0,
) -> np.ndarray:
    out = cv2.resize(frame, (args.display_width, args.display_height), interpolation=cv2.INTER_AREA)
    sx = args.display_width / args.width
    sy = args.display_height / args.height
    color = (40, 210, 80) if quality.ok else (40, 170, 245)
    cv2.rectangle(out, (0, 0), (args.display_width, 44), (18, 22, 28), -1)
    cv2.putText(
        out,
        f"录入人脸：{args.name}  {samples}/{total_samples} | 检测耗时:{infer_ms:.0f}ms",
        (18, 30),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.72,
        (235, 235, 235),
        2,
    )
    gx1 = int(args.display_width * 0.33)
    gx2 = int(args.display_width * 0.67)
    gy1 = int(args.display_height * 0.16)
    gy2 = int(args.display_height * 0.84)
    cv2.rectangle(out, (gx1, gy1), (gx2, gy2), color, 3)
    cv2.line(out, ((gx1 + gx2) // 2, gy1), ((gx1 + gx2) // 2, gy1 + 28), color, 2)
    cv2.line(out, ((gx1 + gx2) // 2, gy2 - 28), ((gx1 + gx2) // 2, gy2), color, 2)
    cv2.line(out, (gx1, (gy1 + gy2) // 2), (gx1 + 28, (gy1 + gy2) // 2), color, 2)
    cv2.line(out, (gx2 - 28, (gy1 + gy2) // 2), (gx2, (gy1 + gy2) // 2), color, 2)
    cv2.rectangle(out, (0, args.display_height - 46), (args.display_width, args.display_height), (18, 22, 28), -1)
    status = "请保持稳定" if quality.ok else "请把脸对准中间框"
    if captured_count > 0 and capture_age < 1.5:
        status = f"已采集 {captured_count}/{total_samples}"
        color = (40, 230, 120)
    cv2.putText(out, f"{status} | 稳定 {stable_count}/{args.stable_frames}", (24, args.display_height - 16), cv2.FONT_HERSHEY_SIMPLEX, 0.72, color, 2)
    return out


def load_models(args: argparse.Namespace):
    import onnxruntime as ort
    from base.detection import Detection
    from base.recognition import Recognition

    print(f"[model] detector: {DET_MODEL}")
    print(f"[model] recognizer: {REC_MODEL}")

    providers = ["CPUExecutionProvider"]
    provider_options = [{}]
    session_threads = max(1, int(getattr(args, "ep_threads", 1)))

    def provider_config(provider_name: str):
        if provider_name == "spacemit":
            ort.set_default_logger_severity(3)
            import spacemit_ort  # noqa: F401
            return [
                "SpaceMITExecutionProvider",
                "CPUExecutionProvider",
            ], [
                {
                    "SPACEMIT_EP_INTRA_THREAD_NUM": str(args.ep_threads),
                    "SPACEMIT_EP_INTRA_THREAD_AFFINITY": str(getattr(args, "cpu_affinity", DEFAULT_CPU_AFFINITY)),
                    "SPACEMIT_EP_INTER_THREAD_NUM": "1",
                    "SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD": "1",
                },
                {},
            ]
        return ["CPUExecutionProvider"], [{}]

    def make_session(model_path: Path, provider_name: str):
        options = ort.SessionOptions()
        options.intra_op_num_threads = session_threads
        options.inter_op_num_threads = 1
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        session_providers, session_provider_options = provider_config(provider_name)
        return ort.InferenceSession(
            str(model_path),
            sess_options=options,
            providers=session_providers,
            provider_options=session_provider_options,
        )

    # The official wrapper hardcodes CPUExecutionProvider in __init__.
    # Keep the official wrapper initialization path, then switch providers.
    det = Detection(str(DET_MODEL), conf_thres=getattr(args, "det_conf", DEFAULT_DET_CONF))
    det.sess = make_session(DET_MODEL, args.provider)

    def infer_face_with_conf(img_src):
        img = img_src.copy()
        img = det.process_img(img)
        model_infer = det.sess.run(None, {"images": img})
        pred = det.post_process(model_infer)
        pred = det.non_max_suppression_face(pred, det.conf_thres, 0.5)
        face_im = []
        boxes = []
        if pred is not None:
            for det_box in pred[0]:
                if len(det_box):
                    det_box[:4] = Detection.infer_face.__globals__["scale_coords"](img.shape[2:], det_box[:4], img_src.shape).round()
                    xyxy = det_box[:4]
                    x1, y1, x2, y2 = [int(v) for v in xyxy]
                    face_im.append(img_src[y1:y2, x1:x2])
                    det_conf = float(det_box[4]) if len(det_box) > 4 else 0.0
                    boxes.append(np.append(xyxy, det_conf))
            return face_im, boxes
        return None, None

    det.infer_face = infer_face_with_conf

    rec = Recognition(str(REC_MODEL), str(FACE_BANK))
    rec_provider = getattr(args, "rec_provider", None) or args.provider
    if rec_provider == "spacemit":
        rec._lazy_provider = rec_provider
        rec._lazy_make_session = make_session
        rec._lazy_model_path = REC_MODEL
        rec._lazy_loaded = False
        print("[model] recognizer providers: lazy SpaceMITExecutionProvider")
    else:
        rec.sess = make_session(REC_MODEL, rec_provider)
        rec._lazy_loaded = True
        print(f"[model] recognizer providers: {rec.sess.get_providers()}")

    print(f"[model] detector providers: {det.sess.get_providers()}")
    print(f"[model] ort intra_op_threads: {session_threads}")
    print(f"[face_bank] {len(rec.face_bank)} users: {', '.join(sorted(rec.face_bank)) or '(empty)'}")
    return det, rec


def push_latest(q: queue.Queue, value) -> None:
    try:
        while True:
            q.get_nowait()
    except queue.Empty:
        pass
    q.put_nowait(value)


def apply_control_command(parts: list[str], params: RuntimeParams, stop: dict[str, bool]) -> None:
    if not parts:
        return
    cmd = parts[0].lower()
    try:
        if cmd in ("q", "quit", "exit", "stop"):
            stop["value"] = True
        elif cmd in ("t", "threshold") and len(parts) == 2:
            value = max(0.1, min(0.95, float(parts[1])))
            with params.lock:
                params.threshold = value
            print(f"[control] threshold={value:.2f}")
        elif cmd in ("i", "interval") and len(parts) == 2:
            value = max(0.0, min(10.0, float(parts[1])))
            with params.lock:
                params.detect_interval = value
            print(f"[control] detect_interval={value:.1f}s")
        elif cmd == "fps" and len(parts) == 2:
            value = max(1.0, min(30.0, float(parts[1])))
            with params.lock:
                params.target_fps = value
            print(f"[control] target_fps={value:.1f}")
        elif cmd == "area" and len(parts) == 2:
            value = max(0.0, min(0.20, float(parts[1])))
            with params.lock:
                params.min_face_area_ratio = value
            print(f"[control] min_face_area_ratio={value:.3f}")
        elif cmd in ("b", "brightness") and len(parts) == 2:
            value = max(-80.0, min(120.0, float(parts[1])))
            with params.lock:
                params.brightness = value
            print(f"[control] brightness={value:.1f}")
        elif cmd in ("c", "contrast") and len(parts) == 2:
            value = max(0.5, min(2.5, float(parts[1])))
            with params.lock:
                params.contrast = value
            print(f"[control] contrast={value:.2f}")
        elif cmd in ("g", "gamma") and len(parts) == 2:
            value = max(0.2, min(3.0, float(parts[1])))
            with params.lock:
                params.gamma = value
            print(f"[control] gamma={value:.2f}")
        else:
            print("[control] unknown command")
    except ValueError:
        print("[control] bad value")


def live(args: argparse.Namespace) -> None:
    require_official_package()
    writer = getattr(args, "handoff_writer", None)
    if writer is None:
        writer = open_display(args)
    if writer is not None:
        transition = cv2.imread(str(Path(__file__).with_name("face_transition_mock.png")))
        if transition is None:
            transition = np.full((args.display_height, args.display_width, 3), (18, 22, 28), dtype=np.uint8)
            draw_texts(transition, [("正在打开摄像头", (42, args.display_height // 2 - 24), (235, 235, 235), 36), ("正在加载模型和摄像头", (42, args.display_height // 2 + 32), (190, 205, 220), 26)])
        elif transition.shape[:2] != (args.display_height, args.display_width):
            transition = cv2.resize(transition, (args.display_width, args.display_height), interpolation=cv2.INTER_AREA)
        writer.write(transition)
    apply_cpu_affinity(getattr(args, "cpu_affinity", DEFAULT_CPU_AFFINITY))
    det, rec = load_models(args)
    users = sorted(rec.face_bank)
    params = RuntimeParams(
        threshold=args.threshold,
        detect_interval=args.detect_interval,
        target_fps=args.target_fps,
        min_face_area_ratio=args.min_face_area_ratio,
        brightness=args.brightness,
        contrast=args.contrast,
        gamma=args.gamma,
        lock=Lock(),
    )
    stats = LiveStats(last_report_ts=time.time())

    stop = {"value": False}

    def _stop(_signum, _frame):
        stop["value"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    frame_q: queue.Queue[np.ndarray] = queue.Queue(maxsize=1)
    result_q: queue.Queue[tuple[float, list[FaceResult], float]] = queue.Queue(maxsize=1)
    use_async_infer = getattr(args, "async_infer", True)

    def worker() -> None:
        while not stop["value"]:
            try:
                frame = frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                with params.lock:
                    threshold = params.threshold
                    min_face_area_ratio = params.min_face_area_ratio
                infer_start = time.perf_counter()
                results = recognize_faces(det, rec, frame, threshold, min_face_area_ratio, getattr(args, "main_face_only", False), getattr(args, "verify_center_gate", True), getattr(args, "center_tolerance", 0.08))
                infer_ms = (time.perf_counter() - infer_start) * 1000.0
                stats.inference_jobs += 1
                stats.inference_faces += len(results)
                stats.inference_time_ms_total += infer_ms
                push_latest(result_q, (time.time(), results, infer_ms))
            except Exception as exc:
                print(f"[worker] {exc}", file=sys.stderr)

    def command_worker() -> None:
        print(f"[control] commands: t 0.65 | i 1.5 | fps 8 | area 0.02 | b 18 | c 1.1 | g 0.75 | q")
        print(f"[control] file: {args.control_file}")
        while not stop["value"]:
            line = sys.stdin.readline()
            if not line:
                time.sleep(0.2)
                continue
            apply_control_command(line.strip().split(), params, stop)

    import threading

    thread = None
    if use_async_infer:
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    control_thread = threading.Thread(target=command_worker, daemon=True)
    if args.stdin_control:
        control_thread.start()

    cap = open_camera(args)
    last_results: list[FaceResult] = []
    last_infer_ms = 0.0
    start_time = time.time()
    last_frame_time = start_time
    fps = 0.0
    status = "starting"
    success_count = 0
    success_result: FaceResult | None = None
    last_match_line = "no result"
    control_path = Path(args.control_file)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.touch(exist_ok=True)
    last_control_mtime = control_path.stat().st_mtime

    try:
        while not stop["value"]:
            loop_start = time.time()
            if args.duration > 0 and time.time() - start_time >= args.duration:
                break
            frame = read_bgr(cap, args.width, args.height)
            if frame is None:
                time.sleep(0.05)
                continue
            if getattr(args, "mirror", True):
                frame = cv2.flip(frame, 1)

            now = time.time()
            try:
                control_mtime = control_path.stat().st_mtime
                if control_mtime > last_control_mtime:
                    last_control_mtime = control_mtime
                    lines = [line.strip() for line in control_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                    if lines:
                        apply_control_command(lines[-1].split(), params, stop)
            except Exception as exc:
                print(f"[control] file read failed: {exc}", file=sys.stderr)

            dt = now - last_frame_time
            last_frame_time = now
            if dt > 0:
                fps = fps * 0.85 + (1.0 / dt) * 0.15

            temp_c = refresh_thermal(stats, now, args.thermal_check_interval)
            if temp_c is not None and temp_c >= args.max_temp_c:
                print(f"[safety] temperature {temp_c:.1f}C >= {args.max_temp_c:.1f}C, stopping", file=sys.stderr)
                break

            with params.lock:
                target_fps = params.target_fps
                brightness = params.brightness
                contrast = params.contrast
                gamma = params.gamma

            frame = enhance_frame(frame, brightness, contrast, gamma)

            if frame_q.empty() and (params.detect_interval <= 0 or now - getattr(stats, "last_submit_ts", 0.0) >= params.detect_interval):
                stats.last_submit_ts = now
                try:
                    if use_async_infer:
                        frame_q.put_nowait(frame.copy())
                    else:
                        with params.lock:
                            threshold = params.threshold
                            min_face_area_ratio = params.min_face_area_ratio
                        infer_start = time.perf_counter()
                        results = recognize_faces(det, rec, frame.copy(), threshold, min_face_area_ratio, getattr(args, "main_face_only", False), getattr(args, "verify_center_gate", True), getattr(args, "center_tolerance", 0.08))
                        infer_ms = (time.perf_counter() - infer_start) * 1000.0
                        stats.inference_jobs += 1
                        stats.inference_faces += len(results)
                        stats.inference_time_ms_total += infer_ms
                        push_latest(result_q, (time.time(), results, infer_ms))
                except queue.Full:
                    pass

            stats.preview_frames += 1

            try:
                ts, last_results, last_infer_ms = result_q.get_nowait()
                status = f"detected {len(last_results)}" if last_results else "searching"
                matched = best_success_result(last_results, getattr(args, "expected_name", ""))
                if last_results:
                    top = max(last_results, key=lambda item: item.score)
                    last_match_line = f"name_npy={top.name}.npy score={top.score:.3f} known={int(top.known)} det={top.det_score:.3f}"
                    print(f"[match] {last_match_line}")
                else:
                    last_match_line = "no_face"
                    print("[match] no_face")
                if matched is not None:
                    if success_result is not None and success_result.name == matched.name:
                        success_count += 1
                    else:
                        success_count = 1
                    success_result = matched
                    status = f"验证成功 {matched.name} {success_count}/{args.success_stable_frames}"
                    write_result_json(
                        args.result_json,
                        {
                            "ok": success_count >= args.success_stable_frames,
                            "status": "recognized",
                            "name": matched.name,
                            "score": matched.score,
                            "expected_name": getattr(args, "expected_name", ""),
                            "threshold": params.threshold,
                            "box": list(matched.box),
                            "stable_count": success_count,
                            "stable_required": args.success_stable_frames,
                            "elapsed_s": round(time.time() - start_time, 3),
                        },
                    )
                    if getattr(args, "exit_on_success", False) and success_count >= args.success_stable_frames:
                        print(f"[live] recognized: {matched.name} score={matched.score:.3f}")
                        break
                else:
                    success_count = 0
                    success_result = None
                    write_result_json(
                        args.result_json,
                        {
                            "ok": False,
                            "status": "searching" if last_results else "no_face",
                            "name": "",
                            "score": 0.0,
                            "expected_name": getattr(args, "expected_name", ""),
                            "threshold": params.threshold,
                            "box": [],
                            "stable_count": 0,
                            "stable_required": args.success_stable_frames,
                            "elapsed_s": round(time.time() - start_time, 3),
                        },
                    )
            except queue.Empty:
                pass

            elapsed = max(now - start_time, 1e-6)
            preview_fps = stats.preview_frames / elapsed
            inference_fps = stats.inference_jobs / elapsed
            if now - stats.last_report_ts >= 1.0:
                avg_infer_ms = stats.inference_time_ms_total / stats.inference_jobs if stats.inference_jobs else 0.0
                print(
                    f"[fps] preview={preview_fps:.2f} infer={inference_fps:.2f} "
                    f"avg_infer_ms={avg_infer_ms:.1f} faces={stats.inference_faces} temp={temp_c if temp_c is not None else 'n/a'} match={last_match_line}"
                )
                stats.last_report_ts = now

            if temp_c is not None:
                status = f"{status} | {temp_c:.0f}C"

            if writer is not None or args.display != "none":
                annotated = annotate_live_fast(frame, last_results, fps, users, args, status, params, inference_fps, last_infer_ms)
                if writer is not None:
                    writer.write(annotated)
                else:
                    cv2.imshow("Smart Medicine Face ID - IMX219", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        break

            if target_fps > 0:
                delay = (1.0 / target_fps) - (time.time() - loop_start)
                if delay > 0:
                    time.sleep(delay)

        print("[live] stopping")
    finally:
        stop["value"] = True
        if thread is not None:
            thread.join(timeout=3.0)
        cap.release()
        if writer is not None:
            writer.release()
        if args.display != "none" and writer is None:
            cv2.destroyAllWindows()


def list_faces(_args: argparse.Namespace) -> None:
    require_official_package()
    users = sorted(p.stem for p in FACE_BANK.glob("*.npy"))
    print(f"[face_bank] {FACE_BANK}")
    if not users:
        print("  (empty)")
        return
    for name in users:
        vec = np.load(FACE_BANK / f"{name}.npy")
        print(f"  - {name}: {vec.shape}")


def face_bank_lines() -> list[str]:
    require_official_package()
    users = sorted(p.stem for p in FACE_BANK.glob("*.npy"))
    if not users:
        return ["人脸库为空"]
    return [f"{idx + 1}. {name}" for idx, name in enumerate(users)]


def make_mode_args(command: str, source: argparse.Namespace, name: str | None = None) -> argparse.Namespace:
    argv = [command]
    if command == "register":
        argv.append(name or "__menu_name__")
    args = build_parser().parse_args(argv)
    for key in [
        "camera_json",
        "width",
        "height",
        "display_width",
        "display_height",
        "display",
        "threshold",
        "det_conf",
        "target_fps",
        "provider",
        "ep_threads",
        "min_face_area_ratio",
        "max_temp_c",
        "thermal_check_interval",
        "brightness",
        "contrast",
        "gamma",
        "mirror",
    ]:
        if hasattr(source, key):
            setattr(args, key, getattr(source, key))
    if hasattr(args, "detect_interval"):
        args.detect_interval = getattr(source, "detect_interval", DEFAULT_DETECT_INTERVAL)
    if hasattr(args, "thermal_check_interval"):
        args.thermal_check_interval = getattr(source, "thermal_check_interval", 2.0)
    if hasattr(args, "stdin_control"):
        args.stdin_control = False
    if hasattr(args, "control_file"):
        args.control_file = DEFAULT_CONTROL_FILE
    if command == "register":
        args.name = name or args.name
        args.output_dir = str(PROJECT_ROOT / "data" / "face-output")
        args.samples = getattr(source, "samples", 5)
        args.sample_interval = getattr(source, "sample_interval", 0.8)
        args.max_seconds = getattr(source, "max_seconds", 40.0)
        args.stable_frames = getattr(source, "stable_frames", 3)
        args.register_min_face_area_ratio = getattr(source, "register_min_face_area_ratio", 0.035)
        args.register_max_face_area_ratio = getattr(source, "register_max_face_area_ratio", 0.30)
        args.center_tolerance = getattr(source, "center_tolerance", 0.08)
        args.sharpness_threshold = getattr(source, "sharpness_threshold", 45.0)
        args.face_brightness_min = getattr(source, "face_brightness_min", 45.0)
        args.face_brightness_max = getattr(source, "face_brightness_max", 235.0)
        args.register_any_face = getattr(source, "register_any_face", False)
    return args


def render_menu(args: argparse.Namespace, message: str) -> np.ndarray:
    frame = np.full((args.display_height, args.display_width, 3), (236, 238, 232), dtype=np.uint8)
    cv2.rectangle(frame, (0, 0), (args.display_width, 72), (22, 30, 36), -1)
    cv2.rectangle(frame, (0, args.display_height - 56), (args.display_width, args.display_height), (22, 30, 36), -1)
    cv2.rectangle(frame, (32, 104), (args.display_width - 32, args.display_height - 82), (255, 255, 255), -1)
    cv2.rectangle(frame, (32, 104), (args.display_width - 32, args.display_height - 82), (180, 188, 180), 2)

    lines: list[tuple[str, tuple[int, int], tuple[int, int, int], int]] = [
        ("智能药盒 - 官方人脸识别", (32, 18), (245, 245, 245), 34),
        ("1. 实时识别人脸", (72, 138), (20, 90, 50), 34),
        ("2. 录入人脸", (72, 190), (20, 90, 50), 34),
        ("3. 查看已录入的人脸信息", (72, 242), (20, 90, 50), 34),
        ("4. 退出菜单", (72, 294), (120, 45, 45), 34),
        (f"阈值 {args.threshold:.2f} | FPS {args.target_fps:.1f} | 识别间隔 {getattr(args, 'detect_interval', DEFAULT_DETECT_INTERVAL):.1f}s", (72, 360), (45, 55, 65), 24),
        ("远程控制: echo \"live\" / \"register 张三\" / \"list\" / \"quit\" > /tmp/smart_medicine_face/menu_control.txt", (32, args.display_height - 40), (235, 235, 235), 19),
    ]
    y = 418
    lines.append(("当前人脸库:", (72, y), (45, 55, 65), 26))
    for line in face_bank_lines()[:6]:
        y += 36
        lines.append((line, (96, y), (55, 65, 75), 24))
    if message:
        lines.append((message, (72, args.display_height - 120), (40, 70, 180), 24))
    return draw_texts(frame, lines)


def display_menu(args: argparse.Namespace) -> None:
    require_official_package()
    control_path = Path(args.menu_control_file)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text("", encoding="utf-8")
    last_mtime = control_path.stat().st_mtime
    writer = open_display(args)
    message = "菜单已启动"

    try:
        while True:
            frame = render_menu(args, message)
            if writer is not None:
                writer.write(frame)
            elif args.display != "none":
                cv2.imshow("Smart Medicine Face Menu", frame)
                key = cv2.waitKey(200) & 0xFF
                if key == ord("1"):
                    live(make_mode_args("live", args))
                    message = "已返回菜单"
                elif key == ord("2"):
                    message = "录入请用远程命令: register 名字"
                elif key == ord("3"):
                    message = "已刷新人脸库"
                elif key in (ord("4"), ord("q"), 27):
                    break
            else:
                time.sleep(0.2)

            try:
                mtime = control_path.stat().st_mtime
                if mtime <= last_mtime:
                    continue
                last_mtime = mtime
                lines = [line.strip() for line in control_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if not lines:
                    continue
                parts = lines[-1].split(maxsplit=1)
                cmd = parts[0].lower()
                value = parts[1].strip() if len(parts) > 1 else ""
                if cmd in ("live", "1"):
                    if writer is not None:
                        writer.release()
                        writer = None
                    live(make_mode_args("live", args))
                    writer = open_display(args)
                    message = "实时识别已停止，返回菜单"
                elif cmd in ("register", "2"):
                    if not value:
                        message = "录入失败: 缺少名字，例如 register 张三"
                        continue
                    if writer is not None:
                        writer.release()
                        writer = None
                    register(make_mode_args("register", args, value))
                    writer = open_display(args)
                    message = f"已录入: {value}"
                elif cmd in ("list", "3"):
                    message = "已刷新人脸库"
                elif cmd in ("quit", "exit", "stop", "4"):
                    break
                else:
                    message = f"未知命令: {lines[-1]}"
            except Exception as exc:
                message = f"菜单命令失败: {exc}"

            time.sleep(max(0.1, 1.0 / max(1.0, args.target_fps)))
    finally:
        if writer is not None:
            writer.release()
        if args.display != "none" and writer is None:
            cv2.destroyAllWindows()


def touch_device_candidates(path: str) -> list[str]:
    if path != "auto":
        return [path]
    candidates: list[str] = []
    try:
        text = Path("/proc/bus/input/devices").read_text(encoding="utf-8", errors="ignore")
    except Exception:
        text = ""
    blocks = [block for block in text.split("\n\n") if block.strip()]
    preferred_names = ("USB2IIC_CTP_CONTROL", "fts_ts", "touch", "Touch")
    for block in blocks:
        if not any(name in block for name in preferred_names):
            continue
        for line in block.splitlines():
            if "Handlers=" not in line:
                continue
            for token in line.split():
                if token.startswith("event"):
                    candidates.append(f"/dev/input/{token}")
    for path_obj in sorted(Path("/dev/input").glob("event*")):
        candidate = str(path_obj)
        if candidate not in candidates:
            candidates.append(candidate)
    return candidates


def open_touch_device(path: str):
    last_error = None
    for candidate in touch_device_candidates(path):
        try:
            fd = os.open(candidate, os.O_RDONLY | os.O_NONBLOCK)
        except OSError as exc:
            last_error = exc
            continue
        print(f"[touch] using {candidate}")
        return fd
    print(f"[touch] open failed: {last_error}", file=sys.stderr)
    return None


def read_touch_trigger(fd) -> bool:
    if fd is None:
        return False
    try:
        ready, _, _ = select.select([fd], [], [], 0)
    except Exception:
        return False
    if not ready:
        return False

    triggered = False
    event_size = struct.calcsize("llHHI")
    try:
        data = os.read(fd, event_size * 32)
    except BlockingIOError:
        return False
    except OSError:
        return False

    for offset in range(0, len(data) - event_size + 1, event_size):
        _sec, _usec, ev_type, code, value = struct.unpack_from("llHHI", data, offset)
        if ev_type == EV_KEY and code == BTN_TOUCH and value == 1:
            triggered = True
        elif ev_type == EV_ABS and code in (ABS_X, ABS_Y, ABS_MT_POSITION_X, ABS_MT_POSITION_Y):
            triggered = True
    return triggered


def _touch_abs_range(fd, code: int) -> tuple[int, int] | None:
    if fd is None:
        return None
    buf = bytearray(struct.calcsize("iiiiii"))
    try:
        fcntl.ioctl(fd, _ior(ord("E"), 0x40 + code, len(buf)), buf, True)
    except OSError:
        return None
    _value, minimum, maximum, _fuzz, _flat, _resolution = struct.unpack("iiiiii", buf)
    if maximum <= minimum:
        return None
    return minimum, maximum


def read_touch_action(fd, display_width: int) -> str | None:
    if fd is None:
        return None
    try:
        ready, _, _ = select.select([fd], [], [], 0)
    except Exception:
        return None
    if not ready:
        return None

    event_size = struct.calcsize("llHHI")
    try:
        data = os.read(fd, event_size * 64)
    except (BlockingIOError, OSError):
        return None

    touched = False
    x_value: int | None = None
    x_code: int | None = None
    for offset in range(0, len(data) - event_size + 1, event_size):
        _sec, _usec, ev_type, code, value = struct.unpack_from("llHHI", data, offset)
        if ev_type == EV_KEY and code == BTN_TOUCH and value == 1:
            touched = True
        elif ev_type == EV_ABS and code in (ABS_X, ABS_MT_POSITION_X):
            touched = True
            x_value = int(value)
            x_code = int(code)
        elif ev_type == EV_ABS and code in (ABS_Y, ABS_MT_POSITION_Y):
            touched = True

    if not touched:
        return None
    if x_value is None or x_code is None:
        return "register"
    abs_range = _touch_abs_range(fd, x_code)
    if abs_range is not None:
        min_x, max_x = abs_range
        display_x = (x_value - min_x) * display_width / max(1, max_x - min_x)
    else:
        display_x = float(x_value)
    return "verify" if display_x >= display_width / 2 else "register"


def render_touch_register(args: argparse.Namespace, message: str) -> np.ndarray:
    frame = np.full((args.display_height, args.display_width, 3), (236, 238, 232), dtype=np.uint8)
    cv2.rectangle(frame, (0, 0), (args.display_width, 72), (22, 30, 36), -1)
    gap = 28
    left = (52, 132, args.display_width // 2 - gap, args.display_height - 104)
    right = (args.display_width // 2 + gap, 132, args.display_width - 52, args.display_height - 104)
    cv2.rectangle(frame, (left[0], left[1]), (left[2], left[3]), (30, 120, 72), -1)
    cv2.rectangle(frame, (left[0], left[1]), (left[2], left[3]), (18, 78, 45), 4)
    cv2.rectangle(frame, (right[0], right[1]), (right[2], right[3]), (42, 92, 170), -1)
    cv2.rectangle(frame, (right[0], right[1]), (right[2], right[3]), (24, 56, 116), 4)
    lines = [
        ("智能药盒 - 人脸测试", (32, 18), (245, 245, 245), 34),
        ("录入", (left[0] + 180, left[1] + 120), (255, 255, 255), 58),
        ("开始采集人脸", (left[0] + 128, left[1] + 200), (236, 250, 236), 30),
        ("验证", (right[0] + 180, right[1] + 120), (255, 255, 255), 58),
        ("进入识别预览", (right[0] + 128, right[1] + 200), (235, 244, 255), 30),
        (f"姓名: {args.register_name}", (64, args.display_height - 58), (45, 55, 65), 24),
    ]
    if message:
        lines.append((message, (64, 88), (40, 70, 180), 24))
    return draw_texts(frame, lines)


def touch_register_ui(args: argparse.Namespace) -> None:
    def _show_transition(title: str, subtitle: str = "请稍候") -> None:
        if writer is not None:
            frame = np.full((args.display_height, args.display_width, 3), (18, 22, 28), dtype=np.uint8)
            lines = [
                ("智能药盒 - 人脸识别", (32, 18), (245, 245, 245), 34),
                (title, (72, args.display_height // 2 - 48), (245, 245, 245), 42),
                (subtitle, (72, args.display_height // 2 + 16), (190, 205, 220), 28),
            ]
            writer.write(draw_texts(frame, lines))

    require_official_package()
    writer = open_display(args)
    touch_fd = open_touch_device(args.touch_device)
    message = "等待触摸"
    control_path = Path(args.touch_control_file)
    control_path.parent.mkdir(parents=True, exist_ok=True)
    control_path.write_text("", encoding="utf-8")
    last_control_mtime = control_path.stat().st_mtime

    def run_register_flow() -> bool:
        nonlocal writer, message
        try:
            reg_args = make_mode_args("register", args, args.register_name)
            message = "正在进入录入预览"
            _show_transition("正在进入录入预览", "请正对摄像头")
            if writer is not None:
                reg_args.handoff_writer = writer
                writer = None
            register(reg_args)
        except Exception as exc:
            message = f"录入失败: {exc}"
            print(f"[touch-register] {message}", file=sys.stderr)
            if writer is not None:
                writer.release()
            writer = open_display(args)
            return False
        if getattr(args, "auto_live", False):
            message = "录入完成，进入实时识别"
            live_args = make_mode_args("live", args)
            live_args.duration = getattr(args, "live_duration", 0.0)
            live(live_args)
            return True
        writer = open_display(args)
        message = "录入完成，可再次触摸重新录入"
        return False

    def run_verify_flow() -> bool:
        nonlocal writer, message
        live_args = make_mode_args("live", args)
        message = "正在进入验证预览"
        _show_transition("正在进入验证预览", "请正对摄像头")
        if writer is not None:
            live_args.handoff_writer = writer
            writer = None
        live_args.duration = getattr(args, "live_duration", 0.0)
        live_args.det_conf = max(getattr(args, "det_conf", 0.25), 0.30)
        live_args.min_face_area_ratio = max(getattr(args, "min_face_area_ratio", 0.0), 0.025)
        live_args.main_face_only = True
        live_args.exit_on_success = True
        live_args.success_stable_frames = 2
        live_args.result_json = "/tmp/smart_medicine_face/face_verify_result.json"
        live(live_args)
        writer = open_display(args)
        message = "验证结束，返回按钮页"
        return False
    try:
        while True:
            try:
                control_mtime = control_path.stat().st_mtime
                if control_mtime > last_control_mtime:
                    last_control_mtime = control_mtime
                    lines = [line.strip().lower() for line in control_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                    if lines:
                        cmd = lines[-1].split()[0]
                        if cmd in ("verify", "live", "v", "2"):
                            if run_verify_flow():
                                break
                        elif cmd in ("register", "enroll", "r", "1"):
                            if run_register_flow():
                                break
                        elif cmd in ("quit", "exit", "stop", "q"):
                            break
            except Exception as exc:
                message = f"控制命令失败: {exc}"

            frame = render_touch_register(args, message)
            if writer is not None:
                writer.write(frame)
            elif args.display != "none":
                cv2.imshow("Smart Medicine Face Register", frame)
                key = cv2.waitKey(100) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key in (ord(" "), ord("\r")):
                    if run_register_flow():
                        break
                if key in (ord("v"), ord("V")):
                    if run_verify_flow():
                        break

            action = read_touch_action(touch_fd, args.display_width)
            if action == "verify":
                if run_verify_flow():
                    break
            elif action == "register":
                if run_register_flow():
                    break

            time.sleep(max(0.05, 1.0 / max(1.0, args.target_fps)))
    finally:
        if touch_fd is not None:
            os.close(touch_fd)
        if writer is not None:
            writer.release()
        if args.display != "none" and writer is None:
            cv2.destroyAllWindows()


def register(args: argparse.Namespace) -> None:
    require_official_package()
    writer = getattr(args, "handoff_writer", None)
    if writer is None:
        writer = open_display(args)
    if writer is not None:
        transition = cv2.imread(str(Path(__file__).with_name("face_transition_mock.png")))
        if transition is None:
            transition = np.full((args.display_height, args.display_width, 3), (18, 22, 28), dtype=np.uint8)
            draw_texts(transition, [("Opening camera", (42, args.display_height // 2 - 24), (235, 235, 235), 36), ("Loading models and camera", (42, args.display_height // 2 + 32), (190, 205, 220), 26)])
        elif transition.shape[:2] != (args.display_height, args.display_width):
            transition = cv2.resize(transition, (args.display_width, args.display_height), interpolation=cv2.INTER_AREA)
        writer.write(transition)
    apply_cpu_affinity(getattr(args, "cpu_affinity", DEFAULT_CPU_AFFINITY))
    det, rec = load_models(args)
    cap = open_camera(args)
    samples: list[np.ndarray] = []
    crops_dir = Path(args.output_dir)
    crops_dir.mkdir(parents=True, exist_ok=True)
    start_time = time.time()
    last_sample = 0.0
    last_detect = 0.0
    last_report = start_time
    preview_frames = 0
    detect_frames = 0
    infer_ms_ema = 0.0
    last_captured_count = 0
    last_capture_time = 0.0
    stable_count = 0
    capture_started = False
    last_quality = RegisterQuality(False, "Align face to camera", None, None)
    face_imgs: list[np.ndarray] = []
    boxes = None
    sample_lock = Lock()
    infer_lock = Lock()
    sample_jobs: queue.Queue[tuple[np.ndarray, np.ndarray] | None] = queue.Queue(maxsize=1)
    sample_errors: list[str] = []
    raw_frame_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=1)
    display_q: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=1)
    camera_stop = Event()
    display_stop = Event()

    def collected_count() -> int:
        with sample_lock:
            return len(samples)

    def enqueue_sample(face_img: np.ndarray, sample_box: tuple[int, int, int, int], source_frame: np.ndarray) -> bool:
        nonlocal last_sample
        if sample_jobs.full() or collected_count() >= args.samples:
            return False
        x1, y1, x2, y2 = sample_box
        crop = source_frame[y1:y2, x1:x2].copy()
        try:
            sample_jobs.put_nowait((face_img.copy(), crop))
        except queue.Full:
            return False
        last_sample = time.time()
        return True

    def sample_worker() -> None:
        nonlocal last_captured_count, last_capture_time
        while True:
            item = sample_jobs.get()
            try:
                if item is None:
                    return
                face_img, crop = item
                with infer_lock:
                    vec = rec.infer(face_img)
                with sample_lock:
                    if len(samples) >= args.samples:
                        continue
                    sample_no = len(samples) + 1
                    samples.append(vec)
                    last_captured_count = len(samples)
                cv2.imwrite(str(crops_dir / f"register_{args.name}_{sample_no:02d}.jpg"), crop)
                last_capture_time = time.time()
                print(f"[register] sample {sample_no}/{args.samples}")
            except Exception as exc:
                sample_errors.append(str(exc))
                print(f"[register] sample worker error: {exc}", file=sys.stderr)
            finally:
                sample_jobs.task_done()

    def camera_worker() -> None:
        while not camera_stop.is_set():
            frame = read_bgr(cap, args.width, args.height)
            if frame is None:
                time.sleep(0.01)
                continue
            if getattr(args, "mirror", True):
                frame = cv2.flip(frame, 1)
            push_latest(raw_frame_q, frame)

    def display_worker() -> None:
        while not display_stop.is_set():
            try:
                frame = display_q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                if frame is None:
                    return
                if writer is not None:
                    writer.write(frame)
                elif args.display != "none":
                    cv2.imshow("Smart Medicine Face ID - register", frame)
                    if cv2.waitKey(1) & 0xFF in (ord("q"), 27):
                        display_stop.set()
                        camera_stop.set()
                        return
            finally:
                display_q.task_done()

    worker = Thread(target=sample_worker, daemon=True)
    worker.start()
    camera_thread = Thread(target=camera_worker, daemon=True)
    camera_thread.start()
    display_thread = Thread(target=display_worker, daemon=True)
    display_thread.start()

    try:
        while collected_count() < args.samples and time.time() - start_time < args.max_seconds:
            try:
                pending_frame = raw_frame_q.get(timeout=0.2)
            except queue.Empty:
                continue
            if pending_frame is None:
                break
            frame = enhance_frame(pending_frame, args.brightness, args.contrast, args.gamma)
            preview_frames += 1
            now = time.time()
            should_detect = args.detect_interval <= 0 or now - last_detect >= args.detect_interval
            detection_fresh = False
            if should_detect:
                infer_start = time.perf_counter()
                if infer_lock.acquire(blocking=False):
                    try:
                        face_imgs, boxes = det.infer_face(frame)
                        detection_fresh = True
                    finally:
                        infer_lock.release()
                else:
                    face_imgs = []
                    boxes = None
                if detection_fresh:
                    last_quality = evaluate_register_quality(frame, boxes, args)
                    if getattr(args, "register_any_face", False) and face_imgs and boxes is not None:
                        best: tuple[int, int, tuple[int, int, int, int]] | None = None
                        for idx, box in enumerate(boxes):
                            x1, y1, x2, y2 = [int(v) for v in box[:4]]
                            x1 = max(0, min(x1, frame.shape[1] - 1))
                            y1 = max(0, min(y1, frame.shape[0] - 1))
                            x2 = max(0, min(x2, frame.shape[1] - 1))
                            y2 = max(0, min(y2, frame.shape[0] - 1))
                            if x2 <= x1 or y2 <= y1:
                                continue
                            clipped = (x1, y1, x2, y2)
                            area = (x2 - x1) * (y2 - y1)
                            if best is None or area > best[0]:
                                best = (area, idx, clipped)
                        if best is None and face_imgs:
                            best = (frame.shape[0] * frame.shape[1], 0, (0, 0, frame.shape[1], frame.shape[0]))
                        if best is not None:
                            last_quality = RegisterQuality(True, "Face detected, collecting", best[2], best[1])
                    if last_quality.ok:
                        stable_count += 1
                    else:
                        stable_count = 0
                        capture_started = False
                    if not capture_started and stable_count >= args.stable_frames:
                        capture_started = True
                        last_sample = 0.0
                        print(f"[register] capture phase started after {stable_count} stable detections")
                    elapsed_ms = (time.perf_counter() - infer_start) * 1000.0
                    infer_ms_ema = elapsed_ms if infer_ms_ema <= 0 else infer_ms_ema * 0.85 + elapsed_ms * 0.15
                    detect_frames += 1
                    last_detect = now
            if (
                capture_started
                and last_quality.ok
                and last_quality.face_index is not None
                and time.time() - last_sample >= args.sample_interval
                and detection_fresh
            ):
                idx = last_quality.face_index
                clipped = last_quality.box
                x1, y1, x2, y2 = clipped or (0, 0, frame.shape[1], frame.shape[0])
                enqueue_sample(face_imgs[idx], (x1, y1, x2, y2), frame)
            if now - last_report >= 2.0:
                dt = max(1e-6, now - last_report)
                faces_seen = len(face_imgs) if face_imgs else 0
                print(f"[register-fps] preview={preview_frames / dt:.2f} detect={detect_frames / dt:.2f} avg_infer_ms={infer_ms_ema:.1f} faces={faces_seen} samples={collected_count()}/{args.samples}")
                preview_frames = 0
                detect_frames = 0
                last_report = now
            annotated = annotate_register_fast(
                frame,
                args,
                last_quality,
                stable_count,
                collected_count(),
                args.samples,
                infer_ms_ema,
                last_captured_count,
                time.time() - last_capture_time,
            )
            push_latest(display_q, annotated)

        sample_jobs.join()
        if collected_count() < 2:
            raise RuntimeError(f"only collected {collected_count()} valid face samples")
        if sample_errors:
            print(f"[register] sample worker errors: {sample_errors[-3:]}", file=sys.stderr)
        avg = np.mean(samples, axis=0)
        avg = avg / (np.linalg.norm(avg) + 1e-12)
        FACE_BANK.mkdir(parents=True, exist_ok=True)
        out_path = FACE_BANK / f"{args.name}.npy"
        np.save(out_path, avg)
        print(f"[register] saved: {out_path}")
    finally:
        try:
            sample_jobs.put_nowait(None)
        except queue.Full:
            pass
        display_stop.set()
        camera_stop.set()
        try:
            raw_frame_q.put_nowait(None)
        except queue.Full:
            pass
        try:
            display_q.put_nowait(None)
        except queue.Full:
            pass
        camera_thread.join(timeout=3.0)
        display_thread.join(timeout=3.0)
        cap.release()
        if writer is not None:
            writer.release()
        if args.display != "none" and writer is None:
            cv2.destroyAllWindows()

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smart Medicine live face recognition on IMX219")
    sub = parser.add_subparsers(dest="command")

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--camera-json", default=IMX219_JSON)
        p.add_argument("--width", type=int, default=CAM_W)
        p.add_argument("--height", type=int, default=CAM_H)
        p.add_argument("--display-width", type=int, default=DISPLAY_W)
        p.add_argument("--display-height", type=int, default=DISPLAY_H)
        p.add_argument("--display", choices=["gst", "gst-gl", "opencv", "none"], default="gst")
        p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
        p.add_argument("--det-conf", type=float, default=DEFAULT_DET_CONF)
        p.add_argument("--target-fps", type=float, default=DEFAULT_TARGET_FPS)
        p.add_argument("--provider", choices=["spacemit", "cpu"], default="spacemit")
        p.add_argument("--rec-provider", choices=["spacemit", "cpu"], default=None)
        p.add_argument("--ep-threads", type=int, default=1)
        p.add_argument("--cpu-affinity", default=DEFAULT_CPU_AFFINITY)
        p.add_argument("--min-face-area-ratio", type=float, default=DEFAULT_MIN_FACE_AREA_RATIO)
        p.add_argument("--max-temp-c", type=float, default=DEFAULT_MAX_TEMP_C)
        p.add_argument("--thermal-check-interval", type=float, default=2.0)
        p.add_argument("--brightness", type=float, default=0.0)
        p.add_argument("--contrast", type=float, default=1.0)
        p.add_argument("--gamma", type=float, default=1.0)
        p.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=True)

    live_p = sub.add_parser("live")
    add_common(live_p)
    live_p.add_argument("--detect-interval", type=float, default=DEFAULT_DETECT_INTERVAL)
    live_p.add_argument("--duration", type=float, default=0.0, help="seconds; 0 means run until stopped")
    live_p.add_argument("--stdin-control", action=argparse.BooleanOptionalAction, default=True)
    live_p.add_argument("--control-file", default=DEFAULT_CONTROL_FILE)
    live_p.add_argument("--exit-on-success", action="store_true", help="exit when a known face is recognized")
    live_p.add_argument("--expected-name", default="", help="only accept this registered user name")
    live_p.add_argument("--success-stable-frames", type=int, default=2, help="consecutive successful recognitions before accepting")
    live_p.add_argument("--main-face-only", action=argparse.BooleanOptionalAction, default=True)
    live_p.add_argument("--verify-center-gate", action=argparse.BooleanOptionalAction, default=True, help="only match faces fully inside the center guide frame")
    live_p.add_argument("--center-tolerance", type=float, default=0.08)
    live_p.add_argument("--async-infer", action=argparse.BooleanOptionalAction, default=True)
    live_p.add_argument("--result-json", default="/tmp/smart_medicine_face/face_verify_result.json")
    live_p.set_defaults(func=live)

    reg_p = sub.add_parser("register")
    add_common(reg_p)
    reg_p.add_argument("name")
    reg_p.add_argument("--samples", type=int, default=5)
    reg_p.add_argument("--detect-interval", type=float, default=1.0)
    reg_p.add_argument("--sample-interval", type=float, default=1.0)
    reg_p.add_argument("--max-seconds", type=float, default=40.0)
    reg_p.add_argument("--output-dir", default="~/smart_medicine/vision/output")
    reg_p.add_argument("--stable-frames", type=int, default=2)
    reg_p.add_argument("--register-min-face-area-ratio", type=float, default=0.035)
    reg_p.add_argument("--register-max-face-area-ratio", type=float, default=0.30)
    reg_p.add_argument("--center-tolerance", type=float, default=0.08)
    reg_p.add_argument("--sharpness-threshold", type=float, default=45.0)
    reg_p.add_argument("--face-brightness-min", type=float, default=45.0)
    reg_p.add_argument("--face-brightness-max", type=float, default=235.0)
    reg_p.add_argument("--register-any-face", action="store_true", help="collect samples from the largest detected face without quality gates")
    reg_p.set_defaults(func=register)

    list_p = sub.add_parser("list")
    list_p.set_defaults(func=list_faces)

    menu_p = sub.add_parser("menu")
    menu_p.set_defaults(func=menu)

    display_menu_p = sub.add_parser("display-menu")
    display_menu_p.add_argument("--display-width", type=int, default=DISPLAY_W)
    display_menu_p.add_argument("--display-height", type=int, default=DISPLAY_H)
    display_menu_p.add_argument("--display", choices=["gst", "gst-gl", "opencv", "none"], default="gst")
    display_menu_p.add_argument("--target-fps", type=float, default=2.0)
    display_menu_p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    display_menu_p.add_argument("--detect-interval", type=float, default=2.0)
    display_menu_p.add_argument("--camera-json", default=IMX219_JSON)
    display_menu_p.add_argument("--width", type=int, default=CAM_W)
    display_menu_p.add_argument("--height", type=int, default=CAM_H)
    display_menu_p.add_argument("--provider", choices=["spacemit", "cpu"], default="spacemit")
    display_menu_p.add_argument("--ep-threads", type=int, default=1)
    display_menu_p.add_argument("--thermal-check-interval", type=float, default=2.0)
    display_menu_p.add_argument("--min-face-area-ratio", type=float, default=DEFAULT_MIN_FACE_AREA_RATIO)
    display_menu_p.add_argument("--max-temp-c", type=float, default=DEFAULT_MAX_TEMP_C)
    display_menu_p.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=True)
    display_menu_p.add_argument("--menu-control-file", default=DEFAULT_MENU_CONTROL_FILE)
    display_menu_p.set_defaults(func=display_menu)

    touch_p = sub.add_parser("touch-register")
    touch_p.add_argument("--display-width", type=int, default=DISPLAY_W)
    touch_p.add_argument("--display-height", type=int, default=DISPLAY_H)
    touch_p.add_argument("--display", choices=["gst", "opencv", "none"], default="gst")
    touch_p.add_argument("--width", type=int, default=960)
    touch_p.add_argument("--height", type=int, default=540)
    touch_p.add_argument("--target-fps", type=float, default=6.0)
    touch_p.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    touch_p.add_argument("--det-conf", type=float, default=DEFAULT_DET_CONF)
    touch_p.add_argument("--detect-interval", type=float, default=0.3)
    touch_p.add_argument("--camera-json", default=IMX219_JSON)
    touch_p.add_argument("--provider", choices=["spacemit", "cpu"], default="spacemit")
    touch_p.add_argument("--ep-threads", type=int, default=1)
    touch_p.add_argument("--thermal-check-interval", type=float, default=2.0)
    touch_p.add_argument("--min-face-area-ratio", type=float, default=DEFAULT_MIN_FACE_AREA_RATIO)
    touch_p.add_argument("--max-temp-c", type=float, default=DEFAULT_MAX_TEMP_C)
    touch_p.add_argument("--brightness", type=float, default=0.0)
    touch_p.add_argument("--contrast", type=float, default=1.0)
    touch_p.add_argument("--gamma", type=float, default=1.0)
    touch_p.add_argument("--mirror", action=argparse.BooleanOptionalAction, default=True)
    touch_p.add_argument("--touch-device", default=DEFAULT_TOUCH_DEVICE)
    touch_p.add_argument("--touch-control-file", default="/tmp/smart_medicine_face/touch_buttons_control.txt")
    touch_p.add_argument("--register-name", default="老人")
    touch_p.add_argument("--auto-live", action="store_true", help="start live recognition after a successful registration")
    touch_p.add_argument("--live-duration", type=float, default=0.0, help="seconds; 0 means run live until stopped")
    touch_p.add_argument("--samples", type=int, default=5)
    touch_p.add_argument("--sample-interval", type=float, default=0.8)
    touch_p.add_argument("--max-seconds", type=float, default=40.0)
    touch_p.add_argument("--stable-frames", type=int, default=3)
    touch_p.add_argument("--register-min-face-area-ratio", type=float, default=0.035)
    touch_p.add_argument("--register-max-face-area-ratio", type=float, default=0.30)
    touch_p.add_argument("--center-tolerance", type=float, default=0.08)
    touch_p.add_argument("--sharpness-threshold", type=float, default=45.0)
    touch_p.add_argument("--face-brightness-min", type=float, default=45.0)
    touch_p.add_argument("--face-brightness-max", type=float, default=235.0)
    touch_p.add_argument("--register-any-face", action="store_true", help="collect samples from the largest detected face without quality gates")
    touch_p.set_defaults(func=touch_register_ui)

    parser.set_defaults(func=live, command="live")
    return parser


def ask_float(prompt: str, default: float, low: float, high: float) -> float:
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw:
        return default
    return max(low, min(high, float(raw)))


def ask_int(prompt: str, default: int, low: int, high: int) -> int:
    raw = input(f"{prompt} [{default}]: ").strip()
    if not raw:
        return default
    return max(low, min(high, int(raw)))


def build_menu_args(command: str) -> argparse.Namespace:
    argv = [command, "__menu_name__"] if command == "register" else [command]
    args = build_parser().parse_args(argv)
    args.display_width = ask_int("显示宽度", 960, 320, 1920)
    args.display_height = ask_int("显示高度", 540, 240, 1080)
    args.target_fps = ask_float("显示目标 FPS", 6.0 if command == "live" else 8.0, 1.0, 30.0)
    args.threshold = ask_float("人脸相似度阈值", DEFAULT_THRESHOLD, 0.1, 0.95)
    args.detect_interval = ask_float("识别间隔秒", DEFAULT_DETECT_INTERVAL, 0.2, 10.0)
    args.ep_threads = ask_int("K1 推理线程数", 1, 1, 4)
    args.min_face_area_ratio = ask_float("最小人脸面积比例", DEFAULT_MIN_FACE_AREA_RATIO, 0.0, 0.20)
    args.max_temp_c = ask_float("温度保护上限 C", DEFAULT_MAX_TEMP_C, 60.0, 95.0)
    return args


def menu(_args: argparse.Namespace) -> None:
    while True:
        print("\nSmart Medicine 官方人脸识别")
        print("1. 实时识别人脸")
        print("2. 录入人脸")
        print("3. 查看已录入的人脸信息")
        print("4. 退出")
        choice = input("请选择: ").strip()
        if choice == "1":
            args = build_menu_args("live")
            live(args)
        elif choice == "2":
            args = build_menu_args("register")
            name = input("请输入人脸名: ").strip()
            if not name:
                print("名字不能为空")
                continue
            args.name = name
            args.output_dir = "~/smart_medicine/vision/output"
            args.samples = ask_int("采集样本数", 5, 2, 20)
            args.sample_interval = ask_float("采样间隔秒", 0.8, 0.2, 5.0)
            args.max_seconds = ask_float("最长录入时间秒", 40.0, 5.0, 180.0)
            register(args)
        elif choice == "3":
            list_faces(_args)
        elif choice == "4":
            return
        else:
            print("无效选择")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if hasattr(args, "output_dir"):
        args.output_dir = str(Path(args.output_dir).expanduser())
    args.func(args)


if __name__ == "__main__":
    main()
