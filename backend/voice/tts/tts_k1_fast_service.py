#!/usr/bin/env python3
"""Fast text-to-speech service for K1.

Endpoints:
  GET/POST /health
  GET/POST /warmup
  GET/POST /say
  GET/POST /say_stream
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

OFFICIAL_DIR = Path(os.environ.get("K1_TTS_OFFICIAL_DIR", "/usr/share/asr-llm-tts"))
CACHE_DIR = Path(os.environ.get("K1_TTS_CACHE_DIR", "/opt/smart_medicine_tts/fast_tts_cache"))
AUDIO_DEVICE = os.environ.get("K1_TTS_AUDIO_DEVICE", "plughw:CARD=Device,DEV=0")
PORT = int(os.environ.get("K1_TTS_PORT", "8788"))
WARMUP_TEXT = os.environ.get("K1_TTS_WARMUP_TEXT", "ok")
MIN_CHUNK_CHARS = int(os.environ.get("K1_TTS_MIN_CHUNK_CHARS", "12"))
MAX_CHUNK_CHARS = int(os.environ.get("K1_TTS_MAX_CHUNK_CHARS", "36"))
PLAYBACK_SPEED = float(os.environ.get("K1_TTS_PLAYBACK_SPEED", "1.0"))

DEFAULT_WARMUP_TEXTS = [
    "\u8bf7\u7a0d\u7b49\uff0c\u6211\u6b63\u5728\u67e5\u8be2\u836f\u7269\u4fe1\u606f\u3002",
    "\u60a8\u597d\uff0c\u7cfb\u7edf\u5df2\u8bfb\u53d6\u60a8\u667a\u80fd\u836f\u76d2\u7684\u670d\u836f\u8bb0\u5f55\uff0c\u6b63\u5728\u751f\u6210\u7528\u836f\u53c2\u8003\u65b9\u6848\u3002",
    "\u8be5\u836f\u7269\u4e3b\u8981\u7528\u4e8e\u7a33\u5b9a\u63a7\u5236\u8eab\u4f53\u6307\u6807\uff0c\u89c4\u5f8b\u6309\u65f6\u670d\u7528\u53ef\u4ee5\u4fdd\u969c\u6548\u679c\u3002",
    "\u5efa\u8bae\u4f7f\u7528\u6e29\u6c34\u9001\u670d\uff0c\u670d\u836f\u671f\u95f4\u5c3d\u91cf\u4e0d\u8981\u996e\u9152\u6216\u996e\u7528\u6d53\u8336\u3002",
    "\u60a8\u7684\u667a\u80fd\u836f\u76d2\u4f1a\u6309\u7167\u672c\u6b21\u65b9\u6848\u8bbe\u7f6e\u5b9a\u65f6\u63d0\u9192\uff0c\u907f\u514d\u6f0f\u670d\u6216\u91cd\u590d\u670d\u7528\u3002",
    "\u5982\u679c\u51fa\u73b0\u80c3\u75db\u3001\u9ed1\u4fbf\u6216\u8005\u8fc7\u654f\uff0c\u8bf7\u53ca\u65f6\u505c\u7528\u5e76\u54a8\u8be2\u533b\u751f\u3002",
    "\u672c\u65b9\u6848\u4ec5\u4f5c\u4e3a\u65e5\u5e38\u670d\u836f\u53c2\u8003\uff0c\u4e0d\u80fd\u66ff\u4ee3\u6267\u4e1a\u533b\u5e08\u8bca\u65ad\u3002",
    "\u5e03\u6d1b\u82ac\u5efa\u8bae\u996d\u540e\u670d\u7528\uff0c\u53ef\u4ee5\u51cf\u5c11\u80c3\u90e8\u523a\u6fc0\u3002",
    "\u963f\u83ab\u897f\u6797\u8fc7\u654f\u8005\u5e94\u907f\u514d\u4f7f\u7528\uff0c\u670d\u7528\u524d\u8bf7\u6838\u5bf9\u8fc7\u654f\u53f2\u3002",
    "\u4e09\u4e5d\u611f\u5192\u7075\u901a\u5e38\u7528\u4e8e\u7f13\u89e3\u611f\u5192\u5f15\u8d77\u7684\u5934\u75db\u3001\u53d1\u70ed\u3001\u9f3b\u585e\u3001\u6d41\u6d95\u548c\u54bd\u75db\u3002",
    "\u5e38\u89c1\u9897\u7c92\u5242\u6210\u4eba\u4e00\u822c\u4e00\u6b21\u4e00\u888b\uff0c\u4e00\u65e5\u4e09\u6b21\uff0c\u5efa\u8bae\u996d\u540e\u7528\u6e29\u6c34\u51b2\u670d\u3002",
    "\u5177\u4f53\u5242\u91cf\u8bf7\u4ee5\u836f\u76d2\u5185\u8bf4\u660e\u4e66\u4e3a\u51c6\u3002",
    "\u670d\u836f\u671f\u95f4\u5c3d\u91cf\u4e0d\u8981\u996e\u9152\uff0c\u4e5f\u4e0d\u8981\u540c\u65f6\u670d\u7528\u6210\u5206\u7c7b\u4f3c\u7684\u611f\u5192\u836f\u3002",
    "\u5982\u679c\u75c7\u72b6\u8d85\u8fc7\u4e09\u5929\u6ca1\u6709\u7f13\u89e3\uff0c\u8bf7\u53ca\u65f6\u5c31\u533b\u3002",
    "\u5982\u679c\u51fa\u73b0\u9ad8\u70ed\u3001\u76ae\u75b9\u6216\u660e\u663e\u55dc\u7761\uff0c\u8bf7\u7acb\u5373\u505c\u836f\u5e76\u5c31\u533b\u3002",
    "\u5c0f\u67f4\u80e1\u9897\u7c92\u901a\u5e38\u7528\u4e8e\u7f13\u89e3\u5bd2\u70ed\u5f80\u6765\u3001\u80f8\u80c1\u82e6\u6ee1\u3001\u98df\u6b32\u4e0d\u4f73\u3001\u53e3\u82e6\u54bd\u5e72\u7b49\u4e0d\u9002\u3002",
    "\u670d\u836f\u671f\u95f4\u5c3d\u91cf\u6e05\u6de1\u996e\u98df\uff0c\u907f\u514d\u996e\u9152\u548c\u8f9b\u8fa3\u523a\u6fc0\u6027\u98df\u7269\u3002",
    "\u5982\u679c\u53d1\u70ed\u6301\u7eed\u4e0d\u9000\uff0c\u6216\u8005\u75c7\u72b6\u8d85\u8fc7\u4e09\u5929\u6ca1\u6709\u7f13\u89e3\uff0c\u8bf7\u53ca\u65f6\u5c31\u533b\u3002",
    "\u6bcf\u65e5\u65e9\u4e0a\u516b\u70b9\u548c\u665a\u4e0a\u516b\u70b9\u5404\u670d\u7528\u4e00\u6b21\u3002",
    "\u5355\u6b21\u4e00\u7247\uff0c\u8bf7\u6309\u7167\u836f\u76d2\u63d0\u9192\u51c6\u65f6\u53d6\u836f\u3002",
    "\u82e5\u5fd8\u8bb0\u670d\u836f\uff0c\u4e0b\u6b21\u6b63\u5e38\u670d\u7528\u5373\u53ef\uff0c\u4e0d\u5efa\u8bae\u53cc\u500d\u8865\u670d\u3002",
]
PUNCTUATION = "\uff0c\u3002\uff01\uff1f\uff1b,.!?;"
STRONG_PUNCTUATION = "\u3002\uff01\uff1f.!?"

sys.path.insert(0, str(OFFICIAL_DIR))
os.chdir(str(OFFICIAL_DIR))

from tts import TTSModel  # noqa: E402

CACHE_DIR.mkdir(parents=True, exist_ok=True)
MODEL_LOCK = threading.RLock()
MODEL: TTSModel | None = None
PCM_CACHE_LOCK = threading.RLock()
PCM_CACHE: dict[str, dict[str, object]] = {}


def now_ms() -> int:
    return int(time.time() * 1000)


def clean_text(text: str) -> str:
    return " ".join(text.replace("\r", " ").replace("\n", " ").split()).strip()


def split_text(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    pieces: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if len(buffer) >= MIN_CHUNK_CHARS and char in PUNCTUATION:
            pieces.append(buffer.strip())
            buffer = ""
            continue
        if len(buffer) >= MAX_CHUNK_CHARS:
            cut = max(buffer.rfind(mark) for mark in PUNCTUATION)
            if cut >= MIN_CHUNK_CHARS:
                pieces.append(buffer[: cut + 1].strip())
                buffer = buffer[cut + 1 :]
            else:
                pieces.append(buffer.strip())
                buffer = ""
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces


def split_sentences(text: str) -> list[str]:
    text = clean_text(text)
    if not text:
        return []
    pieces: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in STRONG_PUNCTUATION:
            pieces.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        pieces.append(buffer.strip())
    return pieces


def cache_path(text: str) -> Path:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:20]
    return CACHE_DIR / f"tts_{digest}.wav"


def get_model() -> TTSModel:
    global MODEL
    with MODEL_LOCK:
        if MODEL is None:
            MODEL = TTSModel()
        return MODEL


def copy_atomic(source: Path, dest: Path) -> None:
    tmp = dest.with_suffix(".tmp.wav")
    shutil.copy2(source, tmp)
    tmp.replace(dest)


def read_wav_pcm(path: Path) -> dict[str, object]:
    with wave.open(str(path), "rb") as wav_file:
        channels = wav_file.getnchannels()
        sample_width = wav_file.getsampwidth()
        sample_rate = wav_file.getframerate()
        frame_count = wav_file.getnframes()
        frames = wav_file.readframes(frame_count)
        duration_ms = int(frame_count * 1000 / sample_rate) if sample_rate else 0
    if sample_width != 2:
        raise ValueError(f"unsupported sample width: {sample_width}")
    return {
        "pcm": frames,
        "channels": channels,
        "sample_rate": sample_rate,
        "sample_width": sample_width,
        "duration_ms": duration_ms,
    }


def synthesize(text: str) -> tuple[Path, bool, int]:
    target = cache_path(text)
    if target.exists() and target.stat().st_size > 44:
        return target, True, 0

    start = now_ms()
    with MODEL_LOCK:
        model = get_model()
        output = Path(model.ort_predict(text))
    elapsed = now_ms() - start
    copy_atomic(output, target)
    return target, False, elapsed


def get_pcm(text: str) -> tuple[dict[str, object], bool, int, Path]:
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("text is empty")
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:20]
    with PCM_CACHE_LOCK:
        cached_pcm = PCM_CACHE.get(digest)
        if cached_pcm is not None:
            return cached_pcm, True, 0, cache_path(cleaned)

    wav, cached_wav, synth_ms = synthesize(cleaned)
    pcm_info = read_wav_pcm(wav)
    pcm_info["text"] = cleaned
    pcm_info["wav"] = str(wav)
    with PCM_CACHE_LOCK:
        PCM_CACHE[digest] = pcm_info
    return pcm_info, cached_wav, synth_ms, wav


def play_pcm(pcm_info: dict[str, object]) -> int:
    start = now_ms()
    pcm_data = pcm_info["pcm"]
    sample_rate = int(pcm_info["sample_rate"])
    channels = int(pcm_info["channels"])
    if abs(PLAYBACK_SPEED - 1.0) > 0.001 and shutil.which("ffmpeg"):
        ffmpeg_proc = subprocess.Popen(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "-i",
                "pipe:0",
                "-filter:a",
                f"atempo={PLAYBACK_SPEED}",
                "-f",
                "s16le",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                "pipe:1",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        pcm_data, ffmpeg_stderr = ffmpeg_proc.communicate(pcm_data, timeout=120)
        if ffmpeg_proc.returncode != 0:
            raise RuntimeError(ffmpeg_stderr.decode(errors="replace"))
    proc = subprocess.Popen(
        [
            "aplay",
            "-q",
            "-D",
            AUDIO_DEVICE,
            "-t",
            "raw",
            "-f",
            "S16_LE",
            "-r",
            str(sample_rate),
            "-c",
            str(channels),
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    _, stderr = proc.communicate(pcm_data, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(stderr.decode(errors="replace") if isinstance(stderr, bytes) else str(stderr))
    return now_ms() - start


def concat_pcm_infos(pcm_infos: list[dict[str, object]]) -> dict[str, object]:
    if not pcm_infos:
        raise ValueError("no pcm data")
    first = pcm_infos[0]
    sample_rate = first["sample_rate"]
    channels = first["channels"]
    sample_width = first["sample_width"]
    pcm = bytearray()
    duration_ms = 0
    for item in pcm_infos:
        if item["sample_rate"] != sample_rate or item["channels"] != channels or item["sample_width"] != sample_width:
            raise ValueError("pcm format mismatch")
        pcm.extend(item["pcm"])
        duration_ms += int(item.get("duration_ms", 0))
    return {
        "pcm": bytes(pcm),
        "sample_rate": sample_rate,
        "channels": channels,
        "sample_width": sample_width,
        "duration_ms": duration_ms,
    }


def say_text(text: str, should_play: bool = True) -> dict[str, object]:
    cleaned = clean_text(text)
    if not cleaned:
        raise ValueError("text is empty")

    total_start = now_ms()
    pcm_info, cached, synth_ms, wav = get_pcm(cleaned)
    play_ms = play_pcm(pcm_info) if should_play else 0
    duration_ms = int(pcm_info.get("duration_ms", 0))
    return {
        "ok": True,
        "text": cleaned,
        "cached": cached,
        "synth_ms": synth_ms,
        "play_ms": play_ms,
        "total_ms": now_ms() - total_start,
        "duration_ms": duration_ms,
        "rtf": round(synth_ms / duration_ms, 3) if duration_ms else None,
        "audio": str(wav),
        "device": AUDIO_DEVICE,
    }


def say_stream(text: str, should_play: bool = True) -> dict[str, object]:
    cleaned = clean_text(text)
    chunks = split_sentences(cleaned) or split_text(cleaned)
    if not chunks:
        raise ValueError("text is empty")

    total_start = now_ms()
    synth_queue: queue.Queue[tuple[int, str] | None] = queue.Queue()
    play_queue: queue.Queue[tuple[int, str, dict[str, object], bool, int, Path] | None] = queue.Queue(maxsize=2)
    results: list[dict[str, object]] = []
    errors: list[str] = []

    def synth_worker() -> None:
        while True:
            item = synth_queue.get()
            if item is None:
                play_queue.put(None)
                synth_queue.task_done()
                return
            index, chunk_text = item
            try:
                pcm_info, cached, synth_ms, wav = get_pcm(chunk_text)
                play_queue.put((index, chunk_text, pcm_info, cached, synth_ms, wav))
            except Exception as exc:  # noqa: BLE001
                errors.append(f"chunk {index}: {exc}")
            finally:
                synth_queue.task_done()

    def play_worker() -> None:
        while True:
            item = play_queue.get()
            if item is None:
                play_queue.task_done()
                return
            index, chunk_text, pcm_info, cached, synth_ms, wav = item
            play_ms = 0
            try:
                if should_play:
                    play_ms = play_pcm(pcm_info)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"play {index}: {exc}")
            results.append(
                {
                    "index": index,
                    "text": chunk_text,
                    "cached": cached,
                    "synth_ms": synth_ms,
                    "play_ms": play_ms,
                    "duration_ms": pcm_info.get("duration_ms", 0),
                    "audio": str(wav),
                }
            )
            play_queue.task_done()

    synth_thread = threading.Thread(target=synth_worker, daemon=True)
    play_thread = threading.Thread(target=play_worker, daemon=True)
    synth_thread.start()
    play_thread.start()
    for index, chunk_text in enumerate(chunks, start=1):
        synth_queue.put((index, chunk_text))
    synth_queue.put(None)
    synth_queue.join()
    play_queue.join()

    results.sort(key=lambda item: int(item["index"]))
    total_synth_ms = sum(int(item["synth_ms"]) for item in results)
    total_play_ms = sum(int(item["play_ms"]) for item in results)
    total_duration_ms = sum(int(item["duration_ms"]) for item in results)
    return {
        "ok": not errors,
        "text": cleaned,
        "chunks": results,
        "errors": errors,
        "chunk_count": len(results),
        "total_synth_ms": total_synth_ms,
        "total_play_ms": total_play_ms,
        "total_duration_ms": total_duration_ms,
        "total_ms": now_ms() - total_start,
        "rtf": round(total_synth_ms / total_duration_ms, 3) if total_duration_ms else None,
        "device": AUDIO_DEVICE,
    }


def say_concat(text: str, should_play: bool = True, texts: list[str] | None = None) -> dict[str, object]:
    chunks = [clean_text(item) for item in (texts if texts is not None else split_sentences(text))]
    chunks = [item for item in chunks if item]
    if not chunks:
        raise ValueError("text is empty")
    total_start = now_ms()
    pcm_infos: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    for index, chunk_text in enumerate(chunks, start=1):
        pcm_info, cached, synth_ms, wav = get_pcm(chunk_text)
        pcm_infos.append(pcm_info)
        results.append(
            {
                "index": index,
                "text": chunk_text,
                "cached": cached,
                "synth_ms": synth_ms,
                "duration_ms": pcm_info.get("duration_ms", 0),
                "audio": str(wav),
            }
        )
    combined = concat_pcm_infos(pcm_infos)
    play_ms = play_pcm(combined) if should_play else 0
    total_synth_ms = sum(int(item["synth_ms"]) for item in results)
    total_duration_ms = int(combined.get("duration_ms", 0))
    return {
        "ok": True,
        "text": clean_text(text) if texts is None else "".join(chunks),
        "chunks": results,
        "chunk_count": len(results),
        "total_synth_ms": total_synth_ms,
        "total_play_ms": play_ms,
        "total_duration_ms": total_duration_ms,
        "total_ms": now_ms() - total_start,
        "rtf": round(total_synth_ms / total_duration_ms, 3) if total_duration_ms else None,
        "device": AUDIO_DEVICE,
    }


def warmup_texts(texts: list[str] | None = None) -> dict[str, object]:
    start = now_ms()
    selected = [clean_text(text) for text in (texts or DEFAULT_WARMUP_TEXTS)]
    selected = [text for text in selected if text]
    get_model()
    items = []
    for text in selected:
        pcm_info, cached, synth_ms, wav = get_pcm(text)
        items.append(
            {
                "text": text,
                "cached": cached,
                "synth_ms": synth_ms,
                "duration_ms": pcm_info.get("duration_ms", 0),
                "audio": str(wav),
            }
        )
    return {
        "ok": True,
        "model_loaded": MODEL is not None,
        "count": len(items),
        "items": items,
        "total_ms": now_ms() - start,
        "cache_dir": str(CACHE_DIR),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "K1FastTTS/1.1"

    def _send_json(self, code: int, payload: dict[str, object]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if parsed.path == "/health":
            self._send_json(
                200,
                {
                    "ok": True,
                    "model_loaded": MODEL is not None,
                    "cache_dir": str(CACHE_DIR),
                    "pcm_cache_count": len(PCM_CACHE),
                },
            )
            return
        if parsed.path == "/warmup":
            self._handle_warmup(query.get("text") or None)
            return
        if parsed.path == "/say":
            self._handle_say(query.get("text", [""])[0], self._query_play(query))
            return
        if parsed.path == "/say_stream":
            self._handle_say_stream(query.get("text", [""])[0], self._query_play(query))
            return
        if parsed.path == "/say_concat":
            self._handle_say_concat(query.get("text", [""])[0], self._query_play(query), None)
            return
        self._send_json(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        parsed_path = urlparse(self.path).path
        if parsed_path not in {"/say", "/say_stream", "/say_concat", "/warmup"}:
            self._send_json(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            payload = json.loads(raw) if raw else {}
            if parsed_path == "/warmup":
                texts = payload.get("texts")
                if isinstance(texts, str):
                    texts = [texts]
                elif not isinstance(texts, list):
                    texts = None
                self._handle_warmup(texts)
                return
            text = str(payload.get("text", ""))
            texts = payload.get("texts")
            if isinstance(texts, str):
                texts = [texts]
            elif not isinstance(texts, list):
                texts = None
            should_play = bool(payload.get("play", True))
        except Exception as exc:  # noqa: BLE001
            self._send_json(400, {"ok": False, "error": str(exc)})
            return
        if parsed_path == "/say_stream":
            self._handle_say_stream(text, should_play)
            return
        if parsed_path == "/say_concat":
            self._handle_say_concat(text, should_play, texts)
            return
        self._handle_say(text, should_play)

    def _query_play(self, query: dict[str, list[str]]) -> bool:
        return query.get("play", ["1"])[0] not in {"0", "false", "False"}

    def _handle_say(self, text: str, should_play: bool) -> None:
        try:
            self._send_json(200, say_text(text, should_play))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_say_stream(self, text: str, should_play: bool) -> None:
        try:
            self._send_json(200, say_stream(text, should_play))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_say_concat(self, text: str, should_play: bool, texts: list[str] | None) -> None:
        try:
            self._send_json(200, say_concat(text, should_play, texts))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})

    def _handle_warmup(self, texts: list[str] | None) -> None:
        try:
            self._send_json(200, warmup_texts(texts))
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"ok": False, "error": str(exc)})

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def serve(host: str, port: int, warmup: bool) -> None:
    if warmup:
        try:
            warmup_texts([WARMUP_TEXT])
        except Exception as exc:  # noqa: BLE001
            print(f"warmup failed: {exc}", file=sys.stderr, flush=True)
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"K1 fast TTS listening on http://{host}:{port}", flush=True)
    httpd.serve_forever()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("text", nargs="*", help="Text to synthesize and play once")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--concat", action="store_true")
    parser.add_argument("--no-play", action="store_true")
    parser.add_argument("--no-warmup", action="store_true")
    args = parser.parse_args()

    if args.serve:
        serve(args.host, args.port, warmup=not args.no_warmup)
        return 0

    text = " ".join(args.text).strip()
    if not text:
        parser.error("text is required unless --serve is used")
    if args.concat:
        result = say_concat(text, should_play=not args.no_play)
    elif args.stream:
        result = say_stream(text, should_play=not args.no_play)
    else:
        result = say_text(text, should_play=not args.no_play)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
