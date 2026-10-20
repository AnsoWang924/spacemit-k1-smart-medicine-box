#!/usr/bin/env python3
import argparse
import audioop
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MODEL_PATH = os.environ.get(
    "K1_ASR_MODEL_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "sensevoice_official_optimized"),
)
AUDIO_DEVICE = os.environ.get("K1_ASR_AUDIO_DEVICE", "plughw:CARD=Device,DEV=0")
# Standalone fallback only.  Normal operation reads the live role mapping
# maintained by the USB audio manager before recording.
MIXER_CARD = os.environ.get("K1_ASR_MIXER_CARD", "2")
AUDIO_ROLE_STATUS_PATH = os.environ.get("K1_AUDIO_ROLE_STATUS_PATH", "/run/user/1000/smart-medicine-audio-roles.json")
RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
CHUNK_FRAMES = 1024
CHUNK_BYTES = CHUNK_FRAMES * CHANNELS * SAMPLE_WIDTH
MIN_RECORD_SECONDS = 0.6
MAX_RECORD_SECONDS = 25
DEFAULT_ASR_THREADS = int(os.environ.get("K1_ASR_THREADS", "1"))
VAD_FRAME_MS = 30
VAD_PADDING_MS = 180
VAD_MIN_VOICED_MS = 180
VAD_MIN_RMS = int(os.environ.get("K1_ASR_VAD_MIN_RMS", "220"))
VAD_PEAK_RATIO = float(os.environ.get("K1_ASR_VAD_PEAK_RATIO", "0.10"))
WORK_DIR = "/tmp/k1_asr_backend"
RAW_WAV_PATH = os.path.join(WORK_DIR, "record_input.wav")
ASR_WAV_PATH = os.path.join(WORK_DIR, "record_for_asr.wav")
RUN_WAV_PATH = os.path.join(WORK_DIR, "record_run.wav")
LAST_TEXT_PATH = os.path.join(WORK_DIR, "last_text.txt")
JOBS_DIR = os.path.join(WORK_DIR, "jobs")

state_lock = threading.RLock()
asr_model = None
model_state = "not_loaded"
model_error = ""
record_state = "idle"
record_error = ""
record_process = None
record_thread = None
record_started_at = 0.0
recognition_state = "idle"
recognition_error = ""
recognition_job_id = ""
last_text = ""
last_audio = {}
level = 0
peak = 0
shutdown_event = threading.Event()


def now_ms():
    return int(time.time() * 1000)


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)


def ensure_work_dir():
    os.makedirs(WORK_DIR, exist_ok=True)
    os.makedirs(JOBS_DIR, exist_ok=True)


def job_paths(session_id):
    safe_id = "".join(character for character in str(session_id) if character.isalnum() or character in "-_")
    if not safe_id:
        raise ValueError("invalid_asr_job_id")
    root = os.path.join(JOBS_DIR, safe_id)
    os.makedirs(root, exist_ok=True)
    return {
        "root": root,
        "raw": os.path.join(root, "record_input.wav"),
        "asr": os.path.join(root, "record_for_asr.wav"),
        "run": os.path.join(root, "record_run.wav"),
    }


def configure_asr_runtime(thread_count):
    thread_value = str(max(1, int(thread_count)))
    for name in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(name, thread_value)
    os.environ.setdefault("ORT_NUM_THREADS", thread_value)
    log(f"K1_ASR_RUNTIME threads={thread_value} vad_min_rms={VAD_MIN_RMS} vad_peak_ratio={VAD_PEAK_RATIO}")


def init_microphone():
    _audio_device, mixer_card = current_microphone_route()
    commands = [
        ["amixer", "-c", mixer_card, "set", "Mic", "100%", "cap"],
        ["amixer", "-c", mixer_card, "set", "Auto Gain Control", "on"],
    ]
    for command in commands:
        result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        if result.returncode:
            log(f"ASR_MIC_INIT_FAILED card={mixer_card} command={' '.join(command)} error={result.stderr.strip()}")


