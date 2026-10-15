"""文档接口。

步骤 2 之后这里的职责变明确：
  上传  -> 落盘 + 立刻解析 + 把结构摘要写进注册表
  列表  -> 前端文档表用的数据
  详情  -> 某份文档的解析结构（轮廓 + 块预览）
  预览  -> 传文件当场看解析结果，不落盘，方便调参
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from ..config import settings
from .. import knowledge
from ..ingest import SUPPORTED_SUFFIXES, parse_file, resolve_strategy
from ..ingest.types import ElementKind

bp = Blueprint("documents", __name__)

def registry_path() -> Path:
    """每次读而不缓存，方便测试与冒烟脚本用环境变量重定向。"""
    return settings.index.registry_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_registry() -> list[dict]:
    path = registry_path()
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []


def save_registry(items: list[dict]) -> None:
    with knowledge.metadata_lock:
        knowledge.atomic_json(registry_path(), items)


def document_count() -> int:
    return len(load_registry())


def update_document_fields(doc_id: str, **fields) -> bool:
    """就地更新某份文档的字段，返回是否命中。

    主要用于步骤 4：入库后把真实块数写回注册表，
    前端的“分块总数”与表格里的“块”列才不会一直显示 0。
    """
    with knowledge.metadata_lock:
        items = load_registry()
        for item in items:
            if item["doc_id"] == doc_id:
                item.update(fields)
                save_registry(items)
                return True
    return False


def _human_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes} B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.0f} KB"
    return f"{num_bytes / 1024 / 1024:.1f} MB"


def _preview_elements(document, limit: int = 12) -> list[dict]:
    """给前端的块预览：跳过纯页标记，保留有内容的块。"""
    picked: list[dict] = []
    for element in document.elements:
        if len(picked) >= limit:
            break
        if element.kind is ElementKind.PAGE_MARK and not element.text:
            continue
        picked.append(
            {
                "kind": element.kind.value,
                "level": element.level,
                "language": element.language,
                "line_start": element.line_start,
                "line_end": element.line_end,
                "page": element.page,
                "section": element.section,
                "text": element.text if len(element.text) <= 400 else element.text[:400] + "…",
            }
        )
    return picked


def _parse_and_record(path: Path, doc_id: str, display_name: str, base: dict) -> dict:
    """解析并合并进文档记录。解析失败不抛异常，把错误写进状态里。"""
    try:
        document = parse_file(path, doc_id, display_name)
    except Exception as error:  # noqa: BLE001 - 解析失败要能在界面上看到原因
        return {**base, "status": "解析失败", "error": f"{type(error).__name__}: {error}", "parsed": None}

    summary = document.summary()
    return {
        **base,
        "status": "已解析",
        "strategy": document.strategy,
        "doc_type": document.doc_type,
        "parser": document.parser,
        "encoding": document.encoding,
        "page_count": document.page_count,
        "line_total": document.line_total,
        "element_total": summary["element_total"],
        "counts_by_kind": summary["counts_by_kind"],
        "outline": summary["outline"],
        "preview": summary["preview"],
        "outline_entries": summary["outline_entries"],
        "warnings": summary["warnings"],
        "error": None,
        "parsed": {"at": _now(), "step": 2},
    }


@bp.get("/documents")
def list_documents():
    items = load_registry()
    if request.args.get("kb_id") is not None:
        ids = set(knowledge.resolve_scope({"kb_id": request.args["kb_id"]}))
        items = [item for item in items if item["doc_id"] in ids]
    return jsonify(
        {
            "count": len(items),
            "items": items,
            "supported_suffixes": SUPPORTED_SUFFIXES,
            "step": 2,
        }
    )


@bp.get("/documents/<doc_id>")
def document_detail(doc_id: str):
    """文档详情：解析结构 + 前若干块，供知识库页的块预览使用。"""
    record = next((item for item in load_registry() if item["doc_id"] == doc_id), None)
    if record is None:
        return jsonify({"error": "文档不存在"}), 404

    path = settings.server.upload_dir / record["stored_name"]
    if not path.exists():
        return jsonify({"error": "文件已丢失，请重新上传"}), 410

    try:
        document = parse_file(path, doc_id, record["name"])
    except Exception as error:  # noqa: BLE001
        return jsonify({"error": f"解析失败：{error}"}), 500

    return jsonify(
        {
            "document": record,
            "summary": document.summary(),
            "elements": _preview_elements(document),
        }
    )


@bp.post("/documents")
def upload_document():
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return jsonify({"error": "缺少 file 字段"}), 400

    suffix = Path(uploaded.filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return jsonify({"error": f"暂不支持的文件类型：{suffix or '未知'}"}), 400

    raw = uploaded.read()
    digest = hashlib.sha1(raw).hexdigest()[:12]
    kb_id = request.form.get("kb_id") or knowledge.UNCLASSIFIED
    knowledge.require_label(kb_id, destination=True)
    with knowledge.metadata_lock:
        existing = next((item for item in load_registry() if item["doc_id"] == digest), None)
        if existing is not None:
            return jsonify({"document": existing, "duplicate": True,
                            "notice": "该文档已存在，已保留原标签与向量；可使用移动操作更改分类"}), 200
    stored_name = f"{digest}{suffix}"
    target = settings.server.upload_dir / stored_name
    target.write_bytes(raw)

    base = {
        "kb_id": kb_id,
        "doc_id": digest,
        "name": uploaded.filename,
        "stored_name": stored_name,
        "suffix": suffix,
        "size": len(raw),
        "size_human": _human_size(len(raw)),
        "chunks": 0,
        "uploaded_at": _now(),
        "step": 2,
        "strategy": resolve_strategy(""),
    }

    record = _parse_and_record(target, digest, uploaded.filename, base)

    with knowledge.metadata_lock:
        knowledge.require_label(kb_id, destination=True)
        items = load_registry()
        existing = next((item for item in items if item["doc_id"] == digest), None)
        if existing is not None:
            return jsonify({"document": existing, "duplicate": True, "notice": "文档已存在，保留原标签"}), 200
        save_registry([*items, record])

    status = 201 if record["status"] != "解析失败" else 422
    return jsonify({"document": record}), status


@bp.post("/documents/preview")
def preview_document():
    """当场解析一份文件看结构，不写注册表也不留文件。"""
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
        payload = document.summary(sample=5)
        payload["elements"] = _preview_elements(document, limit=20)
        return jsonify({"summary": payload})
    except Exception as error:  # noqa: BLE001
        return jsonify({"error": f"解析失败：{error}"}), 422
    finally:
        temp_path.unlink(missing_ok=True)


@bp.delete("/documents/<doc_id>")
def delete_document(doc_id: str):
    with knowledge.metadata_lock:
        items = load_registry()
        removed = next((item for item in items if item["doc_id"] == doc_id), None)
        if removed is None:
            return jsonify({"error": "文档不存在"}), 404
        removed["deletion_pending"] = True
        save_registry(items)
        try:
            from ..retrieval import indexer
            indexer.remove_document(doc_id)
            (settings.server.upload_dir / removed["stored_name"]).unlink(missing_ok=True)
        except Exception as error:
            current_app.logger.warning("清理文档失败：%s", error)
            return jsonify({"error": "文档清理未完成，已从检索范围排除；请点击删除重试", "cleanup_pending": True}), 503
        save_registry([item for item in load_registry() if item["doc_id"] != doc_id])
        return jsonify({"deleted": doc_id})


@bp.patch("/documents/<doc_id>")
def move_document(doc_id: str):
    kb_id = (request.get_json(silent=True) or {}).get("kb_id")
    with knowledge.metadata_lock:
        knowledge.require_label(kb_id, destination=True)
        if not update_document_fields(doc_id, kb_id=kb_id):
            return jsonify({"error": "文档不存在"}), 404
        record = next(item for item in load_registry() if item["doc_id"] == doc_id)
        return jsonify({"document": record})
