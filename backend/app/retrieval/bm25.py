"""BM25 \u5173\u952e\u5b57\u68c0\u7d22\u3002

\u4e3a\u4ec0\u4e48\u81ea\u5df1\u7ef4\u62a4\u4e00\u4e2a\u5185\u5b58\u7d22\u5f15\u800c\u4e0d\u662f\u6bcf\u6b21\u73b0\u7b97\uff1a
BM25 \u8981\u7edf\u8ba1\u8bcd\u9891\u4e0e\u6587\u6863\u9891\u7387\uff0c\u91cd\u5efa\u4e00\u6b21\u5c31\u662f\u4e00\u6b21\u5168\u91cf\u904d\u5386\uff1b
\u6bcf\u4e2a\u8bf7\u6c42\u91cd\u5efa\u4f1a\u628a\u54cd\u5e94\u65f6\u95f4\u5783\u573e\u6389\u3002
\u7528\u4e00\u4e2a\u7b7e\u540d\uff08\u70b9\u6570 + \u6700\u540e\u5165\u5e93\u65f6\u95f4\uff09\u5224\u65ad\u7d22\u5f15\u662f\u5426\u8fc7\u671f\uff0c\u53d8\u4e86\u624d\u91cd\u5efa\u3002

\u5206\u8bcd\u7b56\u7565\uff1a\u4e2d\u6587\u7528 jieba\uff0c\u82f1\u6587 / \u4ee3\u7801\u6807\u8bc6\u7b26\u6574\u4f53\u4fdd\u7559\u3002
\u628a `SlidingWindowCounter` \u5207\u6210 `sliding` `window` `counter` \u4f1a\u4e22\u6389\u201c\u4eba\u5bb6\u786e\u5b9e\u5199\u4e86\u8fd9\u4e2a\u540d\u5b57\u201d
\u8fd9\u4e2a\u4fe1\u53f7\uff0c\u800c\u7528\u6237\u7ecf\u5e38\u76f4\u63a5\u641c\u4ee3\u7801\u91cc\u7684\u540d\u5b57\u3002
"""
from __future__ import annotations

import logging
import re
import threading
from collections import Counter
from typing import Iterable, Sequence

from ..config import settings

logger = logging.getLogger(__name__)

_CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+|[\u4e00-\u9fff]")

_lock = threading.Lock()
_index = None
_index_signature: tuple | None = None


def tokenize(text: str) -> list[str]:
    """\u4e2d\u6587\u5207\u8bcd + \u82f1\u6587 / \u4ee3\u7801\u6807\u8bc6\u7b26\u4fdd\u7559\u3002

    jieba \u5bf9\u96be\u8bcd\u4f1a\u518d\u5207\u4e00\u5c42\uff08\u4f8b\u5982 `\u6ed1\u52a8\u7a97\u53e3` -> `\u6ed1\u52a8` + `\u7a97\u53e3`\uff09\uff0c
    \u540c\u65f6\u4fdd\u7559\u539f\u8bcd\uff0c\u8fd9\u6837\u6574\u8bcd\u547d\u4e2d\u4e0e\u5b50\u8bcd\u547d\u4e2d\u90fd\u80fd\u8ba1\u5206\u3002
    """
    tokens: list[str] = []
    for piece in _TOKEN.findall(text or ""):
        if _CJK.match(piece):
            tokens.append(piece)
            continue
        lowered = piece.lower()
        tokens.append(lowered)
        # \u9a7c\u5cf0\u547d\u540d\u518d\u62c6\u4e00\u5c42\uff0c\u4fbf\u4e8e\u7528\u5c0f\u5199\u5173\u952e\u8bcd\u547d\u4e2d
        parts = re.findall(r"[A-Z]?[a-z]+|\d+", piece)
        if len(parts) > 1:
            tokens.extend(part.lower() for part in parts)

    # \u4e2d\u6587\u7247\u6bb5\u53e6\u8dd1\u4e00\u904d jieba\uff0c\u628a\u8fde\u7eed\u6c49\u5b57\u5f53\u8bcd\u770b
    if _CJK.search(text or ""):
        try:
            import jieba

            for word in jieba.lcut(text or ""):
                word = word.strip()
                if len(word) > 1 and _CJK.search(word):
                    tokens.append(word)
        except ImportError:  # \u6ca1\u88c5 jieba \u4e5f\u80fd\u7528\uff0c\u53ea\u662f\u4e2d\u6587\u53ec\u56de\u5f31\u4e00\u4e9b
            logger.warning("jieba \u672a\u5b89\u88c5\uff0cBM25 \u9000\u5316\u4e3a\u5355\u5b57\u5207\u5206")

    return tokens


