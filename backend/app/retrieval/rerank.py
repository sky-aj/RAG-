"""\u91cd\u6392\uff08rerank\uff09\u5ba2\u6237\u7aef\uff0c\u8d70\u7845\u57fa\u6d41\u52a8\u7684 bge-reranker-v2-m3\u3002

\u4e3a\u4ec0\u4e48\u653e\u5728 retrieval \u800c\u4e0d\u662f llm\uff1a\u91cd\u6392\u662f\u68c0\u7d22\u94fe\u8def\u7684\u6700\u540e\u4e00\u6b65\uff0c
\u5b83\u53ea\u770b\u201c\u95ee\u9898\u4e0e\u7247\u6bb5\u6709\u591a\u76f8\u5173\u201d\uff0c\u4e0d\u751f\u6210\u4efb\u4f55\u6587\u5b57\u3002

\u4e3a\u4ec0\u4e48\u9700\u8981\u5b83\uff1a\u5411\u91cf\u53ec\u56de\u4e00\u6b21\u62ff 80 \u6761\uff0c\u6392\u5e8f\u9760\u53cc\u5854\u6a21\u578b\u7684\u4f59\u5f26\u76f8\u4f3c\u5ea6\uff0c
\u8bed\u4e49\u76f8\u8fd1\u4f46\u771f\u6b63\u7b54\u975e\u6240\u95ee\u7684\u7247\u6bb5\u5f88\u5bb9\u6613\u6324\u5728\u524d\u9762\uff1b
rerank \u7528\u4ea4\u53c9\u7f16\u7801\u9010\u5bf9\u7b97\uff0c\u7cbe\u5ea6\u9ad8\u5f88\u591a\u4f46\u5f88\u8d35\uff0c\u6240\u4ee5\u53ea\u9002\u5408\u7ed9\u5019\u9009\u96c6\u91cd\u6392\u3002
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

_RETRY_STATUS = {429, 500, 502, 503, 504}


class RerankError(RuntimeError):
    """\u91cd\u6392\u5931\u8d25\u3002"""


def available() -> bool:
    return settings.siliconflow.configured and settings.retrieval.rerank_enabled


def _payload(query: str, documents: Sequence[str], top_n: int) -> dict:
    return {
        "model": settings.siliconflow.rerank_model,
        "query": query,
        "documents": list(documents),
        "top_n": min(top_n, len(documents)),
        "return_documents": False,
    }


def rerank(query: str, documents: Sequence[str], top_n: int | None = None) -> list[dict]:
    """\u8fd4\u56de [{"index": \u539f\u4f4d\u7f6e, "score": \u76f8\u5173\u5ea6}]\uff0c\u6309\u5206\u964d\u5e8f\u3002

    \u8c03\u7528\u65b9\u8981\u5904\u7406\u5931\u8d25\uff1a\u91cd\u6392\u53ea\u662f\u201c\u66f4\u597d\u201d\uff0c\u4e0d\u662f\u201c\u5fc5\u9700\u201d\u3002
    \u8fdc\u7aef\u4e0d\u53ef\u7528\u65f6\u5e94\u5f53\u56de\u9000\u5230\u878d\u5408\u7ed3\u679c\uff0c\u800c\u4e0d\u662f\u628a\u6574\u4e2a\u5bf9\u8bdd\u641e\u6302\u3002
    """
    if not documents:
        return []
    limit = top_n or len(documents)
    attempts = max(1, settings.embedding.max_retries)
    headers = {"Authorization": "Bearer " + settings.siliconflow.api_key, "Content-Type": "application/json"}
    url = settings.siliconflow.base_url.rstrip("/") + "/rerank"
    last_error = ""

    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=settings.embedding.timeout) as client:
                response = client.post(url, json=_payload(query, documents, limit), headers=headers)
        except httpx.HTTPError as error:
            last_error = f"{type(error).__name__}: {error}"
        else:
            if response.status_code == 200:
                data = response.json()
                results = data.get("results") or []
                return [
                    {"index": int(item.get("index", 0)), "score": float(item.get("relevance_score", 0.0))}
                    for item in results
                ]
            last_error = f"HTTP {response.status_code}: {response.text[:200]}"
            if response.status_code not in _RETRY_STATUS:
                raise RerankError(last_error)

        if attempt + 1 < attempts:
            wait = 1.5 * (attempt + 1)
            logger.warning("\u91cd\u6392\u8bf7\u6c42\u5931\u8d25\uff08\u7b2c %d/%d \u6b21\uff09\uff0c%.1f \u79d2\u540e\u91cd\u8bd5\uff1a%s",
                           attempt + 1, attempts, wait, last_error)
            time.sleep(wait)

    raise RerankError(f"\u91cd\u8bd5 {attempts} \u6b21\u4ecd\u5931\u8d25\uff1a{last_error}")


def describe() -> dict:
    return {
        "provider": "siliconflow",
        "model": settings.siliconflow.rerank_model,
        "enabled": settings.retrieval.rerank_enabled,
        "available": available(),
    }
