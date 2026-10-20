#!/usr/bin/env python3
"""Read-only static checks for a checkout; does not import hardware runtimes."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REQUIRED_FILES = (
    "backend/gateway/gateway_8799_v2.py",
    "backend/gateway/boot_supervisor.py",
    "backend/gateway/shared_ocr_runtime.py",
    "backend/gateway/frame_geometry.py",
    "backend/qa/medicine_qa_api.py",
    "backend/qa/medicine_qa_engine.py",
    "backend/qa/比赛药品清单.json",
    "backend/qa/通用问题银行.json",
    "backend/qa/药品适用范围词表.json",
    "backend/qa/药品知识卡片/01_维生素D滴剂.json",
    "backend/qa/embedding/bge-small-zh-v1.5/encoder_int8.onnx",
    "backend/qa/embedding/bge-small-zh-v1.5/vocab.txt",
    "backend/llm/llm_server_launcher.py",
    "backend/llm/model_profiles.json",
    "backend/audio/audio_role_manager.py",
    "backend/voice/asr/k1_asr_backend_service.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/__init__.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/llm_asr.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/__init__.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/frontend.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/postprocess_utils.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/sensevoice_bin.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/tokenizer.py",
    "backend/voice/asr/spacemit_asr_runtime/llm_asr/models/utils.py",
    "backend/voice/asr/sensevoice_official_optimized/model_quant_optimized.onnx",
    "backend/medicine_box_recognition/models/medicine_name_database_17.json",
    "backend/medicine_box_recognition/scripts/bench_v5_int8_k1_mixed_opt.py",
    "backend/medicine_box_recognition/scripts/bench_v5_int8_k1_mixed.py",
    "backend/medicine_box_recognition/scripts/eval_ppocr_v5_mobile_fp32_official.py",
    "backend/medicine_box_recognition/scripts/k1_live_yolo_v5ocr_preview_640.py",
    "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_det_onnx_infer/inference.onnx",
    "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_det_onnx_infer/inference.yml",
    "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_rec_onnx_infer/inference.onnx",
    "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_rec_onnx_infer/inference.yml",
    "backend/medicine_box_recognition/models/ppocr_v5_static_int8_20260718/validation_20260801/ppocr_v5_mobile_det_int8_audited_portrait_1x3x960x544.onnx",
    "backend/medicine_prescription_ocr/models/ppocrv5_mobile_rec_prescription_int8_1x3x48x320.onnx",
    "backend/pill_detection/models/k1_pill_yolov8n_640_static_qdq_convonly_int8_minmax_curated64.onnx",
    "backend/face/vendor/face_det_rec/onnx_model/static_int8_20260718_batch2_det/yolov5n-face_320_cut_static_qdq_int8.onnx",
    "backend/face/vendor/face_det_rec/onnx_model/static_int8_20260717/arcface_mobilefacenet_cut_static_qdq_int8.onnx",
    "backend/cabinet/cabinet_controller.py",
    "backend/scheduler/medication_scheduler.py",
    "frontend/index.html",
    "frontend/app.js",
    "frontend/styles.css",
    "frontend/assets/audio/tts-modular/voice_cache_index.json",
    "models/llm/Qwen3-0.6B-Q4_0.gguf",
    "deployment/systemd/smart-medicine-llm.service",
    "deployment/systemd/smart-medicine-qa.service",
    "deployment/systemd/smart-medicine-usb-audio.service",
    "deployment/systemd/smart-medicine-gateway.service",
    "deployment/systemd/smart-medicine-asr.service",
    "deployment/systemd/smart-medicine-kiosk.service",
    "deployment/start-kiosk.sh",
    "deployment/kiosk.env.example",
)
PRIVATE_PATH_MARKERS = (
    "/home/",
    "/root/.cache/torch",
    "D:\\嵌入式比赛D盘",
    "C:\\Users\\",
)


def check(condition: bool, message: str, failures: list[str]) -> None:
    print(f"[{'OK' if condition else 'FAIL'}] {message}")
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    for relative in REQUIRED_FILES:
        check((ROOT / relative).is_file(), f"required file: {relative}", failures)

    installer_path = ROOT / "deployment/install-user-services.sh"
    try:
        installer_text = installer_path.read_text(encoding="utf-8")
        installer_ok = r"\${BASH_SOURCE[0]}" not in installer_text and r"\${units[@]}" not in installer_text
        check(installer_ok, "installer Bash variables are expandable", failures)
    except (OSError, UnicodeError) as exc:
        check(False, f"installer script readable ({exc})", failures)

    unit_paths = list((ROOT / "deployment/systemd").glob("*.service"))
    escaped_units = []
    for path in unit_paths:
        try:
            if r"\${PROJECT_ROOT}" in path.read_text(encoding="utf-8"):
                escaped_units.append(str(path.relative_to(ROOT)))
        except (OSError, UnicodeError) as exc:
            escaped_units.append(f"{path.relative_to(ROOT)} ({exc})")
    check(not escaped_units, "systemd project-root variables are expandable", failures)
    for unit in escaped_units:
        print(f"  invalid unit variable: {unit}")

    asr_unit = ROOT / "deployment/systemd/smart-medicine-asr.service"
    try:
        asr_text = asr_unit.read_text(encoding="utf-8")
        for marker in (
            "CPUAffinity=0 1 2 3",
            "LD_LIBRARY_PATH=/usr/share/asr-llm-tts/.voice2code/lib/python3.12/site-packages/onnxruntime_genai",
            "K1_ASR_THREADS=1",
            "K1_ASR_USE_SPACEMIT=0",
        ):
            check(marker in asr_text, f"ASR service retains K1 setting {marker}", failures)
    except (OSError, UnicodeError) as exc:
        check(False, f"ASR service template readable ({exc})", failures)

    json_files = list(ROOT.rglob("*.json"))
    for path in json_files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            check(False, f"valid JSON: {path.relative_to(ROOT)} ({exc})", failures)
    check(bool(json_files), "JSON configuration and data files found", failures)

    yaml_files = [
        ROOT / "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_det_onnx_infer/inference.yml",
        ROOT / "backend/medicine_box_recognition/models/ppocr_v5_mobile_xquant_int8_k1_mixed_20260717/PP-OCRv5_mobile_rec_onnx_infer/inference.yml",
    ]
    if importlib.util.find_spec("yaml"):
        try:
            import yaml

            for path in yaml_files:
                yaml.safe_load(path.read_text(encoding="utf-8"))
            check(True, "OCR inference YAML files parse", failures)
        except Exception as exc:
            check(False, f"OCR inference YAML parse ({exc})", failures)
    else:
        print("[SKIP] PyYAML unavailable; OCR inference YAML syntax was not parsed")

    python_files = list(ROOT.rglob("*.py"))
    for path in python_files:
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            check(False, f"Python syntax: {path.relative_to(ROOT)} ({exc})", failures)
    check(bool(python_files), "Python source files found", failures)

    profile_path = ROOT / "backend/llm/model_profiles.json"
    try:
        profiles = json.loads(profile_path.read_text(encoding="utf-8"))
        for profile_name, profile in profiles["profiles"].items():
            model = (profile_path.parent / profile["model_path"]).resolve()
            check(model.is_file(), f"LLM model exists for {profile_name}", failures)
            if model.is_file():
                expected_size = profile.get("expected_size_bytes")
                check(expected_size is None or model.stat().st_size == expected_size,
                      f"LLM model size matches profile for {profile_name}", failures)
                expected_hash = profile.get("sha256")
                if expected_hash:
                    actual_hash = hashlib.sha256(model.read_bytes()).hexdigest()
                    check(actual_hash.lower() == expected_hash.lower(),
                          f"LLM model SHA256 matches profile for {profile_name}", failures)
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        check(False, f"LLM profile validation ({exc})", failures)

    voice_index_path = ROOT / "frontend/assets/audio/tts-modular/voice_cache_index.json"
    try:
        voice_index = json.loads(voice_index_path.read_text(encoding="utf-8"))
        entries = voice_index["items"]
        missing = []
        for entry in entries:
            relative = str(entry["wav"]).replace("\\", "/")
            if not (voice_index_path.parent / relative).is_file():
                missing.append(relative)
        check(len(entries) == voice_index.get("wav_count"), "voice cache count matches index", failures)
        check(not missing, f"all indexed voice files exist ({len(entries)} entries)", failures)
        if missing:
            for name in missing[:10]:
                print(f"  missing voice: {name}")
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
        check(False, f"voice index validation ({exc})", failures)

    app_path = ROOT / "frontend/app.js"
    try:
        app_text = app_path.read_text(encoding="utf-8")
        direct_audio = set(re.findall(r"[\"'](assets/audio/wang-grandpa/[^\"']+\.wav)[\"']", app_text))
        missing_direct_audio = sorted(
            relative for relative in direct_audio
            if not (ROOT / "frontend" / relative).is_file()
        )
        check(bool(direct_audio), "direct UI audio references found", failures)
        check(not missing_direct_audio,
              f"all directly referenced UI audio files exist ({len(direct_audio)} files)", failures)
        if missing_direct_audio:
            for name in missing_direct_audio[:10]:
                print(f"  missing UI audio: {name}")
    except (OSError, UnicodeError) as exc:
        check(False, f"UI audio references readable ({exc})", failures)

    runtime_files = [
        path for base in (ROOT / "backend", ROOT / "frontend")
        for path in base.rglob("*")
        if path.is_file() and path.suffix.lower() in {".py", ".json", ".js", ".html", ".sh"}
    ]
    marker_hits = []
    for path in runtime_files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            continue
        for marker in PRIVATE_PATH_MARKERS:
            if marker.lower() in text.lower():
                marker_hits.append(f"{path.relative_to(ROOT)}: {marker}")
    check(not marker_hits, "no known workstation-specific paths in runtime source", failures)
    for hit in marker_hits:
        print(f"  path marker: {hit}")

    face_data = ROOT / "data/faces"
    private_face_files = [
        path for path in face_data.rglob("*")
        if path.is_file() and path.name not in {".gitkeep", "README.md"}
    ] if face_data.exists() else []
    check(face_data.is_dir(), "runtime face-data directory exists", failures)
    check(not private_face_files, "face-data directory contains no user records", failures)

    print(f"Checked {len(REQUIRED_FILES)} required files, {len(json_files)} JSON files, "
          f"and {len(python_files)} Python files.")
    print("Static checks do not verify licenses, system packages, connected peripherals, or live inference.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