def _signature() -> tuple:
    """\u7528\u5e93\u5185\u70b9\u6570\u4e0e\u6700\u540e\u5165\u5e93\u65f6\u95f4\u505a\u7b7e\u540d\u3002"""
    from . import indexer, vector_store

    return (vector_store.count(), indexer.load_state().get("last_run"))


def _build() -> list[dict]:
    """\u4ece\u5411\u91cf\u5e93\u62c9\u5168\u90e8 payload\uff0c\u6784\u5efa\u5185\u5b58\u7d22\u5f15\u3002"""
    from . import vector_store

    records: list[dict] = []
    client = vector_store.get_client()
    if not vector_store.collection_exists():
        return records

    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=settings.qdrant.collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in points:
            payload = dict(point.payload or {})
            payload["point_id"] = point.id
            payload["_tokens"] = tokenize(payload.get("text", ""))
            records.append(payload)
        if offset is None:
            break

    return records


def _ensure_index():
    """\u8fd4\u56de (bm25, records)\uff0c\u5fc5\u8981\u65f6\u91cd\u5efa\u3002"""
    global _index, _index_signature

    from rank_bm25 import BM25Okapi

    signature = _signature()
    with _lock:
        if _index is not None and _index_signature == signature:
            return _index

        records = _build()
        if not records:
            _index = (None, [])
            _index_signature = signature
            return _index

        corpus = [record["_tokens"] for record in records]
        bm25 = BM25Okapi(corpus)
        logger.info("BM25 \u7d22\u5f15\u5df2\u91cd\u5efa\uff1a%d \u6761", len(records))
        _index = (bm25, records)
        _index_signature = signature
        return _index


def invalidate() -> None:
    """\u5165\u5e93 / \u6e05\u5e93\u540e\u4e3b\u52a8\u8c03\u7528\uff0c\u4e0b\u6b21\u68c0\u7d22\u91cd\u5efa\u3002"""
    global _index, _index_signature
    with _lock:
        _index = None
        _index_signature = None


def search(query: str, top_n: int | None = None, doc_ids: Iterable[str] | None = None) -> list[dict]:
    """\u8fd4\u56de\u5e26 `score` \u7684 payload\uff0c\u6309 BM25 \u5206\u964d\u5e8f\u3002

    BM25 \u5206\u662f\u65e0\u4e0a\u754c\u7684\uff0c\u4e0d\u80fd\u4e0e\u4f59\u5f26\u76f8\u4f3c\u5ea6\u76f4\u63a5\u6bd4\u5927\u5c0f\uff0c
    \u6240\u4ee5\u878d\u5408\u90a3\u4e00\u6b65\u8d70\u7684\u662f RRF\uff08\u770b\u6392\u540d\u4e0d\u770b\u5206\u6570\uff09\u3002
    """
    limit = top_n or settings.retrieval.bm25_top_n
    wanted = None if doc_ids is None else set(doc_ids)
    if wanted == set():
        return []
    bm25, records = _ensure_index()
    if bm25 is None:
        return []

    tokens = tokenize(query)
    if not tokens:
        return []

    scores = bm25.get_scores(tokens)

    ranked = sorted(
        zip(records, scores),
        key=lambda pair: pair[1],
        reverse=True,
    )

    hits: list[dict] = []
    for record, score in ranked:
        if score <= 0:
            break  # \u5206\u5df2\u7ecf\u964d\u5230 0\uff0c\u540e\u9762\u5168\u662f 0\uff0c\u518d\u7b97\u6ca1\u610f\u4e49
        if wanted is not None and record.get("doc_id") not in wanted:
            continue
        hit = {key: value for key, value in record.items() if not key.startswith("_")}
        hit["score"] = float(score)
        hits.append(hit)
        if len(hits) >= limit:
            break

    return hits


def size() -> int:
    """已建索引的条数。**不会因为调它而构建索引**。

    这一点很重要：读配置、健康检查这类接口不能顺手把
    数百条的分词与 BM25 矩阵算一遍，那会让首次请求无故变慢。
    """
    if _index is None:
        return 0
    return len(_index[1])


def describe() -> dict:
    return {
        "tokenizer": "jieba + 标识符保留",
        "top_n": settings.retrieval.bm25_top_n,
        "indexed": size(),
        "built": _index is not None,
    }
