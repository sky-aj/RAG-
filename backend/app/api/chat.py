"""\u5bf9\u8bdd\u4e0e\u68c0\u7d22\u63a5\u53e3\uff08\u6b65\u9aa4 5\uff09\u3002

  POST /api/chat      \u6df7\u5408\u68c0\u7d22 + \u751f\u6210\uff0cSSE \u6d41\u5f0f\u8fd4\u56de
  POST /api/retrieve  \u53ea\u68c0\u7d22\u4e0d\u751f\u6210\uff0c\u7528\u6765\u770b\u53ec\u56de\u4e0e\u4e0a\u4e0b\u6587\u62fc\u5f97\u5bf9\u4e0d\u5bf9
  GET  /api/chat/config \u5f53\u524d\u68c0\u7d22\u4e0e\u751f\u6210\u53c2\u6570

SSE \u4e8b\u4ef6\u7c7b\u578b\uff08\u6bcf\u884c\u4e00\u4e2a JSON\uff0c`data: ` \u5f00\u5934\uff09\uff1a
  sources    \u5f15\u7528\u6e05\u5355 + \u8bca\u65ad\u4fe1\u606f\uff08\u5148\u53d1\uff0c\u524d\u7aef\u53ef\u4ee5\u7acb\u5373\u6e32\u67d3\u6765\u6e90\uff09
  reasoning  \u63a8\u7406\u8fc7\u7a0b\uff08glm-4.7-flash \u7279\u6709\uff09
  content    \u6b63\u6587\u589e\u91cf
  done       \u7ed3\u675f\uff0c\u5e26 usage
  error      \u51fa\u9519

\u4e3a\u4ec0\u4e48\u5148\u53d1 sources \u518d\u53d1 content\uff1a\u68c0\u7d22\u5728\u751f\u6210\u4e4b\u524d\u5c31\u7ed3\u675f\u4e86\uff0c
\u5148\u628a\u5f15\u7528\u4e22\u8fc7\u53bb\uff0c\u7528\u6237\u5728\u7b49\u6a21\u578b\u5410\u5b57\u65f6\u5c31\u80fd\u770b\u5230\u201c\u627e\u5230\u4e86\u54ea\u4e9b\u6750\u6599\u201d\u3002
"""
from __future__ import annotations

import json
import logging

from flask import Blueprint, Response, jsonify, request, stream_with_context

from .. import rag, tracing
from .. import knowledge
from ..config import settings
from ..llm import client as llm
from ..retrieval import bm25, context as context_builder, hybrid, rerank

bp = Blueprint("chat", __name__)
logger = logging.getLogger(__name__)


def _payload() -> dict:
    body = request.get_json(silent=True) or {}
    merged = dict(body)
    for key in ("question", "top_k", "hybrid", "rerank", "budget", "threshold", "rewrite", "kb_id", "use_knowledge_base"):
        value = request.args.get(key)
        if value is not None:
            merged[key] = value
    return merged


def _truthy(value, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _int_or_none(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value):
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _doc_ids(payload: dict) -> list[str]:
    raw = payload.get("doc_id")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item]
    return [str(raw)]


def _sse(event: dict) -> str:
    return "data: " + json.dumps(event, ensure_ascii=False) + "\n\n"


@bp.get("/chat/config")
def chat_config():
    return jsonify(
        {
            "generation": llm.describe(),
            "retrieval": {
                "top_k": settings.retrieval.fusion_top_k,
                "vector_top_n": settings.retrieval.vector_top_n,
                "keyword_top_n": settings.retrieval.bm25_top_n,
                "similarity_threshold": settings.retrieval.similarity_threshold,
                "rerank_enabled": settings.retrieval.rerank_enabled,
                "context_token_budget": settings.retrieval.context_token_budget,
            },
            "bm25": bm25.describe(),
            "rerank": rerank.describe(),
            "context": context_builder.describe(),
            "pipeline": rag.describe(),
        }
    )


