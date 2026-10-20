#!/usr/bin/env python3
from __future__ import annotations

import argparse
from difflib import SequenceMatcher
import json
import math
import os
import sys
import threading
import time
from pathlib import Path

import cv2
import gi
import numpy as np
import onnxruntime as ort
from PIL import Image, ImageDraw, ImageFont
ort.set_default_logger_severity(3)
try:
    import spacemit_ort  # noqa: F401 - registers SpaceMITExecutionProvider on K1
except Exception:
    pass

gi.require_version("Gst", "1.0")
from gi.repository import Gst

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from bench_v5_int8_k1_mixed import Int8V5
from eval_ppocr_v5_mobile_fp32_official import get_rotate_crop_image

MEDICINE_DB = [
    "三金西瓜霜", "人参健脾片", "口服补液盐散（III）", "复合维生素B片",
    "复方感冒灵颗粒", "复方金银花颗粒", "孟鲁司特钠片", "小柴胡颗粒",
    "阿莫西林胶囊", "复方氨酚烷胺胶囊", "氨氯地平片",
    "双歧杆菌三联活菌胶囊", "蒙脱石散",
    "盐酸多西环素片", "盐酸西替利嗪片", "维生素D滴剂", "金银花口服液",
]
FONT_PATH = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def normalize_text(value: str) -> str:
    return "".join(ch for ch in value.lower() if not ch.isspace() and ch not in "（）()[]")


def correct_name(raw_text: str) -> tuple[str, float]:
    raw = normalize_text(raw_text)
    best_name, best_score = "(不纠偏)", 0.0
    for name in MEDICINE_DB:
        score = SequenceMatcher(None, raw, normalize_text(name)).ratio()
        if normalize_text(name) in raw:
            score = 1.0
        if score > best_score:
            best_name, best_score = name, score
    return (best_name, best_score) if best_score >= 0.70 else ("(不纠偏)", best_score)


def text_box_height(box: np.ndarray) -> float:
    edge_a = float(np.linalg.norm(box[0] - box[1]))
    edge_b = float(np.linalg.norm(box[1] - box[2]))
    return min(edge_a, edge_b)


def det_ready_roi(roi_bgr: np.ndarray, target_long_side: int = 640) -> np.ndarray:
    """Convert an arbitrary YOLO crop to the static 640x352 OCR-det input."""
    height, width = roi_bgr.shape[:2]
    scale = min(640 / max(1, width), 352 / max(1, height))
    resized = cv2.resize(roi_bgr, (max(1, round(width * scale)), max(1, round(height * scale))), interpolation=cv2.INTER_CUBIC if scale > 1 else cv2.INTER_AREA)
    canvas = np.full((352, 640, 3), 114, np.uint8)
    top = (352 - resized.shape[0]) // 2
    left = (640 - resized.shape[1]) // 2
    canvas[top:top + resized.shape[0], left:left + resized.shape[1]] = resized
    return canvas


def upright_text_crop(image: np.ndarray, points: np.ndarray) -> np.ndarray:
    """Perspective crop and normalize upright vertical labels to left-to-right."""
    points = np.asarray(points, dtype=np.float32)
    edge_x, edge_y = points[1] - points[0], points[3] - points[0]
    crop_w = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    crop_h = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if crop_w < 2 or crop_h < 2:
        return np.empty((0, 0, 3), np.uint8)
    dst = np.float32([[0, 0], [crop_w, 0], [crop_w, crop_h], [0, crop_h]])
    crop = cv2.warpPerspective(image, cv2.getPerspectiveTransform(points, dst), (crop_w, crop_h), borderMode=cv2.BORDER_REPLICATE)
    if crop_w >= crop_h and abs(float(edge_x[1])) > abs(float(edge_x[0])) and edge_x[1] < 0:
        crop = cv2.rotate(crop, cv2.ROTATE_180)
    elif crop_h > crop_w and abs(float(edge_y[1])) > abs(float(edge_y[0])):
        crop = cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE if edge_y[1] > 0 else cv2.ROTATE_90_CLOCKWISE)
    return crop


