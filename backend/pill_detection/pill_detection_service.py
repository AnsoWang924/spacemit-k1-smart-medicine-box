#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import signal
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2
import gi
import numpy as np
import onnxruntime as ort

ort.set_default_logger_severity(3)
try:
    import spacemit_ort  # noqa: F401
except Exception:
    spacemit_ort = None

gi.require_version('Gst', '1.0')
from gi.repository import Gst

CLASS_NAMES = ['k1-pill', 'k1-sachet']
DEFAULT_MODEL = str(Path(__file__).resolve().parent / 'models' / 'pill_sachet_yolo17_combined_yolov8n_320_b1_int8_qop.onnx')
DEFAULT_BASE_JSON = '/usr/share/camera_json/csi1_live.json'
DEFAULT_WORKDIR = '/tmp/k1_pill_detection_single_frame'


def now_iso() -> str:
    return time.strftime('%Y-%m-%dT%H:%M:%S%z', time.localtime())


def response_ok(data, request_id=None):
    return {'requestId': request_id or f'pill_{int(time.time() * 1000)}', 'data': data, 'error': None}


def response_err(code, message, retryable=False, request_id=None):
    return {
        'requestId': request_id or f'pill_{int(time.time() * 1000)}',
        'data': None,
        'error': {'code': code, 'message': message, 'retryable': bool(retryable)},
    }


def write_camera_json(base_json: str, target: Path, width: int, height: int) -> None:
    with open(base_json, 'r', encoding='utf-8') as f:
        data = json.load(f)
    data['show_fps'] = 0
    data['tuning_server_enable'] = 0
    try:
        data['cpp_node'][0]['size_width'] = width
        data['cpp_node'][0]['size_height'] = height
        data['cpp_node'][0]['src_from_file'] = 0
        data['isp_node'][0]['out_width'] = width
        data['isp_node'][0]['out_height'] = height
        data['isp_node'][0]['sensor_work_mode'] = 0
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')


def letterbox_rgb(img: np.ndarray, size: int):
    h, w = img.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LINEAR)
    canvas = np.full((size, size, 3), 114, dtype=np.uint8)
    pad_w = (size - nw) // 2
    pad_h = (size - nh) // 2
    canvas[pad_h:pad_h + nh, pad_w:pad_w + nw] = resized
    blob = canvas.transpose(2, 0, 1)[None].astype(np.float32) / 255.0
    return blob, scale, pad_w, pad_h


def nms_xyxy(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float):
    if len(boxes) == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0, x2 - x1) * np.maximum(0, y2 - y1)
    order = scores.argsort()[::-1]
    keep = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        inter = np.maximum(0, xx2 - xx1) * np.maximum(0, yy2 - yy1)
        union = areas[i] + areas[order[1:]] - inter + 1e-6
        order = order[1:][inter / union <= iou_threshold]
    return keep


