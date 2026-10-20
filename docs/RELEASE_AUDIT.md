# Release Audit

Audit date: 2026-10-20

This is a read-only comparison of the live K1 deployment and the prepared local candidate at D:/智能药盒项目开源/K1智能用药平台_开源整理. The archived full copy and live application files were not modified.

## Current K1 evidence

- The user services smart-medicine-gateway, smart-medicine-qa, smart-medicine-llm and smart-medicine-usb-audio were active.
- Ports 8799, 8790 and 8080 listened on 127.0.0.1.
- Gateway /api/v1/health, QA /api/v1/health, LLM /health and gateway /ui/ returned HTTP 200. QA reported retrieval and LLM ready.
- Port 8765 had no listener. The microphone role was absent; the ASR process could not become ready.
- The full gateway readiness endpoint /api/v2/health/ready was not queried. TTS was masked.
- The local kiosk could not connect to a Wayland display. Camera and cabinet physical I/O were not exercised.

These observations establish service-level availability, not end-to-end medication workflow readiness.

## Hash comparison

| Component | Live K1 SHA256 | Candidate SHA256 | Result |
| --- | --- | --- | --- |
| Gateway | 5bbef9514a5be3d5c223ee83aa7acd7876481bcad9ba500c12b51294c72622d9 | b71b918ce98b4795061741f5083b468cfa4150a5fdd312f6901895397ef5a303 | Expected portability edits |
| Pill-count gateway adapter | 60250f2125f89655e471e793c202eda801a6464b439807a57d95938e4ed5d08b | 98ee72487b6fd35e73a5492fead8942de8841aeb940f19397e4c474fea6565c2 | Expected project-relative path edit |
| QA API | 7e9a8e75801122aeff8cd2188a17972d223e3562222b83dc0634e707157c6a12 | 7e9a8e75801122aeff8cd2188a17972d223e3562222b83dc0634e707157c6a12 | Exact match |
| LLM launcher | bc44bb5456b50eafd342adcbfd6d932b51036c3c470535283e99a122fe5aeac3 | 4541451fa958e062be6d7bf3b17f058965bfbea1b05b9c3d93a0660869c71cff | Expected portability edits |
| Audio role manager | 801cbe1b54bcb05ea1f8d1dbab3e6409647cec299a3a89f8a1f90f2d83dcbe37 | 801cbe1b54bcb05ea1f8d1dbab3e6409647cec299a3a89f8a1f90f2d83dcbe37 | Exact match |
| UI index.html | fc8968a871f2be2da9972a98aa0a2da6f220a4ab3916aea7622149484b73e3f3 | fc8968a871f2be2da9972a98aa0a2da6f220a4ab3916aea7622149484b73e3f3 | Exact match |
| UI app.js | 225e12ff0ec68e75fb8de0feeaee518a5ea1425d2b967829f14f7ee32cca5515 | 225e12ff0ec68e75fb8de0feeaee518a5ea1425d2b967829f14f7ee32cca5515 | Exact match |
| Qwen GGUF | 33bcc57074ec7b6eada5a90651ee546ec0c2b2710022c22baf9f1b2dd1e8f75cb | 33bcc57074ec7b6eada5a90651ee546ec0c2b2710022c22baf9f1b2dd1e8f75cb | Exact match |

The gateway differences change hard-coded cabinet, medicine-box, face, scheduler and UI paths to paths derived from the checkout. The pill-count adapter similarly resolves its detection module and model from its own location. The LLM launcher resolves its relative model path from the selected configuration file. The service commands now take their project paths from PROJECT_ROOT in the user environment file; entry points resolve their data and imports from __file__, so the service units do not depend on a fixed working directory. These portability edits still need a K1 smoke test before claiming parity.

The entire selected frontend tree (241 files) matches the archived UI byte-for-byte. This includes all 220 WAV files; their SHA-256 hashes were checked individually. The QA API, audio-role manager and primary UI files also match their archived sources exactly.

## Release blockers

1. The local candidate has no root `LICENSE`. The public GitHub repository has an MIT license for its older contents; decide whether the new project should carry that license into a separate repository.
2. The UI includes named medication examples and the audio bundle includes named voice recordings. Confirm that examples are fictional and audio can be published, or replace/anonymize them as appropriate.
3. K1 BSP, camera JSON, SpaceMIT runtime packages, ASR runtime and TTS runtime are not fully shipped by the repository. The new-board guide describes the required components, but exact official installation sources and a clean-image version matrix remain unverified.
4. No physical camera, screen, microphone, speaker or cabinet actuator was attached for this audit. Run the full workflow on a clean K1 after connecting the documented hardware.
5. The candidate still contains extracted Python bytecode files under the vendored pypinyin package. They are ignored by `.gitignore`, so Git will omit them, though the local folder is not physically cache-free.

