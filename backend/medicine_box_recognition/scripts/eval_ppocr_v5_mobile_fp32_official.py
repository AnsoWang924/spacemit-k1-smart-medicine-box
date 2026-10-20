#!/usr/bin/env python3
"""Official-faithful PP-OCRv5 mobile FP32 full-image benchmark for K1.

Uses PaddleOCR's model-provided inference.yml parameters, DBPostProcess-style
contours/unclip, rotate-crop handling for vertical text, and CTC decoding.
No YOLO, name DB, title filtering, or quantization is involved.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
import pyclipper
import yaml

if os.environ.get("PPOCR_CPU_ONLY") != "1":
    try:
        import spacemit_ort  # noqa: F401
    except Exception:
        pass

ROOT = Path(__file__).resolve().parents[1]
MODEL_ROOT = ROOT / "models" / "ppocr_v5_mobile_onnx_official_20260717"


def providers():
    return [
        ("SpaceMITExecutionProvider", {
            "SPACEMIT_EP_INTRA_THREAD_NUM": "1",
            "SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD": "1",
        }),
        "CPUExecutionProvider",
    ]


def order_points_clockwise(pts: np.ndarray) -> np.ndarray:
    rect = np.zeros((4, 2), dtype=np.float32)
    pts = np.array(pts, dtype=np.float32)
    x_sorted = pts[np.argsort(pts[:, 0]), :]
    left, right = x_sorted[:2, :], x_sorted[2:, :]
    left = left[np.argsort(left[:, 1]), :]
    rect[0], rect[3] = left[0], left[1]
    d = np.linalg.norm(right - rect[0], axis=1)
    rect[1], rect[2] = right[np.argsort(d), :]
    return rect


def get_mini_boxes(contour: np.ndarray) -> tuple[np.ndarray, float]:
    box = cv2.boxPoints(cv2.minAreaRect(contour))
    box = order_points_clockwise(box)
    side = min(
        float(np.linalg.norm(box[0] - box[1])),
        float(np.linalg.norm(box[0] - box[3])),
    )
    return box, side


def box_score_fast(bitmap: np.ndarray, box: np.ndarray) -> float:
    h, w = bitmap.shape[:2]
    box = np.array(box, dtype=np.float32)
    xmin = int(np.clip(np.floor(box[:, 0].min()), 0, w - 1))
    xmax = int(np.clip(np.ceil(box[:, 0].max()), 0, w - 1))
    ymin = int(np.clip(np.floor(box[:, 1].min()), 0, h - 1))
    ymax = int(np.clip(np.ceil(box[:, 1].max()), 0, h - 1))
    if xmax <= xmin or ymax <= ymin:
        return 0.0
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    box = box.copy()
    box[:, 0] -= xmin
    box[:, 1] -= ymin
    cv2.fillPoly(mask, [box.astype(np.int32)], 1)
    return float(cv2.mean(bitmap[ymin:ymax + 1, xmin:xmax + 1], mask)[0])


def unclip(box: np.ndarray, ratio: float) -> np.ndarray | None:
    box = np.array(box, dtype=np.float32)
    area = abs(cv2.contourArea(box))
    length = cv2.arcLength(box, True)
    if area <= 0 or length <= 0:
        return None
    distance = area * ratio / length
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(box.astype(np.int32).tolist(), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offset.Execute(distance)
    if not expanded:
        return None
    return np.array(expanded[0], dtype=np.float32).reshape(-1, 1, 2)


def sorted_boxes(boxes: list[np.ndarray]) -> list[np.ndarray]:
    boxes = sorted(boxes, key=lambda x: (x[0][1], x[0][0]))
    for i in range(len(boxes) - 1):
        for j in range(i, 0, -1):
            if abs(boxes[j + 1][0][1] - boxes[j][0][1]) < 10 and boxes[j + 1][0][0] < boxes[j][0][0]:
                boxes[j], boxes[j + 1] = boxes[j + 1], boxes[j]
            else:
                break
    return boxes


def get_rotate_crop_image(img: np.ndarray, points: np.ndarray) -> np.ndarray:
    points = np.array(points, dtype=np.float32)
    crop_w = int(max(np.linalg.norm(points[0] - points[1]), np.linalg.norm(points[2] - points[3])))
    crop_h = int(max(np.linalg.norm(points[0] - points[3]), np.linalg.norm(points[1] - points[2])))
    if crop_w < 2 or crop_h < 2:
        return np.empty((0, 0, 3), dtype=np.uint8)
    dst = np.float32([[0, 0], [crop_w, 0], [crop_w, crop_h], [0, crop_h]])
    matrix = cv2.getPerspectiveTransform(points, dst)
    crop = cv2.warpPerspective(img, matrix, (crop_w, crop_h), borderMode=cv2.BORDER_REPLICATE)
    if crop.shape[0] / max(1, crop.shape[1]) >= 1.5:
        crop = np.rot90(crop)
    return crop


class OfficialV5Mobile:
    def __init__(self) -> None:
        self.det_dir = MODEL_ROOT / "PP-OCRv5_mobile_det_onnx_infer"
        self.rec_dir = MODEL_ROOT / "PP-OCRv5_mobile_rec_onnx_infer"
        self.det_cfg = yaml.safe_load((self.det_dir / "inference.yml").read_text(encoding="utf-8"))
        self.rec_cfg = yaml.safe_load((self.rec_dir / "inference.yml").read_text(encoding="utf-8"))
        self.det = ort.InferenceSession(str(self.det_dir / "inference.onnx"), providers=providers())
        self.rec = ort.InferenceSession(str(self.rec_dir / "inference.onnx"), providers=providers())
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        pp = self.det_cfg["PostProcess"]
        self.det_thresh = float(pp["thresh"])
        self.box_thresh = float(pp["box_thresh"])
        self.unclip_ratio = float(pp["unclip_ratio"])
        self.resize_long = int(self.det_cfg["PreProcess"]["transform_ops"][2]["DetResizeForTest"]["resize_long"])
        chars = list(self.rec_cfg["PostProcess"]["character_dict"])
        self.chars = ["blank", *chars, " "]
        output_classes = self.rec.get_outputs()[0].shape[-1]
        if isinstance(output_classes, int) and len(self.chars) != output_classes:
            raise RuntimeError(f"official dictionary mismatch: {len(self.chars)} != {output_classes}")

    def det_preprocess(self, img: np.ndarray) -> tuple[np.ndarray, float, float]:
        src_h, src_w = img.shape[:2]
        ratio = min(1.0, self.resize_long / float(max(src_h, src_w)))
        resize_h = max(32, int(round(src_h * ratio / 32) * 32))
        resize_w = max(32, int(round(src_w * ratio / 32) * 32))
        resized = cv2.resize(img, (resize_w, resize_h))
        data = resized.astype(np.float32) / 255.0
        data = (data - np.array([0.485, 0.456, 0.406], dtype=np.float32)) / np.array([0.229, 0.224, 0.225], dtype=np.float32)
        return data.transpose(2, 0, 1)[None].astype(np.float32), resize_h / src_h, resize_w / src_w

    def detect(self, img: np.ndarray) -> list[dict]:
        tensor, ratio_h, ratio_w = self.det_preprocess(img)
        pred = self.det.run(None, {self.det_input: tensor})[0][0, 0]
        bitmap = (pred > self.det_thresh).astype(np.uint8) * 255
        contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        src_h, src_w = img.shape[:2]
        boxes: list[dict] = []
        for contour in contours[:1000]:
            box, side = get_mini_boxes(contour)
            if side < 3:
                continue
            score = box_score_fast(pred, box)
            if score < self.box_thresh:
                continue
            expanded = unclip(box, self.unclip_ratio)
            if expanded is None:
                continue
            box, side = get_mini_boxes(expanded)
            if side < 5:
                continue
            box[:, 0] = np.clip(np.round(box[:, 0] / ratio_w), 0, src_w - 1)
            box[:, 1] = np.clip(np.round(box[:, 1] / ratio_h), 0, src_h - 1)
            boxes.append({"box": box.astype(np.float32), "det_score": score})
        ordered = sorted_boxes([x["box"] for x in boxes])
        lookup = {tuple(x["box"].reshape(-1).tolist()): x for x in boxes}
        return [lookup[tuple(box.reshape(-1).tolist())] for box in ordered]

    @staticmethod
    def rec_preprocess(img: np.ndarray) -> np.ndarray:
        channels, height, width = 3, 48, 320
        h, w = img.shape[:2]
        resized_w = min(width, max(1, int(math.ceil(height * w / float(h)))))
        resized = cv2.resize(img, (resized_w, height)).astype(np.float32)
        resized = resized.transpose(2, 0, 1) / 255.0
        resized = (resized - 0.5) / 0.5
        padded = np.zeros((channels, height, width), dtype=np.float32)
        padded[:, :, :resized_w] = resized
        return padded[None]

    def decode(self, pred: np.ndarray) -> tuple[str, float]:
        indices = pred.argmax(axis=1)
        probs = pred.max(axis=1)
        text, conf = [], []
        previous = -1
        for idx, prob in zip(indices, probs):
            idx = int(idx)
            if idx != 0 and idx != previous and idx < len(self.chars):
                text.append(self.chars[idx])
                conf.append(float(prob))
            previous = idx
        return "".join(text), (float(np.mean(conf)) if conf else 0.0)

    def ocr(self, path: Path) -> dict:
        image = cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise RuntimeError(f"cannot read {path}")
        start = time.perf_counter()
        det_start = time.perf_counter()
        dets = self.detect(image)
        det_s = time.perf_counter() - det_start
        rec_start = time.perf_counter()
        lines = []
        for item in dets:
            crop = get_rotate_crop_image(image, item["box"])
            if crop.size == 0:
                continue
            output = self.rec.run(None, {self.rec_input: self.rec_preprocess(crop)})[0][0]
            text, confidence = self.decode(output)
            if text:
                lines.append({
                    "text": text,
                    "confidence": round(confidence, 4),
                    "det_score": round(float(item["det_score"]), 4),
                    "box": np.round(item["box"], 1).tolist(),
                    "crop_shape": list(crop.shape),
                })
        rec_s = time.perf_counter() - rec_start
        return {
            "text": "\n".join(x["text"] for x in lines),
            "lines": lines,
            "timing": {
                "det_s": round(det_s, 4),
                "rec_s": round(rec_s, 4),
                "total_s": round(time.perf_counter() - start, 4),
            },
            "detected_boxes": len(dets),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()
    paths = sorted(p for p in args.image_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"})
    init_start = time.perf_counter()
    engine = OfficialV5Mobile()
    print("V5_INIT " + json.dumps({
        "engine": "official_ppocr_v5_mobile_fp32_onnx",
        "image_count": len(paths),
        "init_s": round(time.perf_counter() - init_start, 4),
        "det_providers": engine.det.get_providers(),
        "rec_providers": engine.rec.get_providers(),
        "det_config": engine.det_cfg["PostProcess"],
        "rec_chars": len(engine.chars),
    }, ensure_ascii=False), flush=True)
    all_results = []
    for path in paths:
        try:
            result = engine.ocr(path)
            result["image"] = path.name
        except Exception as exc:
            result = {"image": path.name, "error": f"{type(exc).__name__}: {exc}"}
        all_results.append(result)
        print("V5_RESULT " + json.dumps(result, ensure_ascii=False), flush=True)
    if args.output:
        args.output.write_text(json.dumps(all_results, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