def current_microphone_route():
    """Read the live USB-role mapping before each microphone operation.

    Card numbers change with USB enumeration order.  The audio-role manager
    owns the hardware identity lookup and writes this small runtime status
    file; falling back preserves standalone ASR behavior if it is unavailable.
    """
    try:
        with open(AUDIO_ROLE_STATUS_PATH, "r", encoding="utf-8") as source:
            status = json.load(source)
        card_index = status["roles"]["microphone"]["card_index"]
        card_number = str(int(card_index))
        return f"plughw:{card_number},0", card_number
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return AUDIO_DEVICE, MIXER_CARD


def load_model_once():
    global asr_model, model_state, model_error
    with state_lock:
        if model_state in {"ready", "loading"}:
            return
        model_state = "loading"
        model_error = ""
    try:
        # Prefer the user-owned K1 overlay when present; it contains the
        # SpaceMIT-enabled SenseVoice runtime while retaining /opt as fallback.
        overlay = os.environ.get("K1_ASR_RUNTIME_OVERLAY", os.path.join(os.path.dirname(os.path.abspath(__file__)), "spacemit_asr_runtime"))
        sys.path.insert(0, overlay)
        sys.path.insert(1, os.environ.get("SPACEMIT_ASR_ROOT", "/opt/spacemit-asr"))
        from llm_asr.llm_asr import llm_asr

        loaded_model = llm_asr(
            MODEL_PATH,
            intra_op_num_threads=int(os.environ.get("K1_ASR_THREADS", "1")),
        )
        try:
            providers = loaded_model.sense_model.ort_infer.session.get_providers()
            log(f"ASR_ORT_PROVIDERS={providers}")
        except Exception as provider_exc:
            log(f"ASR_ORT_PROVIDERS=unknown:{type(provider_exc).__name__}")
        with state_lock:
            asr_model = loaded_model
            model_state = "ready"
            model_error = ""
        log("VOICE_MODEL_READY")
    except Exception as exc:
        with state_lock:
            asr_model = None
            model_state = "error"
            model_error = f"{type(exc).__name__}: {exc}"
        log(f"VOICE_ERROR=load_model {model_error}")


def preload_async():
    with state_lock:
        if model_state in {"loading", "ready"}:
            return model_state
    threading.Thread(target=load_model_once, daemon=True).start()
    return "loading"


def remove_file(path):
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def recording_loop(session_id):
    global level, peak, record_process, record_thread, record_state, record_error, recognition_state
    hit_time_limit = False
    should_auto_recognize = False
    try:
        paths = job_paths(session_id)
        remove_file(paths["raw"])
        audio_device, _mixer_card = current_microphone_route()
        log(f"ASR_MIC_ROUTE device={audio_device}")
        command = ["arecord", "-D", audio_device, "-f", "S16_LE", "-r", str(RATE), "-c", str(CHANNELS), "-t", "raw"]
        record_process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        with wave.open(paths["raw"], "wb") as wav_file:
            wav_file.setnchannels(CHANNELS)
            wav_file.setsampwidth(SAMPLE_WIDTH)
            wav_file.setframerate(RATE)
            while not shutdown_event.is_set():
                with state_lock:
                    keep_recording = record_state == "recording" and recognition_job_id == session_id
                    elapsed = time.time() - record_started_at
                if not keep_recording:
                    break
                if elapsed >= MAX_RECORD_SECONDS:
                    hit_time_limit = True
                    break
                data = record_process.stdout.read(CHUNK_BYTES)
                if not data:
                    break
                wav_file.writeframes(data)
                current_level = audioop.rms(data, SAMPLE_WIDTH) if data else 0
                current_peak = audioop.max(data, SAMPLE_WIDTH) if data else 0
                with state_lock:
                    level = current_level
                    peak = max(peak, current_peak)
    except Exception as exc:
        with state_lock:
            record_state = "error"
            record_error = f"{type(exc).__name__}: {exc}"
        log(f"VOICE_ERROR=record_audio {record_error}")
    finally:
        try:
            if record_process and record_process.poll() is None:
                record_process.terminate()
                try:
                    record_process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    record_process.kill()
        except Exception:
            pass
        with state_lock:
            record_process = None
            record_thread = None
            level = 0
            should_auto_recognize = hit_time_limit and record_state == "recording" and recognition_job_id == session_id
            if should_auto_recognize:
                record_state = "stopping"
                recognition_state = "running"
        if should_auto_recognize:
            log(f"VOICE_RECORD_AUTO_STOP job_id={session_id}")
            threading.Thread(target=recognize_recording, args=(session_id,), daemon=True).start()


