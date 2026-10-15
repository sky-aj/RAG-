"""向量化与入库接口（步骤 4）。

  GET    /api/index           看库的现状：collection、已入库文档、点数量
  POST   /api/index           把文档灌进库（默认只灌还没入库的）
  DELETE /api/index           清空指定分类/文档；整库须显式 reset_all=true
  POST   /api/search          纯语义检索（混合检索在步骤 5）

参数都走 query string 或 JSON body 两种形式，方便 curl 与前端各自顺手。
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..config import settings
from .. import knowledge
from ..retrieval import embedder, indexer, vector_store

bp = Blueprint("index", __name__)
logger = logging.getLogger(__name__)


def _payload() -> dict:
    """query string 与 JSON body 合并，query 优先。"""
    body = request.get_json(silent=True) or {}
    merged = dict(body)
    for key in ("doc_id", "force", "strategy", "top_n", "query", "kb_id"):
        value = request.args.get(key)
        if value is not None:
            merged[key] = value
    return merged


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _doc_ids(payload: dict) -> list[str]:
    """支持 ?doc_id=a&doc_id=b，也支持 JSON 里的 doc_id: "a" / ["a","b"]。"""
    raw = payload.get("doc_id")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item]
    return [str(raw)]


@bp.get("/index")
def index_status():
    status = indexer.status()
    if request.args.get("kb_id") is not None:
        ids = set(knowledge.resolve_scope({"kb_id": request.args["kb_id"]}))
        scoped = [item for item in status["indexed"] if item[0] in ids]
        status["scope"] = {"kb_id": request.args["kb_id"], "documents": len(ids),
                           "indexed_count": len(scoped), "points": sum(item[1].get("chunks", 0) for item in scoped)}
    return jsonify(status)


@bp.post("/index")
def index_documents():
    from .documents import load_registry

    payload = _payload()
    registry = load_registry()
    wanted = knowledge.resolve_scope(payload)

    if wanted is not None:
        known = {record["doc_id"] for record in registry}
        unknown = [doc_id for doc_id in wanted if doc_id not in known]
        if unknown:
            return jsonify({"error": "文档不存在：" + ", ".join(unknown)}), 404
        registry = [record for record in registry if record["doc_id"] in set(wanted)]

    try:
        report = indexer.index_documents(
            registry,
            strategy=payload.get("strategy") or None,
            force=_truthy(payload.get("force")),
        )
    except embedder.SiliconFlowError as error:
        return jsonify({"error": f"向量化失败：{error}"}), 502
    except vector_store.VectorStoreError as error:
        return jsonify({"error": f"向量库错误：{error}"}), 500

    report["store"] = vector_store.info()
    return jsonify(report)


@bp.delete("/index")
def clear_index():
    payload = _payload()
    if _truthy(payload.get("reset_all")):
        if payload.get("kb_id") not in (None, knowledge.ALL) or _doc_ids(payload):
            return jsonify({"error": "整库清空不能同时指定局部范围"}), 400
        return jsonify(indexer.reset_index())
    wanted = knowledge.resolve_scope(payload)
    if wanted is None:
        return jsonify({"error": "清空需要明确指定知识库范围；整库清空须设置 reset_all=true"}), 400

    removed_total = 0
    for doc_id in wanted:
        removed_total += indexer.remove_document(doc_id)
    return jsonify({"ok": True, "removed": removed_total, "total_points": vector_store.count()})


@bp.post("/search")
def search():
    payload = _payload()
    query = str(payload.get("query") or "").strip()
    if not query:
        return jsonify({"error": "缺少 query"}), 400

    doc_ids = knowledge.resolve_retrieval_scope(payload)
    if doc_ids != [] and not vector_store.count():
        return jsonify({"error": "向量库还是空的，先跑 POST /api/index"}), 409

    try:
        top_n = int(payload.get("top_n") or settings.retrieval.vector_top_n)
    except (TypeError, ValueError):
        return jsonify({"error": "top_n 必须是整数"}), 400

    try:
        result = indexer.search_semantic(query, top_n=top_n, doc_ids=doc_ids)
    except embedder.SiliconFlowError as error:
        return jsonify({"error": f"查询向量化失败：{error}"}), 502

    return jsonify(result)