@bp.post("/retrieve")
def retrieve():
    payload = _payload()
    question = str(payload.get("question") or payload.get("query") or "").strip()
    if not question:
        return jsonify({"error": "\u7f3a\u5c11 question"}), 400

    from ..retrieval import vector_store

    doc_ids = knowledge.resolve_retrieval_scope(payload)
    if doc_ids != [] and not vector_store.count():
        return jsonify({"error": "\u5411\u91cf\u5e93\u8fd8\u662f\u7a7a\u7684\uff0c\u5148\u8dd1 POST /api/index"}), 409

    try:
        result = rag.retrieve(
            question,
            top_k=_int_or_none(payload.get("top_k")),
            use_hybrid=_truthy(payload.get("hybrid"), True),
            use_rerank=None if payload.get("rerank") is None else _truthy(payload.get("rerank"), False),
            doc_ids=doc_ids,
            budget=_int_or_none(payload.get("budget")),
            threshold=_float_or_none(payload.get("threshold")),
            use_rewrite=None if payload.get("rewrite") is None else _truthy(payload.get("rewrite"), False),
        )
    except Exception as error:  # noqa: BLE001 - \u628a\u539f\u56e0\u5e26\u56de\u524d\u7aef
        logger.exception("\u68c0\u7d22\u5931\u8d25")
        return jsonify({"error": f"\u68c0\u7d22\u5931\u8d25\uff1a{error}"}), 502

    return jsonify(result)


