#!/usr/bin/env python3
"""K1 benchmark: PP-OCRv5 mobile INT8, with documented EP compatibility split."""

from __future__ import annotations

import argparse
import json
import re
import time
from pathlib import Path

import onnxruntime as ort
import spacemit_ort  # noqa: F401 - registers SpaceMITExecutionProvider
import yaml

import eval_ppocr_v5_mobile_fp32_official as base


EP = "SpaceMITExecutionProvider"


CPU_SPLIT_CONVS = "Conv.7;Conv.11;Conv.21;Conv.29"


def strict_providers():
    return [(EP, {
        "SPACEMIT_EP_INTRA_THREAD_NUM": "1",
        "SPACEMIT_EP_USE_GLOBAL_INTRA_THREAD": "1",
        "SPACEMIT_EP_DISABLE_FLOAT16_EPILOGUE": "1",
        # These four asymmetric-stride depthwise convs fail K1's fused INT8
        # compiler. They remain INT8 but execute on CPU; all other supported
        # subgraphs stay on the SpaceMIT EP.
        "SPACEMIT_EP_DISABLE_OP_NAME_FILTER": CPU_SPLIT_CONVS,
    }), "CPUExecutionProvider"]


class Int8V5(base.OfficialV5Mobile):
    def __init__(self, model_root: Path) -> None:
        self.det_dir = model_root / "PP-OCRv5_mobile_det_onnx_infer"
        self.rec_dir = model_root / "PP-OCRv5_mobile_rec_onnx_infer"
        self.det_cfg = yaml.safe_load((self.det_dir / "inference.yml").read_text(encoding="utf-8"))
        self.rec_cfg = yaml.safe_load((self.rec_dir / "inference.yml").read_text(encoding="utf-8"))
        self.det = ort.InferenceSession(str(self.det_dir / "inference.onnx"), providers=strict_providers())
        self.rec = ort.InferenceSession(str(self.rec_dir / "inference.onnx"), providers=strict_providers())
        self.det_input = self.det.get_inputs()[0].name
        self.rec_input = self.rec.get_inputs()[0].name
        pp = self.det_cfg["PostProcess"]
        self.det_thresh = float(pp["thresh"])
        self.box_thresh = float(pp["box_thresh"])
        self.unclip_ratio = float(pp["unclip_ratio"])
        self.resize_long = int(self.det_cfg["PreProcess"]["transform_ops"][2]["DetResizeForTest"]["resize_long"])
        self.chars = ["blank", *list(self.rec_cfg["PostProcess"]["character_dict"]), " "]
        if EP not in self.det.get_providers() or EP not in self.rec.get_providers():
            raise RuntimeError(f"SpaceMIT EP unavailable: det={self.det.get_providers()}, rec={self.rec.get_providers()}")


def truth(name: str) -> str:
    return re.sub(r"\s*\(\d+\)$", "", Path(name).stem)


def norm(value: str) -> str:
    return re.sub(r"[\s\u3000（）()]", "", value).lower()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image_dir", type=Path)
    parser.add_argument("--model-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = sorted(p for p in args.image_dir.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp"})
    if len(paths) != 12:
        raise RuntimeError(f"expected exactly 12 representative images, got {len(paths)}")
    init_start = time.perf_counter()
    engine = Int8V5(args.model_root)
    init_s = time.perf_counter() - init_start
    for path in paths[:3]:
        engine.ocr(path)
    rows = []
    for path in paths:
        result = engine.ocr(path)
        expected = truth(path.name)
        rows.append({
            "image": path.name,
            "truth": expected,
            "raw_ocr": result["text"],
            "hit": norm(expected) in norm(result["text"]),
            "boxes": result["detected_boxes"],
            "timing": result["timing"],
        })
    totals = [row["timing"]["total_s"] for row in rows]
    report = {
        "engine": "ppocr_v5_mobile_xquant_int8_det_rec",
        "provider_requirement": "SpaceMITExecutionProvider explicitly requested for both sessions",
        "int8_cpu_split_convs": CPU_SPLIT_CONVS,
        "det_providers": engine.det.get_providers(),
        "rec_providers": engine.rec.get_providers(),
        "init_s": round(init_s, 4),
        "warmup_images": 3,
        "accuracy": {"hits": sum(row["hit"] for row in rows), "total": len(rows)},
        "timing": {"mean_total_s": round(sum(totals) / len(totals), 4), "min_total_s": round(min(totals), 4), "max_total_s": round(max(totals), 4)},
        "rows": rows,
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
