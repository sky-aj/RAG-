"""\u6df7\u5408\u68c0\u7d22\uff1a\u8bed\u4e49\uff08\u5411\u91cf\uff09+ \u5173\u952e\u5b57\uff08BM25\uff09\uff0cRRF \u878d\u5408\uff0c\u53ef\u9009\u91cd\u6392\u3002

\u4e00\u6b21\u68c0\u7d22\u7684\u56db\u4e2a\u9636\u6bb5\uff08\u6bcf\u6bb5\u90fd\u80fd\u5355\u72ec\u770b\u7ed3\u679c\uff0c\u65b9\u4fbf\u5b9a\u4f4d\u54ea\u4e00\u6bb5\u5dee\uff09\uff1a
  1. \u8bed\u4e49\u8def\uff1a\u5411\u91cf\u68c0\u7d22\uff0c\u4e00\u6b21\u62ff vector_top_n\uff08\u9ed8\u8ba4 80\uff09\u6761\u5019\u9009
  2. \u5173\u952e\u5b57\u8def\uff1aBM25\uff0c\u4e00\u6b21\u62ff bm25_top_n\uff08\u9ed8\u8ba4 40\uff09\u6761
  3. RRF \u878d\u5408\uff1a\u53ea\u770b\u6392\u540d\u4e0d\u770b\u5206\u6570\uff0c\u907f\u5f00\u201c\u4f59\u5f26\u76f8\u4f3c\u5ea6\u4e0e BM25 \u5206\u4e0d\u53ef\u6bd4\u201d\u7684\u5751
  4. \u53ef\u9009\u91cd\u6392 + \u6bcf\u6587\u6863\u914d\u989d

\u4e3a\u4ec0\u4e48\u7528 RRF \u800c\u4e0d\u662f\u52a0\u6743\u6c42\u548c\uff1a\u4e24\u8def\u7684\u5206\u6570\u91cf\u7eb2\u5b8c\u5168\u4e0d\u540c\uff08\u4f59\u5f26 0\u20131\uff0c
BM25 \u65e0\u4e0a\u754c\u4e14\u968f\u8bed\u6599\u53d8\uff09\uff0c\u786c\u62df\u5408\u6743\u91cd\u9700\u8981\u53cd\u590d\u8c03\u53c2\uff1b\u6392\u540d\u5374\u662f\u540c\u8d28\u7684\u3002
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable, Sequence

from ..config import settings
from . import bm25, rerank, vector_store

logger = logging.getLogger(__name__)

# RRF \u7684\u5e73\u6ed1\u5e38\u6570\uff0c\u539f\u8bba\u6587\u63a8\u8350 60\uff1a\u8d8a\u5927\u5bf9\u9ad8\u6392\u540d\u8d8a\u5bbd\u5bb9
RRF_K = 60


def _key(hit: dict) -> str:
    return str(hit.get("chunk_id") or hit.get("point_id") or "")


def reciprocal_rank_fusion(
    routes: Sequence[Sequence[dict]],
    weights: Sequence[float] | None = None,
    k: int = RRF_K,
) -> list[dict]:
    """\u591a\u8def\u7ed3\u679c\u878d\u5408\u3002

    \u6bcf\u6761\u7684\u5f97\u5206 = \u03a3 weight / (k + \u8be5\u8def\u4e2d\u7684\u6392\u540d)\u3002
    \u540c\u4e00\u6761\u5728\u591a\u8def\u90fd\u51fa\u73b0\u5c31\u7d2f\u52a0\uff0c\u8fd9\u6b63\u662f\u6df7\u5408\u68c0\u7d22\u60f3\u8981\u7684\u6548\u679c\u3002
    """
    weight_list = list(weights) if weights else [1.0] * len(routes)
    if len(weight_list) != len(routes):
        raise ValueError("\u6743\u91cd\u4e2a\u6570\u5fc5\u987b\u4e0e\u8def\u6570\u4e00\u81f4")

    fused: dict[str, dict] = {}
    scores: dict[str, float] = defaultdict(float)
    sources: dict[str, list[str]] = defaultdict(list)

    for route_index, route in enumerate(routes):
        for rank, hit in enumerate(route, start=1):
            key = _key(hit)
            if not key:
                continue
            scores[key] += weight_list[route_index] / (k + rank)
            sources[key].append("\u8bed\u4e49" if route_index == 0 else "\u5173\u952e\u5b57")
            if key not in fused:
                fused[key] = hit

    merged: list[dict] = []
    for key, hit in fused.items():
        item = dict(hit)
        item["rrf_score"] = round(scores[key], 6)
        item["routes"] = sorted(set(sources[key]))
        merged.append(item)

    merged.sort(key=lambda item: item["rrf_score"], reverse=True)
    return merged


def _apply_document_quota(hits: Sequence[dict], per_document: int) -> tuple[list[dict], list[dict]]:
    """\u6bcf\u4efd\u6587\u6863\u6700\u591a\u7559 per_document \u6761\uff0c\u9632\u6b62\u5355\u4e00\u6587\u6863\u9738\u699c\u3002

    \u8d85\u51fa\u7684\u4e0d\u4e22\uff0c\u653e\u8fdb\u5907\u9009\u91cc\u3002\u5982\u679c\u8fc7\u9608\u540e\u6574\u4f53\u4e0d\u591f\u6570\uff0c
    \u8fd8\u80fd\u4ece\u5907\u9009\u91cc\u56de\u586b\uff0c\u907f\u514d\u201c\u4e3a\u4e86\u591a\u6837\u6027\u628a\u6709\u7528\u7684\u5757\u5168\u6324\u6ca1\u4e86\u201d\u3002
    """
    picked: list[dict] = []
    overflow: list[dict] = []
    tally: dict[str, int] = defaultdict(int)

    for hit in hits:
        doc_id = str(hit.get("doc_id", ""))
        if tally[doc_id] < per_document:
            tally[doc_id] += 1
            picked.append(hit)
        else:
            overflow.append(hit)

    return picked, overflow


def _normalize_scores(hits: Sequence[dict], hybrid: bool) -> None:
    """把每条命中的 `score` 归一成 0~1、越大越相关，原地改。

    两路的量纲不同：语义分是余弦相似度（天然 0~1），BM25 分无上界。
    阈值过滤和前端进度条都需要统一的量纲，所以先在这里拉齐：
      * 命中来自向量路（routes 含「语义」）-> 原样保留，余弦本身可比
      * 纯关键字命中 -> 除以本批 BM25 最高分，变成相对分
    """
    keyword_scores = [
        float(hit.get("score") or 0.0)
        for hit in hits
        if "语义" not in (hit.get("routes") or [])
    ]
    if not hybrid or not keyword_scores:
        return
    top = max(keyword_scores)
    if top <= 0:
        return
    for hit in hits:
        if "语义" in (hit.get("routes") or []):
            continue
        hit["score"] = round(float(hit.get("score") or 0.0) / top, 4)


def _label_scores(hits: Sequence[dict], hybrid: bool, reranked: bool) -> None:
    """把每条命中的 `score` 统一成“0~1 之间、越大越相关”，原地改。

    为什么必须统一：前端引用卡片按 `score` 画进度条。
    混合检索里若重排没生效，排在前面的很可能是关键字路的命中，
    而 BM25 分是无上界的（实测 41 分），进度条会直接溢出。
    三种情形分开处理：
      * 重排生效 -> 用重排分，本来就是 0~1
      * 重排生效但只有前几条进过重排 -> 没分的那几条按名次线性衰减，
        不能拿原来的 BM25/融合分跟重排分同台比，量纲是错位的
      * 纯关键字 -> 除以本条最高分，变成相对分
      * 纯语义   -> 余弦相似度本身就是 0~1，不动
    """
    if not hits:
        return
    if reranked:
        scored = [hit for hit in hits if hit.get("rerank_score") is not None]
        known = len(scored)
        bottom = min((float(item["rerank_score"]) for item in scored), default=0.0)
        span = max(1, len(hits) - known + 1)
        for index, hit in enumerate(hits):
            if hit.get("rerank_score") is not None:
                hit["score"] = float(hit["rerank_score"])
            else:
                decay = max(0.0, 1 - (index - known + 1) / span)
                hit["score"] = round(bottom * decay, 4)
        return
    _normalize_scores(hits, hybrid=hybrid)


# ---------------------------------------------------------------------------
# 步骤 7 的实测结论（脚本：backend/scripts/compare_step7.py，4 条样本）
#
#   指标            基线     改写开启     变化
#   忠实度          90.0%     90.0%     +0.0pp
#   相关性          67.4%     62.1%     -5.3pp
#   精确率         100.0%    100.0%     +0.0pp
#   召回率         100.0%    100.0%     +0.0pp
#   命中率(自算)     75.0%     75.0%     +0.0pp
#
# 多查询在「召回」上没带来任何增量（精确率/召回率/hit_rate 全平），
# 却在「回答相关性」上掉了 5.3pp。合理推断是题面本身已经够明确、
# 语料只有 56 个块，子查询并没有打开新的召回面，反而让上下文里多了
# 一批"相关但不切题"的旁支片段，把回答带偏了。
#
# 所以 QUERY_REWRITE_ENABLED 保持**默认关闭**。改写能力按步骤 7 的验收
# 标准（没有可测量的提升就不合入）保留为可选开关，语料变大、
# 或题面更口语化之后值得重新用同一个脚本再测一次。
# ---------------------------------------------------------------------------


def _multi_query_routes(
    queries: Sequence[str],
    scoped: list[str] | None,
    vector_top_n: int | None,
    keyword_top_n: int | None,
    use_hybrid: bool,
    notes: list[str],
) -> tuple[list[list[dict]], list[dict], list[dict]]:
    """把每个子查询各跑一遍混合检索，返回所有待融合的「路」。

    为什么子查询之间也走 RRF 而不是分数加权：
    和两路融合同一个理由——子查询数量可变、各自分数不可比，排名才是同质的。
    所有子查询的第 1 名并列成一条等权的语义路、一条关键字路，
    这样「原问题」与「改写问题」的权重天然相同，不会因为改写多就压过原问题。
    """
    vector_routes: list[list[dict]] = []
    keyword_routes: list[list[dict]] = []
    first_vector: list[dict] = []
    first_keyword: list[dict] = []

    for index, item in enumerate(queries):
        try:
            found = vector_store.search(
                _embed(item), top_n=vector_top_n or settings.retrieval.vector_top_n, doc_ids=scoped
            )
        except Exception as error:  # noqa: BLE001 - 一路坏了还有别的子查询与关键字路
            logger.warning("语义检索失败（子查询 %d），跳过：%s", index + 1, error)
            if not any("语义路不可用" in note for note in notes):
                notes.append(f"语义路不可用（{type(error).__name__}），本次只用关键字")
            found = []
        vector_routes.append(found)
        if index == 0:
            first_vector = found

        if not use_hybrid:
            continue
        try:
            words = bm25.search(item, top_n=keyword_top_n or settings.retrieval.bm25_top_n, doc_ids=scoped)
        except Exception as error:  # noqa: BLE001
            logger.warning("BM25 检索失败（子查询 %d），跳过：%s", index + 1, error)
            if not any("关键字路不可用" in note for note in notes):
                notes.append(f"关键字路不可用（{type(error).__name__}），本次只用语义")
            words = []
        keyword_routes.append(words)
        if index == 0:
            first_keyword = words

    routes = list(vector_routes)
    routes.extend(keyword_routes)
    return routes, first_vector, first_keyword


def hybrid_search(
    query: str | Sequence[str],
    top_k: int | None = None,
    vector_top_n: int | None = None,
    keyword_top_n: int | None = None,
    use_hybrid: bool = True,
    use_rerank: bool | None = None,
    doc_ids: Iterable[str] | None = None,
    per_document: int = 3,
    threshold: float | None = None,
) -> dict:
    """混合检索主入口。返回带诊断信息的结果，不只给最后的 hits。

    诊断信息很关键：两路各召回多少、融合后多少、重排是否生效，
    前端要能看到，不然效果不好时只能猜。

    `query` 可以是单个问题，也可以是「原问题 + 改写子查询」的列表（步骤 7）。
    多个子查询时，每个都各跑一遍两路，再把所有路一起做 RRF 融合——
    这样互补的子查询能带来并集更大的召回，而不是互相抢名额。
    """
    queries = [query] if isinstance(query, str) else [item for item in query if item]
    if not queries:
        queries = [""]
    primary = queries[0]

    limit = top_k or settings.retrieval.fusion_top_k
    want_rerank = settings.retrieval.rerank_enabled if use_rerank is None else use_rerank
    scoped = None if doc_ids is None else [doc_id for doc_id in doc_ids if doc_id]
    notes: list[str] = []

    if scoped == []:
        routes, first_vector, first_keyword = [], [], []
        notes.append("当前分类没有可检索的文档")
    elif len(queries) == 1:
        notes_holder: list[str] = []
        routes, first_vector, first_keyword = _multi_query_routes(
            queries, scoped, vector_top_n, keyword_top_n, use_hybrid, notes_holder
        )
        notes.extend(notes_holder)
    else:
        routes, first_vector, first_keyword = _multi_query_routes(
            queries, scoped, vector_top_n, keyword_top_n, use_hybrid, notes
        )
        notes.append(f"查询改写生效：原问题 + {len(queries) - 1} 条子查询并行召回")

    vector_hits = first_vector
    keyword_hits = first_keyword

    # 语义路先按阈值过一道，避免全是无关的也进融合（阈值可由请求覆盖）。
    # 注意：多子查询时这里只统计「原问题」那一路的候选数，
    # 全量候选数在 fused 之后已经没有意义，诊断里给出的是各路的并集。
    threshold = settings.retrieval.similarity_threshold if threshold is None else float(threshold)
    vector_kept = [hit for hit in vector_hits if hit.get("score", 0) >= threshold]
    dropped = len(vector_hits) - len(vector_kept)

    # 阈值要能同时管住两路，否则调它时结果一条不少，用起来像没生效。
    # 但语义分是余弦（0~1），BM25 分无上界（实测能到 41），不能直接比，
    # 所以先融合、归一，再统一过闸。
    fused = reciprocal_rank_fusion(routes)
    route_candidates = sum(len(route) for route in routes)

    _normalize_scores(fused, hybrid=use_hybrid)
    if threshold > 0:
        above, below_threshold = [], []
        for hit in fused:
            (above if float(hit.get("score") or 0.0) >= threshold else below_threshold).append(hit)
        fused = above
    else:
        below_threshold = []

    fused, overflow = _apply_document_quota(fused, per_document)

    picked = fused[:limit]
    if len(picked) < limit:
        picked.extend(overflow[: limit - len(picked)])

    rerank_used = False
    if want_rerank and picked:
        try:
            # 重排用原问题当 query：子查询是为了「召回更全」，
            # 但「哪段最相关」要按用户真正问的那句话来判，不能用改写后的术语版。
            ranked = rerank.rerank(primary, [hit.get("text", "") for hit in picked], top_n=len(picked))
            reordered = []
            for item in ranked:
                if 0 <= item["index"] < len(picked):
                    hit = dict(picked[item["index"]])
                    hit["rerank_score"] = round(item["score"], 4)
                    reordered.append(hit)
            if reordered:
                picked = reordered
                rerank_used = True
        except Exception as error:  # noqa: BLE001 - 重排失败不能影响主流程
            logger.warning("重排失败，保留融合排序：%s", error)
            notes.append(f"重排不可用（{type(error).__name__}），本次用融合排序")

    _label_scores(picked, hybrid=use_hybrid, reranked=rerank_used)

    if dropped:
        notes.append(f"语义路有 {dropped} 条低于相似度阈值 {threshold} 被过滤")
    if below_threshold:
        notes.append(f"阈值 {threshold} 又滤掉 {len(below_threshold)} 条融合结果")

    return {
        "query": primary,
        "queries": list(queries),
        "hits": picked,
        "diagnostics": {
            "hybrid": use_hybrid,
            "subqueries": len(queries),
            "vector_candidates": len(vector_hits),
            "vector_kept": len(vector_kept),
            "keyword_candidates": len(keyword_hits),
            "route_candidates": route_candidates,
            "fused": len(fused),
            "overflow": len(overflow),
            "returned": len(picked),
            "below_threshold": len(below_threshold),
            "threshold": threshold,
            "per_document": per_document,
            "vector_top_n": vector_top_n or settings.retrieval.vector_top_n,
            "keyword_top_n": keyword_top_n or settings.retrieval.bm25_top_n,
            "rerank_requested": want_rerank,
            "rerank_used": rerank_used,
            "notes": notes,
        },
    }


def _embed(query: str) -> list[float]:
    from . import embedder

    return embedder.embed_query(query)