@bp.post("/chat")
def chat():
    payload = _payload()
    question = str(payload.get("question") or "").strip()
    if not question:
        return jsonify({"error": "\u7f3a\u5c11 question"}), 400

    from ..retrieval import vector_store

    use_knowledge_base = _truthy(payload.get("use_knowledge_base"), True)
    doc_ids = knowledge.resolve_retrieval_scope(payload) if use_knowledge_base else None
    if use_knowledge_base and doc_ids != [] and not vector_store.count():
        return jsonify({"error": "\u5411\u91cf\u5e93\u8fd8\u662f\u7a7a\u7684\uff0c\u5148\u8dd1 POST /api/index"}), 409

    top_k = _int_or_none(payload.get("top_k"))
    use_hybrid = _truthy(payload.get("hybrid"), True)
    use_rerank = None if payload.get("rerank") is None else _truthy(payload.get("rerank"), False)
    budget = _int_or_none(payload.get("budget"))
    threshold = _float_or_none(payload.get("threshold"))
    history = payload.get("history") or []
    if not isinstance(history, list):
        return jsonify({"error": "history 必须是数组"}), 400
    def generate():
        trace = tracing.new_trace(
            "chat", question=question, top_k=top_k, hybrid=use_hybrid, rerank=use_rerank,
            use_knowledge_base=use_knowledge_base, kb_id=payload.get("kb_id")
        )
        try:
            with trace.span("混\u5408\u68c0\u7d22", top_k=top_k, hybrid=use_hybrid) as span:
                found = rag.retrieve(
                    question,
                    top_k=top_k,
                    use_hybrid=use_hybrid,
                    use_rerank=use_rerank,
                    doc_ids=doc_ids,
                    budget=budget,
                    threshold=threshold,
                ) if use_knowledge_base else {"citations": [], "hits": [], "context_text": "", "diagnostics": {}}
                span.fields.update(
                    {
                        "returned": found["diagnostics"].get("returned"),
                        "rerank_used": found["diagnostics"].get("rerank_used"),
                    }
                )
        except Exception as error:  # noqa: BLE001
            logger.exception("\u68c0\u7d22\u9636\u6bb5\u5931\u8d25")
            tracing.report(trace, {"error": str(error)})
            yield _sse({"type": "error", "message": f"\u68c0\u7d22\u5931\u8d25\uff1a{error}"})
            return

        citations = found["citations"]
        yield _sse(
            {
                "type": "sources",
                "knowledge_base_used": use_knowledge_base,
                "kb_id": payload.get("kb_id", "all") if use_knowledge_base else None,
                "citations": citations,
                "hits": [
                    {
                        "chunk_id": hit.get("chunk_id"),
                        "doc_name": hit.get("doc_name"),
                        "line_start": hit.get("line_start"),
                        "line_end": hit.get("line_end"),
                        "score": hit.get("score"),
                        "rerank_score": hit.get("rerank_score"),
                        "routes": hit.get("routes"),
                        "symbol": hit.get("symbol"),
                    }
                    for hit in found["hits"]
                ],
                "diagnostics": found["diagnostics"],
                "trace": trace.to_dict(),
            }
        )

        if use_knowledge_base and not citations:
            if doc_ids == []:
                scoped_ids = knowledge.resolve_scope(payload)
                message = (f"当前分类有 {len(scoped_ids)} 份文件，但尚无可检索片段。"
                           "请在「知识库 → 向量库」点击「增量入库」；若入库失败，请查看具体错误。"
                           if scoped_ids else "当前分类没有文件，请先上传或移入文件，再进行入库。")
            else:
                message = rag.NO_CONTEXT_ANSWER
            yield _sse({"type": "content", "text": message})
            yield _sse({"type": "done", "finish_reason": "no_context", "usage": {}})
            tracing.report(trace, {"finish_reason": "no_context", "citations": 0})
            return

        messages = rag.build_messages(question, found["context_text"], history, use_knowledge_base=use_knowledge_base)
        answer_chars = 0
        reasoning_chars = 0
        finish_reason = ""
        usage: dict | None = None
        actual_model = settings.llm.chat_model
        try:
            with trace.span("\u751f\u6210", model=settings.llm.chat_model) as span:
                # until_content：推理模型偶尔会把预算全烧在推理上，正文空白，
                # 那种情况自动换备用模型重生成，不让用户拿到空回答。
                for event in llm.stream_chat_until_content(messages):
                    if event["type"] == "done":
                        finish_reason = event.get("finish_reason") or ""
                        usage = event.get("usage")
                        actual_model = event.get("actual_model") or actual_model
                        yield _sse(
                            {
                                "type": "done",
                                "finish_reason": event.get("finish_reason"),
                                "usage": usage,
                                "actual_model": event.get("actual_model"),
                                "actual_provider": event.get("actual_provider"),
                                "attempts": event.get("attempts"),
                            }
                        )
                    elif event["type"] == "reasoning":
                        reasoning_chars += len(event.get("text") or "")
                        if settings.llm.emit_reasoning:
                            yield _sse(event)
                    elif event["type"] == "notice":
                        yield _sse(event)
                    else:
                        answer_chars += len(event.get("text") or "")
                        yield _sse(event)
                span.fields.update(
                    {
                        "answer_chars": answer_chars,
                        "reasoning_chars": reasoning_chars,
                        "completion_tokens": (usage or {}).get("completion_tokens"),
                        "actual_model": actual_model,
                    }
                )
        except llm.LLMError as error:
            logger.warning("\u751f\u6210\u5931\u8d25\uff1a%s", error)
            tracing.report(trace, {"error": str(error), "finish_reason": "error"})
            yield _sse({"type": "error", "message": f"\u751f\u6210\u5931\u8d25\uff1a{error}",
                        "actual_model": error.actual_model, "actual_provider": error.actual_provider,
                        "attempts": error.attempts})
        except Exception as error:  # noqa: BLE001
            logger.exception("\u751f\u6210\u9636\u6bb5\u672a\u77e5\u5f02\u5e38")
            tracing.report(trace, {"error": str(error), "finish_reason": "error"})
            yield _sse({"type": "error", "message": f"\u751f\u6210\u5f02\u5e38\uff1a{error}"})
        else:
            tracing.report(
                trace,
                {
                    "finish_reason": finish_reason,
                    "answer_chars": answer_chars,
                    "actual_model": actual_model,
                    "reasoning_chars": reasoning_chars,
                    "citations": len(citations),
                },
            )

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"  # \u9632\u4ee3\u7406\u7f13\u51b2\uff0c\u5426\u5219\u6d41\u5f0f\u4f1a\u53d8\u6210\u4e00\u6b21\u6027
    response.headers["Connection"] = "keep-alive"
    return response

