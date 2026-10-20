"""
智能用药问答引擎。

用于前端/后端集成：
1. 应用启动时创建 MedicineQAEngine，并调用 load() 预加载问题银行、药品卡片和 embedding 索引。
2. 用户点击发送后，调用 ask(question, session_id) 获取回答。
3. engine 内部会维护每个 session 最近一次药品，支持“这个药怎么吃”这类追问。
"""
from chat_demo import (
    DEFAULT_LLM_BACKEND,
    DEFAULT_LLM_URL,
    DEFAULT_MODEL,
    DEFAULT_PROMPT_VERSION,
    DEFAULT_EMBED_BACKEND,
    answer_once,
    build_index,
    call_llm,
    default_llm_url,
    load_embedding_model,
    load_data,
)
import time


SESSION_TTL_SECONDS = 30 * 60


class MedicineQAEngine:
    def __init__(
        self,
        model=DEFAULT_MODEL,
        use_llm=True,
        llm_backend=DEFAULT_LLM_BACKEND,
        llm_url=DEFAULT_LLM_URL,
        embed_backend=DEFAULT_EMBED_BACKEND,
    ):
        self.model = model
        self.use_llm = use_llm
        self.llm_backend = llm_backend
        self.llm_url = llm_url or default_llm_url(llm_backend)
        self.embed_backend = embed_backend
        self.bank = None
        self.cards = None
        self.emb = None
        self.variant_unit_ids = None
        self.variant_vecs = None
        self.current_drug_by_session = {}
        self.session_last_seen = {}
        self.retrieval_ready = False
        self.llm_ready = not use_llm
        self.warmup_error = ""

    def load(self, warmup_llm=True):
        self.bank, self.cards = load_data()
        self.emb = load_embedding_model(self.embed_backend)
        _, self.variant_unit_ids, self.variant_vecs, self.index_source = build_index(
            self.bank,
            self.emb,
            cards=self.cards,
        )
        self.retrieval_ready = True
        if warmup_llm and self.use_llm:
            if not self.warmup_llm():
                raise RuntimeError(f"LLM warmup failed: {self.warmup_error or 'unknown'}")
        return self

    def warmup_llm(self):
        try:
            call_llm(
                "你是连通性检查助手。",
                "请只回答：好",
                self.model,
                backend=self.llm_backend,
                url=self.llm_url,
            )
            self.llm_ready = True
            self.warmup_error = ""
            return True
        except Exception as exc:
            self.llm_ready = False
            self.warmup_error = f"{type(exc).__name__}: {exc}"
            return False

    @property
    def ready(self):
        retrieval = self.retrieval_ready and all(
            item is not None
            for item in (self.bank, self.cards, self.emb, self.variant_unit_ids, self.variant_vecs)
        )
        return retrieval and (self.llm_ready or not self.use_llm)

    def health(self):
        return {
            "ready": self.ready,
            "retrievalReady": bool(self.retrieval_ready),
            "llmReady": bool(self.llm_ready),
            "warmupError": self.warmup_error,
            "sessionCount": len(self.current_drug_by_session),
        }

    def _reap_sessions(self):
        now = time.monotonic()
        expired = [session_id for session_id, last_seen in self.session_last_seen.items() if now - last_seen >= SESSION_TTL_SECONDS]
        for session_id in expired:
            self.session_last_seen.pop(session_id, None)
            self.current_drug_by_session.pop(session_id, None)

    def ask(self, question, session_id="default"):
        if not self.ready:
            raise RuntimeError("MedicineQAEngine 尚未 load()，请先预加载。")

        self._reap_sessions()
        self.session_last_seen[session_id] = time.monotonic()
        current_drug = self.current_drug_by_session.get(session_id)
        result = answer_once(
            question=question,
            cards=self.cards,
            current_drug=current_drug,
            bank=self.bank,
            emb=self.emb,
            variant_unit_ids=self.variant_unit_ids,
            variant_vecs=self.variant_vecs,
            use_llm=self.use_llm,
            model=self.model,
            llm_backend=self.llm_backend,
            llm_url=self.llm_url,
        )

        # answer_once() uses an explicit None to invalidate stale context
        # after an unknown or unlisted medicine reference.  Retaining the
        # prior card here would reintroduce cross-drug answers in frontends.
        if "new_current_drug" in result:
            new_current_drug = result["new_current_drug"]
            if new_current_drug is None:
                self.current_drug_by_session.pop(session_id, None)
            else:
                self.current_drug_by_session[session_id] = new_current_drug

        card = result.get("card")
        top3 = result.get("top3") or []
        selected_unit_ids = result.get("selected_unit_ids") or (
            [result.get("top_unit_id")] if result.get("top_unit_id") else []
        )
        answer_parts = result.get("answer_parts") or [result["answer"]]
        return {
            "answer": result["answer"],
            # Frontends can render each item as its own dialogue bubble when a
            # multi-fact answer was preplanned for the 88-token model ceiling.
            "answer_parts": answer_parts,
            "drug_name": card["drug_name"] if card else None,
            "matched_name": result.get("matched_name"),
            "intent_id": result.get("top_unit_id"),
            "intent_ids": selected_unit_ids,
            "score": round(result.get("top_score", 0), 4) if card else None,
            "risk_level": result.get("risk_level"),
            "source_answer": result.get("standard_answer"),
            "source_answers": result.get("standard_answers") or [],
            "selected_facts": result.get("selected_facts") or [],
            "fallback_reason": result.get("fallback_reason"),
            "llm_raw": result.get("llm_raw"),
            "token_usage": result.get("token_usage") or {
                "prompt_tokens": 0,
                "output_tokens": 0,
                "done_reason": "not_called",
            },
            "timing": {
                "embedding_ms": round(result.get("embed_ms", 0), 1),
                "llm_ms": round(result.get("llm_ms", 0), 1),
            },
            "llm_backend": self.llm_backend,
            "llm_url": self.llm_url,
            "prompt_version": result.get("token_usage", {}).get(
                "prompt_version", DEFAULT_PROMPT_VERSION
            ),
            "top3": [
                {"intent_id": unit_id, "score": round(score, 4)}
                for unit_id, score in top3
            ],
        }

    def reset_session(self, session_id="default"):
        self.current_drug_by_session.pop(session_id, None)
        self.session_last_seen.pop(session_id, None)
