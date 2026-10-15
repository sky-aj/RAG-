"""RAG \u4e3b\u6d41\u7a0b\uff1a\u68c0\u7d22 -> \u62fc\u4e0a\u4e0b\u6587 -> \u7ec4\u63d0\u793a\u8bcd -> \u751f\u6210\u3002

\u653e\u5728 app \u6839\u4e0b\u800c\u4e0d\u5728 retrieval \u91cc\uff0c\u662f\u56e0\u4e3a\u5b83\u540c\u65f6\u4f9d\u8d56\u68c0\u7d22\u5c42\u4e0e llm \u5c42\uff0c
\u5c5e\u4e8e\u4e24\u5c42\u4e4b\u4e0a\u7684\u7f16\u6392\u800c\u4e0d\u662f\u67d0\u4e00\u5c42\u7684\u5185\u90e8\u5b9e\u73b0\u3002

\u8fd9\u91cc\u53ea\u505a\u4e00\u4ef6\u4e8b\uff1a\u628a\u201c\u7528\u6237\u95ee\u9898\u201d\u53d8\u6210\u201c\u6a21\u578b\u80fd\u76f4\u63a5\u56de\u7b54\u7684\u4e00\u6b21\u8c03\u7528\u201d\u3002
"""
from __future__ import annotations

import logging

from .config import settings
from . import query
from .retrieval import context as context_builder
from .retrieval import hybrid

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """\
\u4f60\u662f\u4e00\u4e2a\u4e25\u8c28\u7684\u77e5\u8bc6\u5e93\u52a9\u624b\u3002\u53ea\u80fd\u4f9d\u636e\u4e0b\u9762\u7ed9\u51fa\u7684\u3010\u53c2\u8003\u7247\u6bb5\u3011\u56de\u7b54\u3002

\u89c4\u5219\uff1a
1. \u7b54\u6848\u91cc\u5f15\u7528\u4fe1\u606f\u65f6\uff0c\u7528\u3010\u7247\u6bb5 N\u3011\u6807\u6ce8\u6765\u6e90\uff0cN \u5bf9\u5e94\u4e0b\u9762\u7247\u6bb5\u7684\u7f16\u53f7\u3002
2. \u53c2\u8003\u7247\u6bb5\u91cc\u6ca1\u6709\u7684\u4fe1\u606f\uff0c\u4e0d\u8981\u7f16\u3002\u76f4\u63a5\u8bf4\u201c\u7ed9\u5b9a\u8d44\u6599\u91cc\u6ca1\u6709\u8fd9\u4e2a\u4fe1\u606f\u201d\uff0c
   \u5e76\u8bf4\u6e05\u7f3a\u4ec0\u4e48\u3002
3. \u56de\u7b54\u7528\u4e2d\u6587\uff0c\u7ed3\u6784\u6e05\u6670\uff0c\u4e0d\u5ba2\u5957\uff0c\u4e0d\u91cd\u590d\u95ee\u9898\u3002
4. \u5982\u679c\u591a\u4e2a\u7247\u6bb5\u8bf4\u7684\u662f\u4e00\u56de\u4e8b\uff0c\u5408\u5e76\u6982\u62ec\uff0c\u4e0d\u8981\u9010\u6761\u5ff5\u3002

\u3010\u53c2\u8003\u7247\u6bb5\u3011
{context}
"""

NO_CONTEXT_ANSWER = (
    "\u77e5\u8bc6\u5e93\u91cc\u6ca1\u627e\u5230\u8ddf\u8fd9\u4e2a\u95ee\u9898\u76f8\u5173\u7684\u7247\u6bb5\uff0c\u6240\u4ee5\u4e0d\u80fd\u51ed\u8d44\u6599\u56de\u7b54\u3002\n"
    "\u53ef\u4ee5\u8bd5\u8bd5\uff1a\u6362\u4e2a\u95ee\u6cd5\u3001\u628a\u76f8\u5173\u6587\u6863\u4e0a\u4f20\u5e76\u5165\u5e93\uff0c"
    "\u6216\u5728\u77e5\u8bc6\u5e93\u9875\u628a\u76f8\u4f3c\u5ea6\u9608\u503c\u8c03\u4f4e\u3002"
)