def postprocess(pred, scale, pad_w, pad_h, roi_w, roi_h, args):
    arr = pred[0]
    if arr.ndim == 3:
        arr = arr[0]
    if arr.shape[0] <= 10 and arr.shape[1] > arr.shape[0]:
        arr = arr.T
    if arr.shape[1] < 5:
        return []

    coords = arr[:, :4].astype(np.float32, copy=False)
    cls_scores = arr[:, 4:].astype(np.float32, copy=False)
    if cls_scores.shape[1] == 1:
        class_ids = np.zeros((cls_scores.shape[0],), dtype=np.int32)
        scores = cls_scores[:, 0]
        sachet_scores = np.zeros_like(scores, dtype=np.float32)
    else:
        class_ids = np.argmax(cls_scores, axis=1).astype(np.int32)
        scores = np.max(cls_scores, axis=1)
        sachet_scores = cls_scores[:, 1] if cls_scores.shape[1] > 1 else np.zeros_like(scores, dtype=np.float32)

    thresholds = np.full_like(scores, float(args.conf), dtype=np.float32)
    thresholds[class_ids == 1] = float(args.sachet_conf)
    mask = scores >= thresholds
    if not np.any(mask):
        return []

    coords = coords[mask]
    scores = scores[mask]
    class_ids = class_ids[mask]
    sachet_scores = sachet_scores[mask]

    cx, cy, bw, bh = coords[:, 0], coords[:, 1], coords[:, 2], coords[:, 3]
    boxes = np.empty((coords.shape[0], 4), dtype=np.float32)
    boxes[:, 0] = (cx - bw * 0.5 - pad_w) / scale
    boxes[:, 1] = (cy - bh * 0.5 - pad_h) / scale
    boxes[:, 2] = (cx + bw * 0.5 - pad_w) / scale
    boxes[:, 3] = (cy + bh * 0.5 - pad_h) / scale
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, roi_w - 1)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, roi_h - 1)

    widths = np.maximum(0, boxes[:, 2] - boxes[:, 0])
    heights = np.maximum(0, boxes[:, 3] - boxes[:, 1])
    areas = widths * heights
    area_mask = (areas >= args.min_area) & (areas <= args.max_area)

    large_pill_mask = (class_ids == 0) & (areas >= (roi_w * roi_h * args.pill_max_area_ratio))
    if np.any(large_pill_mask):
        large_pill_as_sachet = large_pill_mask & (sachet_scores >= args.sachet_conf)
        class_ids[large_pill_as_sachet] = 1
        scores[large_pill_as_sachet] = sachet_scores[large_pill_as_sachet]
        area_mask = area_mask & (~large_pill_mask | large_pill_as_sachet)

    sachet_mask = class_ids == 1
    if np.any(sachet_mask):
        sachet_shape_mask = (
            (areas >= args.sachet_min_area)
            & (widths >= args.sachet_min_width)
            & (heights >= args.sachet_min_height)
        )
        area_mask = area_mask & (~sachet_mask | sachet_shape_mask)
    if not np.any(area_mask):
        return []

    boxes = boxes[area_mask]
    scores = scores[area_mask]
    class_ids = class_ids[area_mask]
    detections = []
    for cls_id in np.unique(class_ids):
        cls_mask = class_ids == cls_id
        keep = nms_xyxy(boxes[cls_mask], scores[cls_mask], args.iou)
        cls_boxes = boxes[cls_mask]
        cls_scores = scores[cls_mask]
        for i in keep:
            detections.append((cls_boxes[i], float(cls_scores[i]), int(cls_id)))
    detections.sort(key=lambda item: item[1], reverse=True)
    return detections


def choose_session(args):
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, args.ort_threads)
    opts.inter_op_num_threads = 1
    if args.provider == 'cpu':
        return ort.InferenceSession(args.model, sess_options=opts, providers=['CPUExecutionProvider'])
    if spacemit_ort is None:
        raise RuntimeError('spacemit_ort is not available')
    providers = ['SpaceMITExecutionProvider', 'CPUExecutionProvider']
    provider_options = [
        {'SPACEMIT_EP_INTRA_THREAD_NUM': '2', 'SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD': '1'},
        {},
    ]
    return ort.InferenceSession(args.model, sess_options=opts, providers=providers, provider_options=provider_options)


def capture_one_frame(args):
    Gst.init(None)
    workdir = Path(args.workdir)
    camera_json = workdir / f'camera_{args.width}x{args.height}.json'
    write_camera_json(args.base_json, camera_json, args.width, args.height)
    desc = (
        f'spacemitsrc location={camera_json} close-dmabuf=1 '
        f'! video/x-raw,format=NV12,width={args.width},height={args.height},framerate=30/1 '
        '! videoconvert ! video/x-raw,format=RGB '
        '! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true'
    )
    pipeline = Gst.parse_launch(desc)
    sink = pipeline.get_by_name('sink')
    if sink is None:
        raise RuntimeError('appsink missing')
    pipeline.set_state(Gst.State.PLAYING)
    try:
        deadline = time.time() + args.capture_timeout
        sample = None
        while time.time() < deadline:
            sample = sink.emit('try-pull-sample', int(200 * 1000 * 1000))
            if sample is not None:
                break
        if sample is None:
            raise TimeoutError('capture timeout')
        buf = sample.get_buffer()
        ok, info = buf.map(Gst.MapFlags.READ)
        if not ok:
            raise RuntimeError('buffer map failed')
        try:
            return np.frombuffer(info.data, dtype=np.uint8).reshape((args.height, args.width, 3)).copy()
        finally:
            buf.unmap(info)
    finally:
        pipeline.set_state(Gst.State.NULL)


