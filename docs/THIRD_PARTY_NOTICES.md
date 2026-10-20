# Third-Party and Asset Provenance Inventory

Review date: 2026-10-20

This is a provenance inventory. Per the project owner's instruction, publicly published upstream model weights are treated as public and their individual license review is not a release gate for this project. The rows below remain useful for recording model identity and lineage. This position does not cover the separate project-level LICENSE, copied third-party source code, recorded voices, personal data, or platform binaries.

| Bundled component | Evidence in the local copy | Release work |
| --- | --- | --- |
| `backend/qa/vendor/pypinyin` | Version 0.55.0 and MIT are recorded in the archived wheel metadata. The corresponding MIT text is included at `backend/qa/vendor/pypinyin/LICENSE.txt`. | Preserve the notice and verify the vendored files match that release. |
| `backend/face/vendor/face_det_rec` | Copied Python package; the local snapshot does not identify its upstream version. | Record source and version for maintenance; this is copied source code, not a model-weight row. |
| `backend/voice/asr/spacemit_asr_runtime/llm_asr` | Eight Python files are from the K1 project overlay. Two files differ from the platform `/opt/spacemit-asr` copy to pass thread settings and optionally select the SpaceMIT execution provider. Two model files contain MIT header comments; no package-level license file was found. | Record upstream project/version and preserve any applicable source-code notices. |
| `models/llm/Qwen3-0.6B-Q4_0.gguf` | The profile records the model name, size, and SHA-256. | Record upstream model/revision and quantization provenance when available. |
| `backend/qa/embedding/bge-small-zh-v1.5` | Config and tokenizer files identify the model family. | Record upstream model/revision when available. |
| `backend/voice/asr/sensevoice_official_optimized` | Model/config files are included. | Record source/revision when available. |
| `backend/medicine_box_recognition/models`, `backend/medicine_prescription_ocr/models` | PaddleOCR-family names/configuration are present. | Record upstream origin and conversion/quantization lineage when available. |
| `backend/pill_detection/models` | Runtime-selected models and five alternate ONNX files are present. | Record model names and export lineage when available; decide whether alternate files belong in a future cleanup. |
| `backend/face/vendor/face_det_rec/onnx_model` | Face detector and recognition weights are present. | Record model identity and training/export provenance when available. |
| `frontend/assets/audio` | 185 modular clips are listed by `voice_cache_index.json`; 35 additional clips are directly referenced by `frontend/app.js`. All 220 are included. | Confirm speaker consent, recording ownership, names/privacy, and redistribution rights; anonymize or replace clips where needed. |
| Medicine examples and QA content | Drug cards, question bank, medicine list, and UI examples contain health-related content. Local audit found named demonstration records. | Confirm authorship and sources; establish that personal examples are fictional or anonymize them before release. |
| Bianbu, K1 BSP, SpaceMIT runtime, system ASR/TTS packages | These are documented target-system dependencies and are not fully included in this repository. | Document official installation sources and compatible versions. Do not copy vendor packages into this repository without redistribution permission. |

The local candidate has no root `LICENSE`. The existing GitHub repository currently contains an MIT license for its older contents; when creating a separate repository, decide whether to carry that license into the new project. Audio, demonstration records, and copied source packages remain distinct from the published model weights described above.