## GitHub repository check

On 2026-10-20, the screenshot repository `AnsoWang924/smart-medicine-box` was reachable and public. Its `main` branch pointed to commit `3e3a74b08c55c55a5367ef3c3494716c3c55fbc3`, whose tree contains the earlier flat K1 voice-input project and an MIT `LICENSE`; it does not contain this candidate's structured platform tree or its LFS assets. The local candidate is not a Git repository and has no configured remote. A separate empty GitHub repository is the recommended first publication target; do not force-push this candidate over the existing `main` branch.

## Packaging follow-up

Static review found that the installer and service templates originally contained escaped shell/systemd variables, which would have prevented clean deployment. The candidate now uses expandable ${BASH_SOURCE[0]}, ${units[@]} and ${PROJECT_ROOT} references. The installer and kiosk shell scripts pass bash -n; systemd-analyze --user verify accepts the service templates. The verifier emitted only executable/world-writable permission warnings because the candidate is on a Windows-mounted filesystem; it returned success. The preflight script checks for regressions in those escaped variables.

The candidate now includes an optional Chromium kiosk unit and a launcher that uses the cloned project's frontend through the gateway. The core installer installs that unit but leaves it disabled until a graphical session, display environment and browser are ready. The USB audio manager waits for the user audio session, PipeWire and WirePlumber. Neither kiosk startup nor audio device operation has been exercised on the board after these template changes.

Static closure review also found four OCR scripts imported by the live gateway worker but absent from the candidate. Those exact archive sources are now included under `backend/medicine_box_recognition/scripts/` and required by preflight. Preflight now also requires the OCR worker's `frame_geometry.py` helper and both model `inference.yml` files. The worker explicitly adds the gateway module directory to its import path. `backend/face/run_face.sh` no longer forces UID 1000 or a fixed Wayland socket; it respects the current user session and keeps a fallback for the common `wayland-0` name. The OCR path's `python3-gi`, GStreamer typelib, PyYAML and pyclipper system dependencies are now called out in `DEPENDENCIES.md`. These changes remain pending a K1 runtime smoke test.

ASR closure review on 2026-10-20 found that the service imports the project-local `spacemit_asr_runtime` overlay before `/opt/spacemit-asr`. The candidate had omitted this overlay even though its service calls `llm_asr(..., intra_op_num_threads=...)`; the stock `/opt` implementation has no such parameter and would fail during model initialization. The candidate now includes all eight overlay Python modules, each SHA-256 matched against the read-only archive. Its ASR unit also preserves the observed CPUs 0-3 affinity (CPUs 4-7 raised `SIGILL` during inference), ONNX Runtime GenAI library path, single-thread setting and CPU-provider selection. Preflight requires the overlay and these K1 unit settings. This is static closure evidence only; ASR is still untested on a clean K1 with the required runtime and microphone. The overlay is derived from the platform's ASR package, so its redistribution terms must be resolved before release.

The UI references two audio groups: 185 WAV files through `voice_cache_index.json` and 35 clips by path in `frontend/app.js`. All 220 are present; preflight now checks both groups. The 35 directly referenced files were not covered by the earlier index-only check and remain subject to voice/privacy clearance. Five alternate pill detector ONNX files had no references in the candidate's runtime source or documentation. They remain physically present because removal was rejected by automatic approval review; the read-only archive remains unchanged.

## Audit-side write incident

One local health probe issued curl -o with /tmp/health.readonly as its K1 output target; a parallel probe reported writing /tmp/codex-health-body. I did not verify or remove either path, because the requested K1 review was read-only. No application source, service configuration or user data was intentionally changed on K1.

## Local checks

Local checks rerun on 2026-10-20 pass for 54 required files, 33 JSON files, 87 Python files, both OCR YAML files, the Qwen model size/hash, all 185 indexed WAV files and all 35 direct UI audio references. All eight ASR overlay files match the read-only archive. Five unreferenced alternate pill detector models are still present in the candidate. Bash syntax checks pass for the installer, kiosk launcher and face launcher. `systemd-analyze --user verify` exits successfully; WSL reports executable/world-writable mode warnings because the candidate resides on a Windows-mounted filesystem. These checks do not validate licenses, K1 package installation, peripherals or live inference.

Run python3 deployment/preflight.py on a checkout with Git LFS objects present. The check validates static file closure, installer/systemd variable expansion markers and required ASR settings, JSON, Python syntax, OCR YAML syntax when PyYAML is installed, LLM model size/hash, indexed and direct UI audio references, known workstation path markers and absence of face records.