def retrieve(
    question: str,
    top_k: int | None = None,
    use_hybrid: bool = True,
    use_rerank: bool | None = None,
    doc_ids: list[str] | None = None,
    budget: int | None = None,
    threshold: float | None = None,
    use_rewrite: bool | None = None,
) -> dict:
    """\u53ea\u68c0\u7d22\u3001\u4e0d\u751f\u6210\u3002\u524d\u7aef\u7684\u68c0\u7d22\u6d4b\u8bd5\u4e0e\u8c03\u8bd5\u90fd\u8d70\u8fd9\u91cc\u3002"""
    # 步骤 7：打开 QUERY_REWRITE_ENABLED 时先拆成互补子查询再召回；
    # 默认关闭，行为与步骤 5 完全一致。
    enabled = settings.query.rewrite_enabled if use_rewrite is None else use_rewrite
    rewrite_notices: list[dict] = []
    queries = query.rewrite(question, on_notice=rewrite_notices.append) if enabled and doc_ids != [] else [question]
    found = hybrid.hybrid_search(
        queries[0] if len(queries) == 1 else queries,
        top_k=top_k,
        use_hybrid=use_hybrid,
        use_rerank=use_rerank,
        doc_ids=doc_ids,
        threshold=threshold,
    )
    packed = context_builder.build_context(found["hits"], budget=budget)

    return {
        "query": question,
        # 子查询列表透出去：前端/脚本能确认改写到底改成了什么，
        # 不然「改写生效」只能靠 notes 里那句话来推断。
        "queries": found.get("queries", [question]),
        "hits": found["hits"],
        "citations": packed["citations"],
        "context_text": packed["text"],
        "diagnostics": {
            **found["diagnostics"],
            "notes": [*(found["diagnostics"].get("notes") or []),
                      *(notice.get("text", "") for notice in rewrite_notices)],
            "context_tokens": packed["tokens"],
            "context_budget": packed["budget"],
            "context_used": packed["used"],
            "context_dropped": packed["dropped"],
            "merged_from_hits": packed["merged_from"],
        },
    }


def build_messages(question: str, context_text: str, history: list[dict] | None = None,
                   use_knowledge_base: bool = True) -> list[dict]:
    """\u7ec4\u63d0\u793a\u8bcd\u3002

    \u5386\u53f2\u53ea\u5e26\u6700\u8fd1\u51e0\u8f6e\uff1a\u5bf9\u8bdd\u5386\u53f2\u4f1a\u5360\u63d0\u793a\u8bcd\u9884\u7b97\uff0c
    \u800c RAG \u7684\u91cd\u70b9\u662f\u53c2\u8003\u7247\u6bb5\uff0c\u4e0d\u80fd\u8ba9\u95f2\u804a\u628a\u7247\u6bb5\u6324\u51fa\u53bb\u3002
    """
    prompt = SYSTEM_PROMPT.format(context=context_text or "（无）") if use_knowledge_base else (
        "你是一个中文助手。直接清晰地回答用户的问题，必要时使用 Markdown 组织内容。"
        "本次没有检索知识库，不要编造知识库来源或【片段 N】引用；不确定的信息请明确说明。"
    )
    messages = [{"role": "system", "content": prompt}]

    for turn in (history or [])[-4:]:
        if not isinstance(turn, dict):
            continue
        role = turn.get("role")
        content = (turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": question})
    return messages


def describe() -> dict:
    return {
        "top_k": settings.retrieval.fusion_top_k,
        "vector_top_n": settings.retrieval.vector_top_n,
        "keyword_top_n": settings.retrieval.bm25_top_n,
        "threshold": settings.retrieval.similarity_threshold,
        "rerank_enabled": settings.retrieval.rerank_enabled,
        "context": context_builder.describe(),
    }

