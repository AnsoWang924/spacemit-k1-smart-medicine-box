"""K1 智能体问药 API（供 8799 网关挂载或独立烟测）。

正式契约：POST /api/v1/medicine-qa/messages
模型路径：SpaceMIT 官方优化 llama-server -> Qwen3-0.6B Q4_0 非思考模式。
"""
import argparse
import json
import threading
import time
import uuid
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from medicine_qa_engine import MedicineQAEngine


ENGINE = None
ENGINE_LOCK = threading.Lock()  # 选定的 llama-server profile 并发为 1
PRELOAD_LOCK = threading.Lock()
PRELOADING = False
PRELOAD_ERROR = None
WARMUP_LOCK = threading.Lock()
WARMUP_IN_FLIGHT = False
WARMUP_AT = None
WARMUP_DURATION_MS = None
WARMUP_ERROR = None
TZ_UTC8 = timezone(timedelta(hours=8))


def request_id():
    return "req_" + uuid.uuid4().hex[:16]


def now_iso():
    return datetime.now(TZ_UTC8).isoformat(timespec="seconds")


def envelope(data=None, error=None, rid=None):
    return {"requestId": rid or request_id(), "data": data, "error": error}


def api_error(code, message, retryable=False):
    return {"code": code, "message": message, "retryable": bool(retryable)}


def preload(block=False):
    global PRELOADING, PRELOAD_ERROR, WARMUP_AT, WARMUP_DURATION_MS, WARMUP_ERROR

    def run():
        global PRELOADING, PRELOAD_ERROR, WARMUP_AT, WARMUP_DURATION_MS, WARMUP_ERROR
        with PRELOAD_LOCK:
            if ENGINE.ready:
                PRELOADING, PRELOAD_ERROR = False, None
                return
            PRELOADING, PRELOAD_ERROR = True, None
            started = time.perf_counter()
            try:
                ENGINE.load(warmup_llm=True)
                WARMUP_AT = now_iso()
                WARMUP_ERROR = None
            except Exception as exc:  # 不把底层异常泄露给浏览器
                PRELOAD_ERROR = str(exc)
                WARMUP_ERROR = PRELOAD_ERROR
            finally:
                WARMUP_DURATION_MS = round((time.perf_counter() - started) * 1000, 1)
                PRELOADING = False

    if block:
        run()
    elif not PRELOADING and not ENGINE.ready:
        threading.Thread(target=run, daemon=True).start()


def warmup_status():
    if ENGINE is None or not ENGINE.retrieval_ready:
        state = "loading" if PRELOADING else "unavailable"
    elif ENGINE.llm_ready:
        state = "already_warm"
    elif WARMUP_IN_FLIGHT:
        state = "warming"
    else:
        state = "unavailable"
    return {
        "status": state,
        "warmAt": WARMUP_AT,
        "durationMs": WARMUP_DURATION_MS,
        "error": WARMUP_ERROR,
    }


def trigger_warmup():
    """Warm only the LLM transport; never enter ask() or a user session."""
    global WARMUP_IN_FLIGHT, WARMUP_AT, WARMUP_DURATION_MS, WARMUP_ERROR
    if ENGINE is None or not ENGINE.retrieval_ready:
        preload(block=False)
        return warmup_status()
    if ENGINE.llm_ready:
        return warmup_status()

    with WARMUP_LOCK:
        if ENGINE.llm_ready or WARMUP_IN_FLIGHT:
            return warmup_status()
        WARMUP_IN_FLIGHT = True
        WARMUP_ERROR = None

    def run():
        global WARMUP_IN_FLIGHT, WARMUP_AT, WARMUP_DURATION_MS, WARMUP_ERROR
        started = time.perf_counter()
        acquired = False
        try:
            # A real user request always wins over a background warmup.
            acquired = ENGINE_LOCK.acquire(timeout=0.05)
            if not acquired:
                WARMUP_ERROR = "model_busy"
                return
            if not ENGINE.warmup_llm():
                WARMUP_ERROR = ENGINE.warmup_error or "warmup_failed"
                return
            WARMUP_AT = now_iso()
        finally:
            if acquired:
                ENGINE_LOCK.release()
            WARMUP_DURATION_MS = round((time.perf_counter() - started) * 1000, 1)
            WARMUP_IN_FLIGHT = False

    threading.Thread(target=run, daemon=True).start()
    return warmup_status()


def write_json(handler, status, payload):
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
    handler.send_header("Access-Control-Allow-Headers", "Content-Type, X-Request-Id")
    handler.send_header("Content-Length", str(len(encoded)))
    handler.end_headers()
    handler.wfile.write(encoded)


class Handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        write_json(self, 200, envelope({"status": "ok"}))

    def do_GET(self):
        if self.path.rstrip("/") != "/api/v1/health":
            write_json(self, 404, envelope(error=api_error("not_found", "接口不存在")))
            return
        state = "ready" if ENGINE.ready else ("loading" if PRELOADING else "unavailable")
        health = ENGINE.health()
        write_json(self, 200 if ENGINE.ready else 503, envelope({
            "status": state,
            **health,
            "preloadError": PRELOAD_ERROR,
            "module": "medicine_qa",
            "model": "K1-official-spacemit-llama-server/qwen3-0.6B-Q4_0/non-thinking",
            "prewarm": warmup_status(),
            "generatedAt": now_iso(),
        }))

    def do_POST(self):
        path = self.path.rstrip("/")
        if path == "/api/v1/medicine-qa/prewarm":
            write_json(self, 202, envelope(trigger_warmup()))
            return
        if path != "/api/v1/medicine-qa/messages":
            write_json(self, 404, envelope(error=api_error("not_found", "接口不存在")))
            return
        rid = self.headers.get("X-Request-Id") or request_id()
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 16 * 1024:
                raise ValueError
            raw = json.loads(self.rfile.read(length).decode("utf-8"))
            payload = raw.get("data", raw) if isinstance(raw, dict) else {}
            question = str(payload.get("question") or "").strip()
            session_id = str(payload.get("sessionId") or payload.get("session_id") or "default").strip()
            if not question or len(question) > 500:
                write_json(self, 400, envelope(error=api_error("invalid_question", "question 必须是 1 到 500 个字符", False), rid=rid))
                return
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            write_json(self, 400, envelope(error=api_error("invalid_request", "请求 JSON 或字段格式不正确", False), rid=rid))
            return

        if not ENGINE.ready:
            preload(block=False)
            write_json(self, 503, envelope(error=api_error("model_unavailable", "问药模型正在加载，请稍后重试", True), rid=rid))
            return
        if not ENGINE_LOCK.acquire(timeout=0.2):
            write_json(self, 429, envelope(error=api_error("model_busy", "问药模型忙，请稍后重试", True), rid=rid))
            return
        started = time.perf_counter()
        try:
            result = ENGINE.ask(question, session_id=session_id)
        except TimeoutError:
            write_json(self, 504, envelope(error={**api_error("model_timeout", "问药模型响应超时", True), "stage": "llm"}, rid=rid))
            return
        except Exception as exc:
            write_json(self, 502, envelope(error={**api_error("model_failed", "问药推理失败", True), "stage": "qa", "causeCode": type(exc).__name__}, rid=rid))
            return
        finally:
            ENGINE_LOCK.release()

        # LLM 服务不可达不能伪装成“已由模型生成”。由网关按 504 统一提示重试。
        if str(result.get("fallback_reason", "")).startswith("LLM失败"):
            write_json(self, 504, envelope(error=api_error("model_timeout", "问药模型响应超时或不可用", True), rid=rid))
            return
        answer = result["answer"]
        answer_parts = [
            str(part).strip()
            for part in (result.get("answer_parts") or [])
            if str(part).strip()
        ]
        # The old UI reads data.answer only.  Keep one newline-separated text
        # response while exposing the verified semantic parts as an optional
        # field for future QA-only diagnostics.
        if not answer_parts or "\n".join(answer_parts) != answer:
            answer_parts = [answer]
        write_json(self, 200, envelope({
            "answer": answer,
            "answerParts": answer_parts,
            "tokenUsage": result.get("token_usage") or {
                "prompt_tokens": 0,
                "output_tokens": 0,
                "done_reason": "not_called",
            },
            "timing": result.get("timing") or {},
            "promptVersion": result.get("prompt_version"),
            "sessionId": session_id,
            "model": "K1-official-spacemit-llama-server/qwen3-0.6B-Q4_0/non-thinking",
            "generatedAt": now_iso(),
            "safetyNotice": "具体用药请遵医嘱。",
            "durationMs": round((time.perf_counter() - started) * 1000, 1),
        }, rid=rid))

    def log_message(self, _format, *_args):
        return


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8790, help="内部问药端口；浏览器只能经 8799 网关访问。")
    parser.add_argument("--model", default="qwen3-0.6b-q40")
    parser.add_argument("--llm-url", default="http://127.0.0.1:8080/v1/chat/completions")
    parser.add_argument("--startup-preload", action="store_true")
    args = parser.parse_args()

    global ENGINE
    ENGINE = MedicineQAEngine(
        model=args.model,
        use_llm=True,
        llm_backend="llama_cpp_chat",
        llm_url=args.llm_url,
        embed_backend="auto",
    )
    if args.startup_preload:
        preload(block=True)
        if not ENGINE.ready:
            raise SystemExit("问药引擎预加载失败")
    print(f"medicine_qa_api listening on http://{args.host}:{args.port}", flush=True)
    ThreadingHTTPServer((args.host, args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