def draw_cn(frame: np.ndarray, lines: list[tuple[str, tuple[int, int, int]]]) -> np.ndarray:
    image = Image.fromarray(frame)
    painter = ImageDraw.Draw(image)
    font = ImageFont.truetype(FONT_PATH, 30)
    y = 130
    for text, color in lines:
        painter.text((18, y), text, font=font, fill=color)
        y += 38
    return np.asarray(image)


def write_camera_json(base_json: str, target: Path, width: int, height: int) -> None:
    with open(base_json, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["show_fps"] = 0
    data["tuning_server_enable"] = 0
    try:
        data["cpp_node"][0]["size_width"] = width
        data["cpp_node"][0]["size_height"] = height
        data["isp_node"][0]["out_width"] = width
        data["isp_node"][0]["out_height"] = height
        data["isp_node"][0]["sensor_work_mode"] = 0
    except Exception:
        pass
    target.parent.mkdir(parents=True, exist_ok=True)
    with open(target, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


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


def postprocess(pred, scale, pad_w, pad_h, orig_w, orig_h, conf_thres, iou_thres):
    pred = pred[0].T
    boxes = []
    scores = []
    for row in pred:
        score = float(row[4])
        if score < conf_thres:
            continue
        cx, cy, w, h = map(float, row[:4])
        boxes.append([cx - w / 2.0, cy - h / 2.0, w, h])
        scores.append(score)
    if not boxes:
        return []
    idxs = cv2.dnn.NMSBoxes(boxes, scores, conf_thres, iou_thres)
    if idxs is None or len(idxs) == 0:
        return []
    idxs = np.array(idxs).reshape(-1)
    out = []
    for i in idxs:
        x1, y1, w, h = boxes[int(i)]
        ox1 = (x1 - pad_w) / scale
        oy1 = (y1 - pad_h) / scale
        ox2 = (x1 + w - pad_w) / scale
        oy2 = (y1 + h - pad_h) / scale
        ox1 = int(max(0, min(orig_w - 1, ox1)))
        oy1 = int(max(0, min(orig_h - 1, oy1)))
        ox2 = int(max(0, min(orig_w - 1, ox2)))
        oy2 = int(max(0, min(orig_h - 1, oy2)))
        if ox2 > ox1 and oy2 > oy1:
            out.append((ox1, oy1, ox2, oy2, scores[int(i)]))
    return out


def choose_providers(requested: str, threads: int):
    available = ort.get_available_providers()
    if requested == "cpu":
        return ["CPUExecutionProvider"]
    if requested == "spacemit":
        for name in ("SpaceMITExecutionProvider", "SpacemiTExecutionProvider"):
            if name in available:
                return [(name, {
                    "SPACEMIT_EP_INTRA_THREAD_NUM": str(max(1, threads)),
                    "SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD": "1",
                    "SPACEMIT_EP_DISABLE_FLOAT16_EPILOGUE": "1",
                    "SPACEMIT_EP_DISABLE_OP_NAME_FILTER": "Conv.7;Conv.11;Conv.21;Conv.29",
                }), "CPUExecutionProvider"]


class SharedState:
    def __init__(self):
        self.lock = threading.Lock()
        self.cond = threading.Condition(self.lock)
        self.stop = False
        self.pending_frame = None
        self.pending_id = 0
        self.last_submitted_id = 0
        self.cached = []
        self.miss_count = 0
        self.infer_count = 0
        self.last_infer_ms = 0.0
        self.latest_pred_frame = None
        self.latest_rgb_frame = None
        self.ocr_raw = "(等待药盒稳定)"
        self.ocr_corrected = "(等待识别)"
        self.ocr_score = 0.0
        self.ocr_ms = 0.0
        self.ocr_count = 0
        self.last_ocr_submit = 0.0


def inference_loop(state: SharedState, session, input_name: str, args, inference_lock: threading.Lock) -> None:
    last_seen_id = 0
    while True:
        with state.cond:
            state.cond.wait_for(lambda: state.stop or state.pending_id != last_seen_id)
            if state.stop:
                return
            frame = state.pending_frame
            frame_id = state.pending_id
            last_seen_id = frame_id
        if frame is None:
            continue
        blob, scale, pad_w, pad_h = letterbox_rgb(frame, args.input_size)
        t0 = time.perf_counter()
        with inference_lock:
            pred = session.run(None, {input_name: blob})[0]
        last_infer_ms = (time.perf_counter() - t0) * 1000.0
        detections = postprocess(pred, scale, pad_w, pad_h, args.width, args.height, args.conf, args.iou)
        with state.lock:
            state.infer_count += 1
            state.last_infer_ms = last_infer_ms
            state.latest_pred_frame = frame_id
            state.latest_rgb_frame = frame
            if detections:
                state.cached = detections
                state.miss_count = 0
            else:
                state.miss_count += 1
                if state.miss_count >= max(1, args.hold_miss):
                    state.cached = []


def ocr_loop(state: SharedState, engine: Int8V5, args, inference_lock: threading.Lock) -> None:
    while True:
        time.sleep(0.03)
        with state.lock:
            if state.stop:
                return
            now = time.perf_counter()
            if not state.cached or state.latest_rgb_frame is None or now - state.last_ocr_submit < args.ocr_interval:
                continue
            x1, y1, x2, y2, _ = max(state.cached, key=lambda item: item[4])
            box_w, box_h = x2 - x1, y2 - y1
            pad_x, pad_y = int(math.ceil(box_w * args.crop_pad)), int(math.ceil(box_h * args.crop_pad))
            frame = state.latest_rgb_frame
            # Coordinates are mapped back to the original camera frame. The
            # YOLO 320x320 letterbox tensor is never passed to OCR.
            crop_rgb = frame[max(0, y1-pad_y):min(args.height, y2+pad_y), max(0, x1-pad_x):min(args.width, x2+pad_x)].copy()
            state.last_ocr_submit = now
        if crop_rgb.size == 0:
            continue
        start = time.perf_counter()
        try:
            # Camera frames are RGB; v5 OCR preprocessing accepts BGR images.
            crop = det_ready_roi(cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR))
            with inference_lock:
                dets = engine.detect(crop)
                # Evaluate largest text boxes in order; database similarity, not
                # the model's per-character confidence, decides the medicine.
                dets = sorted(dets, key=lambda item: text_box_height(item["box"]), reverse=True)[:5]
                candidates = []
                for item in dets:
                    word = upright_text_crop(crop, item["box"])
                    if word.size == 0:
                        continue
                    output = engine.rec.run(None, {engine.rec_input: engine.rec_preprocess(word)})[0][0]
                    text, confidence = engine.decode(output)
                    corrected_candidate, db_score = correct_name(text)
                    candidates.append((text, confidence, text_box_height(item["box"]), corrected_candidate, db_score))
                    if corrected_candidate != "(不纠偏)" and db_score >= 0.70:
                        break
            selected = next(((text, conf, h, corrected, db_score) for text, conf, h, corrected, db_score in candidates if corrected != "(不纠偏)" and db_score >= 0.70), None)
            if selected is None:
                selected = next(((text, conf, h, corrected, db_score) for text, conf, h, corrected, db_score in candidates if text), None)
            if selected is None:
                raw, corrected, score = "(未识别到文字)", "(无纠偏结果)", 0.0
            else:
                raw, rec_conf, _, corrected, db_score = selected
                score = db_score
        except Exception as exc:
            raw, corrected, score = f"OCR异常: {type(exc).__name__}", "(无纠偏结果)", 0.0
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        with state.lock:
            state.ocr_raw, state.ocr_corrected, state.ocr_score = raw, corrected, score
            state.ocr_ms, state.ocr_count = elapsed_ms, state.ocr_count + 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--ocr-model-root", required=True)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=720)
    ap.add_argument("--display-width", type=int, default=0)
    ap.add_argument("--display-height", type=int, default=0)
    ap.add_argument("--input-size", type=int, default=416)
    ap.add_argument("--conf", type=float, default=0.25)
    ap.add_argument("--iou", type=float, default=0.45)
    ap.add_argument("--skip", type=int, default=2)
    ap.add_argument("--hold-miss", type=int, default=6)
    ap.add_argument("--base-json", default="/usr/share/camera_json/csi1_camera_auto.json")
    ap.add_argument("--workdir", default="/tmp/medicine_box_yolo_preview")
    ap.add_argument("--provider", choices=["auto", "cpu", "spacemit"], default="auto")
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--ocr-interval", type=float, default=1.0)
    ap.add_argument("--crop-pad", type=float, default=0.05)
    ap.add_argument("--rec-min-conf", type=float, default=0.40)
    args = ap.parse_args()
    if args.display_width <= 0:
        args.display_width = args.width
    if args.display_height <= 0:
        args.display_height = args.height

    os.environ.setdefault("XDG_RUNTIME_DIR", "/run/user/1000")
    os.environ.setdefault("WAYLAND_DISPLAY", "wayland-0")

    opts = ort.SessionOptions()
    opts.intra_op_num_threads = max(1, args.threads)
    opts.inter_op_num_threads = 1
    providers = choose_providers(args.provider, args.threads)
    session = ort.InferenceSession(args.model, sess_options=opts, providers=providers)
    ocr_engine = Int8V5(Path(args.ocr_model_root))
    input_name = session.get_inputs()[0].name
    actual_providers = session.get_providers()
    print("ORT providers:", actual_providers, flush=True)
    print("Input:", input_name, session.get_inputs()[0].shape, flush=True)
    print("Output:", session.get_outputs()[0].name, session.get_outputs()[0].shape, flush=True)
    print("OCR providers:", ocr_engine.det.get_providers(), ocr_engine.rec.get_providers(), flush=True)

    Gst.init(None)
    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    camera_json = workdir / f"camera_{args.width}x{args.height}.json"
    write_camera_json(args.base_json, camera_json, args.width, args.height)

    capture_desc = (
        f"spacemitsrc location={camera_json} close-dmabuf=1 "
        f"! video/x-raw,format=NV12,width={args.width},height={args.height},framerate=30/1 "
        "! videoconvert ! video/x-raw,format=RGB "
        "! appsink name=sink emit-signals=false sync=false max-buffers=1 drop=true"
    )
    output_desc = (
        "appsrc name=src is-live=true block=false format=time "
        f"caps=video/x-raw,format=RGB,width={args.display_width},height={args.display_height},framerate=30/1 "
        "! videoconvert "
        f"! waylandsink sync=false render-rectangle=<0,0,{args.display_width},{args.display_height}>"
    )
    print("capture:", capture_desc, flush=True)
    capture = Gst.parse_launch(capture_desc)
    output = Gst.parse_launch(output_desc)
    sink = capture.get_by_name("sink")
    src = output.get_by_name("src")
    if sink is None or src is None:
        raise RuntimeError("appsink/appsrc missing")

    state = SharedState()
    inference_lock = threading.Lock()
    worker = threading.Thread(target=inference_loop, args=(state, session, input_name, args, inference_lock), daemon=True)
    ocr_worker = threading.Thread(target=ocr_loop, args=(state, ocr_engine, args, inference_lock), daemon=True)
    worker.start()
    ocr_worker.start()

    capture.set_state(Gst.State.PLAYING)
    output.set_state(Gst.State.PLAYING)

    frame_id = 0
    preview_count = 0
    last_infer_total = 0
    t_fps = time.perf_counter()
    preview_fps = 0.0
    infer_fps = 0.0
    pts = 0
    frame_duration = Gst.SECOND // 30
    submit_every = max(1, args.skip)

    try:
        while True:
            sample = sink.emit("pull-sample")
            if sample is None:
                time.sleep(0.005)
                continue
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if not ok:
                continue
            try:
                frame = np.frombuffer(info.data, dtype=np.uint8).reshape((args.height, args.width, 3)).copy()
            finally:
                buf.unmap(info)

            frame_id += 1
            preview_count += 1
            if frame_id % submit_every == 0:
                with state.cond:
                    state.pending_frame = frame.copy()
                    state.pending_id = frame_id
                    state.last_submitted_id = frame_id
                    state.cond.notify()

            with state.lock:
                cached = list(state.cached)
                miss_count = state.miss_count
                last_infer_ms = state.last_infer_ms
                infer_total = state.infer_count
                latest_pred_frame = state.latest_pred_frame
                ocr_raw = state.ocr_raw
                ocr_corrected = state.ocr_corrected
                ocr_score = state.ocr_score
                ocr_ms = state.ocr_ms
                ocr_count = state.ocr_count

            now = time.perf_counter()
            if now - t_fps >= 1.0:
                elapsed = now - t_fps
                preview_fps = preview_count / elapsed
                infer_fps = (infer_total - last_infer_total) / elapsed
                preview_count = 0
                last_infer_total = infer_total
                t_fps = now
                status = {
                    "detections": len(cached),
                    "preview_fps": round(preview_fps, 2),
                    "infer_fps": round(infer_fps, 2),
                    "last_infer_ms": round(last_infer_ms, 2),
                    "providers": actual_providers,
                    "miss_count": miss_count,
                    "latest_frame": frame_id,
                    "latest_pred_frame": latest_pred_frame,
                    "boxes": [list(x) for x in cached],
                    "ocr_raw": ocr_raw,
                    "ocr_corrected": ocr_corrected,
                    "ocr_score": round(ocr_score, 3),
                    "ocr_ms": round(ocr_ms, 1),
                    "ocr_count": ocr_count,
                }
                (workdir / "status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
                print("STATUS", json.dumps(status), flush=True)

            for x1, y1, x2, y2, score in cached:
                cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3)
                cv2.putText(frame, f"medicine_box conf:{score:.2f}", (x1, max(24, y1 - 8)), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            cv2.putText(frame, f"boxes:{len(cached)} infer:{infer_fps:.1f}fps {last_infer_ms:.1f}ms", (20, 42), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (255, 220, 0), 2)
            cv2.putText(frame, f"preview:{preview_fps:.1f}fps model:{Path(args.model).name}", (20, 78), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 220, 0), 2)
            cv2.putText(frame, f"provider:{actual_providers[0]} miss:{miss_count} conf:{args.conf:.2f}", (20, 114), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 220, 0), 2)
            frame = draw_cn(frame, [
                (f"原始 OCR: {ocr_raw}", (255, 245, 130)),
                (f"药名库纠偏: {ocr_corrected}  OCR置信:{ocr_score:.2f}", (120, 255, 120)),
                (f"OCR: {ocr_ms:.0f}ms  刷新: {args.ocr_interval:.1f}s", (170, 220, 255)),
            ])

            out_frame = frame
            if args.display_width != args.width or args.display_height != args.height:
                out_frame = cv2.resize(frame, (args.display_width, args.display_height), interpolation=cv2.INTER_LINEAR)
            out_buf = Gst.Buffer.new_allocate(None, out_frame.nbytes, None)
            out_buf.fill(0, out_frame.tobytes())
            out_buf.pts = pts
            out_buf.duration = frame_duration
            pts += frame_duration
            ret = src.emit("push-buffer", out_buf)
            if ret != Gst.FlowReturn.OK:
                time.sleep(0.005)
    except KeyboardInterrupt:
        pass
    finally:
        with state.cond:
            state.stop = True
            state.cond.notify_all()
        try:
            src.emit("end-of-stream")
        except Exception:
            pass
        capture.set_state(Gst.State.NULL)
        output.set_state(Gst.State.NULL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
