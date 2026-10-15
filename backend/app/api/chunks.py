"""分块接口。

两种用法：
  POST /api/chunks/preview   传文件当场看分块结果，不落盘，用来调参
  GET  /api/chunks/<doc_id>  看已上传文档的分块结果
两条路径共用同一段切分逻辑，避免「预览和入库结果不一致」这种坑。
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from flask import Blueprint, jsonify, request

from ..config import settings
from ..ingest import SUPPORTED_SUFFIXES, parse_file
from ..ingest.chunking import available_strategies, chunk_document

bp = Blueprint("chunks", __name__)


def _int_param(name: str) -> int | None:
    raw = request.args.get(name) or request.form.get(name)
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _run(document, strategy: str | None):
    return chunk_document(
        document,
        strategy=strategy,
        target_tokens=_int_param("target_tokens"),
        max_tokens=_int_param("max_tokens"),
        overlap_tokens=_int_param("overlap_tokens"),
    )


@bp.get("/chunks/strategies")
def list_strategies():
    return jsonify(
        {
            "strategies": available_strategies(),
            "defaults": {
                "target_tokens": settings.chunking.target_tokens,
                "max_tokens": settings.chunking.max_tokens,
                "overlap_tokens": settings.chunking.overlap_tokens,
            },
        }
    )


@bp.get("/chunks/<doc_id>")
def chunks_for_document(doc_id: str):
    from .documents import load_registry

    record = next((item for item in load_registry() if item["doc_id"] == doc_id), None)
    if record is None:
        return jsonify({"error": "文档不存在"}), 404

    path = settings.server.upload_dir / record["stored_name"]
    if not path.exists():
        return jsonify({"error": "文件已丢失，请重新上传"}), 410

    document = parse_file(path, doc_id, record["name"])
    report = _run(document, request.args.get("strategy"))
    return jsonify(report.summary(preview=int(request.args.get("preview", 12))))


@bp.post("/chunks/preview")
def preview_chunks():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少 file 字段"}), 400

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return jsonify({"error": f"暂不支持的文件类型：{suffix or '未知'}"}), 400

    raw = uploaded.read()
    digest = hashlib.sha1(raw).hexdigest()[:12]
    temp_dir = settings.backend_dir / "data" / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    temp_path = temp_dir / f"{digest}{suffix}"
    temp_path.write_bytes(raw)

    try:
        document = parse_file(temp_path, digest, uploaded.filename)
        report = _run(document, request.args.get("strategy"))
        payload = report.summary(preview=int(request.args.get("preview", 12)))
        payload["all_strategies"] = available_strategies()
        return jsonify(payload)
    except Exception as error:  # noqa: BLE001 - 失败要把原因带回前端
        return jsonify({"error": f"切分失败：{error}"}), 422
    finally:
        temp_path.unlink(missing_ok=True)

@bp.post("/chunks/fetch")
def fetch_chunks():
    """按 chunk_id 批量取回片段正文。

    前端点开引用卡片时要看原文，而检索返回里的 text 可能已被相邻合并，
    所以引用展开走这里，拿的是入库时那份干净的块。
    """
    body = request.get_json(silent=True) or {}
    ids = body.get("chunk_ids") or body.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    if not isinstance(ids, list):
        return jsonify({"error": "chunk_ids 必须是数组"}), 400

    from ..retrieval import vector_store

    try:
        found = vector_store.fetch_chunks([str(item) for item in ids][:60])
    except Exception as error:  # noqa: BLE001
        return jsonify({"error": f"读取片段失败：{error}"}), 502

    return jsonify({"count": len(found), "items": found, "missing": len(ids) - len(found)})