def trim_silence_for_asr(data, sample_width, rate):
    if not data:
        return data, {"trimmed": False, "vad_threshold": VAD_MIN_RMS, "voiced_ms": 0, "trimmed_ms": 0}
    frame_bytes = max(sample_width, int(rate * VAD_FRAME_MS / 1000) * sample_width)
    frame_count = len(data) // frame_bytes
    if frame_count <= 1:
        return data, {"trimmed": False, "vad_threshold": VAD_MIN_RMS, "voiced_ms": 0, "trimmed_ms": 0}
    frame_rms = [audioop.rms(data[i * frame_bytes:(i + 1) * frame_bytes], sample_width) for i in range(frame_count)]
    peak_rms = max(frame_rms) if frame_rms else 0
    threshold = max(VAD_MIN_RMS, int(peak_rms * VAD_PEAK_RATIO))
    voiced_indexes = [index for index, rms in enumerate(frame_rms) if rms >= threshold]
    if not voiced_indexes:
        return data, {"trimmed": False, "vad_threshold": threshold, "voiced_ms": 0, "trimmed_ms": 0}
    voiced_ms = len(voiced_indexes) * VAD_FRAME_MS
    if voiced_ms < VAD_MIN_VOICED_MS:
        return data, {"trimmed": False, "vad_threshold": threshold, "voiced_ms": voiced_ms, "trimmed_ms": 0}
    padding_frames = max(1, int(VAD_PADDING_MS / VAD_FRAME_MS))
    start_frame = max(0, voiced_indexes[0] - padding_frames)
    end_frame = min(frame_count, voiced_indexes[-1] + padding_frames + 1)
    start = start_frame * frame_bytes
    end = end_frame * frame_bytes
    trimmed = data[start:end]
    trimmed_ms = max(0.0, (len(data) - len(trimmed)) / (rate * sample_width) * 1000)
    return trimmed, {
        "trimmed": len(trimmed) < len(data),
        "vad_threshold": threshold,
        "voiced_ms": voiced_ms,
        "trimmed_ms": round(trimmed_ms, 1),
    }