class PillDetectionRuntime:
    def __init__(self, args):
        self.args = args
        self.session = None
        self.input_name = None
        self.input_shape = None
        self.model_loaded_at = None
        self.lock = threading.Lock()

    def preload(self):
        with self.lock:
            if self.session is None:
                self.session = choose_session(self.args)
                input_meta = self.session.get_inputs()[0]
                self.input_name = input_meta.name
                self.input_shape = list(input_meta.shape)
                if len(input_meta.shape) >= 4 and isinstance(input_meta.shape[2], int):
                    self.args.input_size = int(input_meta.shape[2])
                self.model_loaded_at = now_iso()
            return self.status(ready_override=True)

    def release(self):
        with self.lock:
            already = self.session is None
            self.session = None
            self.input_name = None
            self.input_shape = None
            self.model_loaded_at = None
            return {'released': True, 'alreadyReleased': already, 'releasedAt': now_iso()}

    def status(self, ready_override=None):
        ready = self.session is not None if ready_override is None else ready_override
        return {
            'module': 'pill-count',
            'ready': bool(ready),
            'modelVersion': Path(self.args.model).name,
            'modelPath': self.args.model,
            'provider': self.args.provider,
            'tcmBlocks': 2,
            'quantization': 'INT8 static QOP' if 'int8' in Path(self.args.model).name.lower() else 'FP32',
            'camera': 'OV5647/csi1_live.json',
            'inputSize': self.args.input_size,
            'thresholds': {
                'pillConf': self.args.conf,
                'sachetConf': self.args.sachet_conf,
                'pillMaxAreaRatio': self.args.pill_max_area_ratio,
            },
            'readyAt': self.model_loaded_at,
        }

    def recognize_once(self):
        if not self.lock.acquire(blocking=False):
            raise RuntimeError('model_busy')
        try:
            if self.session is None:
                self.session = choose_session(self.args)
                input_meta = self.session.get_inputs()[0]
                self.input_name = input_meta.name
                self.input_shape = list(input_meta.shape)
                if len(input_meta.shape) >= 4 and isinstance(input_meta.shape[2], int):
                    self.args.input_size = int(input_meta.shape[2])
                self.model_loaded_at = now_iso()

            t_cap = time.perf_counter()
            frame = capture_one_frame(self.args)
            cap_ms = (time.perf_counter() - t_cap) * 1000.0
            t_pre = time.perf_counter()
            blob, scale, pad_w, pad_h = letterbox_rgb(frame, self.args.input_size)
            pre_ms = (time.perf_counter() - t_pre) * 1000.0
            t_infer = time.perf_counter()
            pred = self.session.run(None, {self.input_name: blob})
            infer_ms = (time.perf_counter() - t_infer) * 1000.0
            t_post = time.perf_counter()
            detections = postprocess(pred, scale, pad_w, pad_h, self.args.width, self.args.height, self.args)
            post_ms = (time.perf_counter() - t_post) * 1000.0

            items = []
            for box, score, cls_id in detections:
                items.append({
                    'classId': cls_id,
                    'className': CLASS_NAMES[cls_id] if 0 <= cls_id < len(CLASS_NAMES) else str(cls_id),
                    'score': float(score),
                    'box': [float(v) for v in box],
                    'area': float(max(0.0, box[2] - box[0]) * max(0.0, box[3] - box[1])),
                })
            pill_count = sum(1 for item in items if item['classId'] == 0)
            sachet_count = sum(1 for item in items if item['classId'] == 1)
            return {
                'recognized': True,
                'capturedFrames': 1,
                'pillCount': pill_count,
                'sachetCount': sachet_count,
                'totalCount': pill_count + sachet_count,
                'items': items,
                'timingMs': {
                    'capture': round(cap_ms, 2),
                    'preprocess': round(pre_ms, 2),
                    'inference': round(infer_ms, 2),
                    'postprocess': round(post_ms, 2),
                    'total': round(cap_ms + pre_ms + infer_ms + post_ms, 2),
                },
                'modelVersion': Path(self.args.model).name,
                'provider': 'SpaceMITExecutionProvider',
                'tcmBlocks': 2,
                'quantization': 'INT8 static QOP' if 'int8' in Path(self.args.model).name.lower() else 'FP32',
                'capturedAt': now_iso(),
            }
        finally:
            self.lock.release()


