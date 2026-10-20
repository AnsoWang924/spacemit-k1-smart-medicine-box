# Dependencies and Runtime Closure

This list separates files shipped by this repository from packages supplied by the target K1 image. A clean checkout is not a self-contained operating system image.

## Repository files required by services

| Service or feature | Repository paths |
| --- | --- |
| API gateway and resident supervisor | backend/gateway, backend/cabinet, backend/scheduler |
| Medication QA API | backend/qa, including 17 drug cards, question data, resolver, vendored pypinyin, tokenizer and BGE ONNX encoder |
| Local LLM | backend/llm/model_profiles.json and models/llm/Qwen3-0.6B-Q4_0.gguf |
| Medicine-box OCR | backend/medicine_box_recognition/models/scripts and the gateway OCR runtime; the scripts directory contains imported runtime modules, not just standalone benchmarks |
| Prescription OCR | backend/medicine_box_recognition/models and backend/medicine_prescription_ocr/models |
| Pill counting | backend/pill_detection and the model selected by backend/gateway/pill_count_v2_service.py |
| Face recognition | backend/face/vendor/face_det_rec and empty data/faces runtime directory |
| Web UI and optional kiosk | frontend, including API modules, the 185 modular audio assets indexed by `voice_cache_index.json`, and 35 audio clips referenced directly by `app.js`; kiosk also needs a K1 graphical session and Chromium-compatible browser |
| ASR source/model | backend/voice/asr, including the `spacemit_asr_runtime/llm_asr` compatibility overlay; execution also requires the SpaceMIT ASR runtime installed on K1 |

The repository does not contain enrolled face vectors. It also does not bundle the K1 camera JSON files or the operating-system libraries used by the camera pipeline.

## K1 system dependencies

The verified board ran RISC-V Bianbu Linux with Python 3.12.3 and user-level systemd. The active LLM service used the native SpaceMIT llama-server from the llama.cpp-tools-spacemit package. The QA/OCR/face/pill paths require the matching SpaceMIT-enabled ONNX Runtime and execution provider, NumPy, OpenCV, Pillow, PyYAML and image-processing dependencies used by the selected code paths. OCR additionally imports `pyclipper`, and the OCR/pill GStreamer workers import Python `gi` and `gi.repository.Gst`; provide the Bianbu equivalents of `python3-gi`, `gir1.2-gstreamer-1.0` and required GStreamer plugins. Confirm these package names against the board's configured repositories before installation.

Camera features depend on the K1 BSP camera drivers, GStreamer plugins and camera JSON files under /usr/share/camera_json. Audio features depend on ALSA/PipeWire and the configured USB device roles. The ASR service also expects `/usr/share/asr-llm-tts/.voice2code`, its Python 3.12 ONNX Runtime GenAI library directory, and `/opt/spacemit-asr`. These are platform packages or system assets, not files a Git checkout can replace. Its service template preserves the observed library path and restricts execution to CPUs 0-3 because SenseVoice raised `SIGILL` on CPUs 4-7 during real inference on the captured K1.

The current K1 snapshot reported llama.cpp-tools-spacemit 0.1.1-bpo1+6, spacemit-asr-server 0.0.20 and spacemit-sensevoice-model 0.0.4. Candidate dependency files name onnxruntime 1.20.1+spacemit, spacemit-ort 2.0.0+beta2, OpenCV 4.12.0.88 and Pillow 11.2.1. The OCR runtime also needs PyYAML and pyclipper. Treat these as observed versions, not a complete lockfile for every feature.

The ASR compatibility overlay contains the eight Python modules copied from the active project tree; two differ from the `/opt/spacemit-asr` package to pass the thread count and configure the optional SpaceMIT execution provider. Keep this overlay with the ASR service. Its third-party license and redistribution terms still need confirmation before publication. The exact package versions and installation source must be confirmed against the target Bianbu image before publication. Do not run a generic pip install over the SpaceMIT runtime: replacing the vendor ONNX Runtime or llama-server may make the RISC-V inference path fail.

## Model and asset distribution

The current candidate contains model weights and audio that are hundreds of megabytes in total. .gitattributes routes ONNX, GGUF and WAV files through Git LFS. A clone must install Git LFS and fetch LFS objects. Each model and audio asset still needs a documented source and license before public distribution.

The Qwen profile includes the expected size and SHA256. `deployment/preflight.py` checks that metadata, all 185 modular voice WAVs referenced by the index, and all 35 UI clips referenced directly from `app.js`. These checks establish file presence and integrity, not audio ownership or personal-data status.

The source and asset provenance inventory is in `docs/THIRD_PARTY_NOTICES.md`. The local candidate has no root `LICENSE`; the existing GitHub repository has an MIT license for its older contents, which must be deliberately carried into a separate repository if that is the intended project license.

## Optional or unverified components

- ASR is not ready without the microphone role and the board-provided runtime. Its unit is installed as a template but is not enabled by the default installer.
- TTS Python source is included, but the active board TTS service was masked and its runtime/model tree under /usr/share/asr-llm-tts and /opt/smart_medicine_tts is not included.
- Camera, display and cabinet actuator behavior could not be verified in the current hardware state.