def normalize_for_asr(source_path, target_path):
    with wave.open(source_path, "rb") as wav_file:
        data = wav_file.readframes(wav_file.getnframes())
        sample_width = wav_file.getsampwidth()
        frame_count = wav_file.getnframes()
    raw_duration = frame_count / RATE if RATE else 0
    raw_rms = audioop.rms(data, sample_width) if data else 0
    gain = 1.0
    if 0 < raw_rms < 1800:
        gain = min(16.0, 2800.0 / raw_rms)
        data = audioop.mul(data, sample_width, gain)
    trimmed_data, trim_info = trim_silence_for_asr(data, sample_width, RATE)
    with wave.open(target_path, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(RATE)
        wav_file.writeframes(trimmed_data)
    asr_duration = len(trimmed_data) / (RATE * sample_width) if RATE and sample_width else 0
    trim_info.update({
        "raw_duration": raw_duration,
        "asr_duration": asr_duration,
        "raw_rms": raw_rms,
        "gain": gain,
    })
    return trim_info


def recognize_recording(session_id):
    global recognition_state, recognition_error, last_text, last_audio, record_state
    try:
        with state_lock:
            model = asr_model
            current_peak = peak
        if model is None:
            raise RuntimeError("ASR model is not ready")
        paths = job_paths(session_id)
        with state_lock:
            active_record_thread = record_thread
        if active_record_thread is not None and active_record_thread.is_alive():
            raise RuntimeError("recording_thread_still_alive")
        audio_info = normalize_for_asr(paths["raw"], paths["asr"])
        duration = audio_info["asr_duration"]
        if audio_info["raw_duration"] < MIN_RECORD_SECONDS:
            text = "Recording is too short. Please try again."
            audio_info["infer_seconds"] = 0.0
            audio_info["rtf"] = 0.0
        else:
            shutil.copyfile(paths["asr"], paths["run"])
            started_at = time.perf_counter()
            text = model.audio_to_text(paths["run"]).strip()
            infer_seconds = time.perf_counter() - started_at
            audio_info["infer_seconds"] = round(infer_seconds, 3)
            audio_info["rtf"] = round(infer_seconds / max(duration, 0.001), 3)
            if not text or text in {"。", "."}:
                text = "No clear speech recognized. Please move closer to the microphone and try again."
        with open(LAST_TEXT_PATH, "w", encoding="utf-8") as output:
            output.write(text)
        with state_lock:
            last_text = text
            last_audio = {
                "duration": round(audio_info["raw_duration"], 3),
                "asr_duration": round(audio_info["asr_duration"], 3),
                "raw_rms": audio_info["raw_rms"],
                "peak": current_peak,
                "gain": round(audio_info["gain"], 3),
                "trimmed": audio_info["trimmed"],
                "trimmed_ms": audio_info["trimmed_ms"],
                "vad_threshold": audio_info["vad_threshold"],
                "infer_seconds": audio_info["infer_seconds"],
                "rtf": audio_info["rtf"],
            }
            recognition_state = "done"
            recognition_error = ""
            record_state = "idle"
        log("VOICE_TOGGLE_RESULT=" + text)
        log(
            f"VOICE_TOGGLE_AUDIO duration={audio_info['raw_duration']:.2f} asr_duration={audio_info['asr_duration']:.2f} "
            f"trimmed_ms={audio_info['trimmed_ms']} raw_rms={audio_info['raw_rms']} peak={current_peak} "
            f"gain={audio_info['gain']:.2f} infer_seconds={audio_info['infer_seconds']:.3f} rtf={audio_info['rtf']:.3f}"
        )
    except Exception as exc:
        with state_lock:
            recognition_state = "error"
            recognition_error = f"{type(exc).__name__}: {exc}"
            record_state = "idle"
        log(f"VOICE_ERROR=recognize_audio {recognition_error}")


def start_recording():
    global record_state, record_error, record_thread, record_started_at, recognition_state, recognition_error
    global recognition_job_id, last_text, last_audio, peak
    preload_async()
    with state_lock:
        if model_state != "ready":
            return False, HTTPStatus.CONFLICT, "model_not_ready"
        if record_state == "recording":
            return False, HTTPStatus.CONFLICT, "already_recording"
        if recognition_state == "running":
            return False, HTTPStatus.CONFLICT, "recognition_running"
        session_id = uuid.uuid4().hex
        recognition_job_id = session_id
        recognition_state = "recording"
        recognition_error = ""
        record_state = "recording"
        record_error = ""
        record_started_at = time.time()
        last_text = ""
        last_audio = {}
        peak = 0
    record_thread = threading.Thread(target=recording_loop, args=(session_id,), daemon=True)
    record_thread.start()
    log(f"VOICE_RECORD_START job_id={session_id}")
    return True, HTTPStatus.OK, session_id


def stop_recording(expected_session_id=""):
    global record_state, recognition_state
    with state_lock:
        if record_state != "recording":
            return False, HTTPStatus.CONFLICT, "not_recording"
        session_id = recognition_job_id
        if expected_session_id and str(expected_session_id) != session_id:
            return False, HTTPStatus.CONFLICT, "job_mismatch"
        record_state = "stopping"
        recognition_state = "running"
        process = record_process
        thread = record_thread
    if process is not None and process.poll() is None:
        try:
            process.terminate()
        except Exception:
            pass
    if thread:
        thread.join(timeout=3)
    if thread is not None and thread.is_alive():
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except Exception:
                pass
        thread.join(timeout=1)
    if thread is not None and thread.is_alive():
        with state_lock:
            record_state = "error"
            recognition_state = "error"
        return False, HTTPStatus.GATEWAY_TIMEOUT, "recording_stop_timeout"
    raw_path = job_paths(session_id)["raw"]
    if not os.path.isfile(raw_path) or os.path.getsize(raw_path) <= 44:
        with state_lock:
            record_state = "idle"
            recognition_state = "error"
        return False, HTTPStatus.UNPROCESSABLE_ENTITY, "recording_empty"
    threading.Thread(target=recognize_recording, args=(session_id,), daemon=True).start()
    log(f"VOICE_RECORD_STOP job_id={session_id}")
    return True, HTTPStatus.OK, session_id


def status_snapshot():
    with state_lock:
        elapsed = time.time() - record_started_at if record_state in {"recording", "stopping"} else 0.0
        normalized_level = min(1.0, (level / 5000.0) ** 0.55) if level > 0 else 0.0
        return {
            "ok": True,
            "time_ms": now_ms(),
            "model": {
                "state": model_state,
                "ready": model_state == "ready",
                "error": model_error,
                "path": MODEL_PATH,
                "threads": int(os.environ.get("OMP_NUM_THREADS", DEFAULT_ASR_THREADS)),
                "vad_min_rms": VAD_MIN_RMS,
                "vad_peak_ratio": VAD_PEAK_RATIO,
            },
            "recording": {
                "state": record_state,
                "elapsed": round(elapsed, 3),
                "max_seconds": MAX_RECORD_SECONDS,
                "level": round(normalized_level, 3),
                "peak": peak,
                "error": record_error,
            },
            "recognition": {
                "state": recognition_state,
                "job_id": recognition_job_id,
                "text": last_text,
                "audio": last_audio,
                "error": recognition_error,
            },
        }


def response_payload(ok=True, data=None, error=""):
    payload = {"ok": ok, "time_ms": now_ms()}
    if data:
        payload.update(data)
    if error:
        payload["error"] = error
    return payload


class AsrHandler(BaseHTTPRequestHandler):
    server_version = "K1AsrBackend/1.1"

    def log_message(self, fmt, *args):
        log(f"HTTP {self.address_string()} {fmt % args}")

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def send_json(self, payload, status=HTTPStatus.OK):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(int(status))
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/asr/status":
            self.send_json(status_snapshot())
            return
        if self.path == "/healthz":
            self.send_json(response_payload(data={"service": "k1_asr_backend", "healthy": True}))
            return
        self.send_json(response_payload(False, error="not_found"), HTTPStatus.NOT_FOUND)

    def do_POST(self):
        try:
            if self.path == "/api/asr/preload":
                preload_async()
                self.send_json(response_payload(data={"model_state": model_state}))
                return
            if self.path == "/api/asr/record/start":
                ok, status, result = start_recording()
                if ok:
                    self.send_json(response_payload(data={"job_id": result, "recording_state": "recording"}))
                else:
                    self.send_json(response_payload(False, error=result), status)
                return
            if self.path == "/api/asr/record/stop":
                payload = self.read_json()
                ok, status, result = stop_recording(payload.get("job_id") or "")
                if ok:
                    self.send_json(response_payload(data={"job_id": result, "recognition_state": "running"}))
                else:
                    self.send_json(response_payload(False, error=result), status)
                return
            self.send_json(response_payload(False, error="not_found"), HTTPStatus.NOT_FOUND)
        except json.JSONDecodeError:
            self.send_json(response_payload(False, error="invalid_json"), HTTPStatus.BAD_REQUEST)
        except Exception as exc:
            log(f"VOICE_ERROR=http {type(exc).__name__}: {exc}")
            self.send_json(response_payload(False, error=f"{type(exc).__name__}: {exc}"), HTTPStatus.INTERNAL_SERVER_ERROR)


def parse_args():
    parser = argparse.ArgumentParser(description="K1 local ASR backend for UI integration")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--threads", type=int, default=DEFAULT_ASR_THREADS, help="ASR backend thread hint")
    parser.add_argument("--preload", action="store_true", help="load ASR model immediately on service start")
    return parser.parse_args()


def main():
    args = parse_args()
    configure_asr_runtime(args.threads)
    ensure_work_dir()
    init_microphone()
    if args.preload:
        preload_async()
    server = ThreadingHTTPServer((args.host, args.port), AsrHandler)
    log(f"K1_ASR_BACKEND_READY host={args.host} port={args.port} preload={args.preload}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        shutdown_event.set()
        server.server_close()


if __name__ == "__main__":
    main()