class Handler(BaseHTTPRequestHandler):
    runtime: PillDetectionRuntime = None

    def _read_json(self):
        length = int(self.headers.get('Content-Length', '0') or '0')
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode('utf-8'))

    def _send_json(self, code, payload):
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, fmt, *args):
        return

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/v2/pill-count/status':
            self._send_json(200, response_ok(self.runtime.status()))
            return
        self._send_json(404, response_err('not_found', 'route not found', False))

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
        except Exception:
            self._send_json(400, response_err('invalid_request', 'invalid json body', False))
            return
        request_id = body.get('requestId') if isinstance(body, dict) else None
        try:
            if path == '/api/v2/pill-count/preload':
                self._send_json(200, response_ok(self.runtime.preload(), request_id))
            elif path == '/api/v2/pill-count/release':
                self._send_json(200, response_ok(self.runtime.release(), request_id))
            elif path == '/api/v2/pill-count/recognitions':
                self._send_json(200, response_ok(self.runtime.recognize_once(), request_id))
            else:
                self._send_json(404, response_err('not_found', 'route not found', False, request_id))
        except RuntimeError as exc:
            code = str(exc)
            if code == 'model_busy':
                self._send_json(429, response_err('model_busy', 'pill detection is already running', True, request_id))
            else:
                self._send_json(503, response_err('model_unavailable', repr(exc), True, request_id))
        except TimeoutError as exc:
            self._send_json(504, response_err('camera_unavailable', str(exc), True, request_id))
        except Exception as exc:
            self._send_json(503, response_err('model_unavailable', repr(exc), True, request_id))


def build_args():
    ap = argparse.ArgumentParser()
    ap.add_argument('--serve', action='store_true')
    ap.add_argument('--once', action='store_true')
    ap.add_argument('--host', default='0.0.0.0')
    ap.add_argument('--port', type=int, default=8797)
    ap.add_argument('--model', default=DEFAULT_MODEL)
    ap.add_argument('--base-json', default=DEFAULT_BASE_JSON)
    ap.add_argument('--workdir', default=DEFAULT_WORKDIR)
    ap.add_argument('--width', type=int, default=640)
    ap.add_argument('--height', type=int, default=360)
    ap.add_argument('--input-size', type=int, default=320)
    ap.add_argument('--conf', type=float, default=0.35)
    ap.add_argument('--sachet-conf', type=float, default=0.60)
    ap.add_argument('--iou', type=float, default=0.45)
    ap.add_argument('--min-area', type=float, default=20)
    ap.add_argument('--max-area', type=float, default=180000)
    ap.add_argument('--sachet-min-area', type=float, default=8000)
    ap.add_argument('--sachet-min-width', type=float, default=80)
    ap.add_argument('--sachet-min-height', type=float, default=45)
    ap.add_argument('--pill-max-area-ratio', type=float, default=1.0 / 35.0)
    ap.add_argument('--capture-timeout', type=float, default=5.0)
    ap.add_argument('--provider', choices=['spacemit', 'cpu'], default='spacemit')
    ap.add_argument('--ort-threads', type=int, default=1)
    return ap.parse_args()


def main():
    os.environ.setdefault('XDG_RUNTIME_DIR', '/run/user/1000')
    os.environ.setdefault('WAYLAND_DISPLAY', 'wayland-0')
    args = build_args()
    runtime = PillDetectionRuntime(args)
    if args.once or not args.serve:
        print(json.dumps(response_ok(runtime.recognize_once()), ensure_ascii=False, indent=2), flush=True)
        return 0

    Handler.runtime = runtime
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    stop = {'value': False}

    def handle_stop(signum, frame):
        stop['value'] = True
        httpd.shutdown()

    signal.signal(signal.SIGINT, handle_stop)
    signal.signal(signal.SIGTERM, handle_stop)
    print(f'pill_detection_service listening on {args.host}:{args.port}', flush=True)
    httpd.serve_forever()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
