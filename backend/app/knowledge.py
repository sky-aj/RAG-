"""一级知识库分类；文档 ID 是分类与现有向量库之间的唯一关联键。"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.exceptions import BadRequest, Conflict, NotFound

metadata_lock = threading.RLock()
ALL = "all"
UNCLASSIFIED = "unclassified"
BUILTINS = ({"id": ALL, "name": "全部文档", "builtin": True},
            {"id": UNCLASSIFIED, "name": "未分类", "builtin": True})


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        # Windows 的杀毒/索引程序可能短暂持有文件；保留原文件并有限重试。
        for attempt in range(4):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 3:
                    raise
                time.sleep(0.03 * (attempt + 1))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def registry_path() -> Path:
    from .api import documents
    return documents.settings.index.knowledge_base_path or documents.registry_path().with_name("knowledge_bases.json")


def load_labels() -> list[dict]:
    path = registry_path()
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(items, list) or any(
            not isinstance(item, dict) or not isinstance(item.get("id"), str)
            or not isinstance(item.get("name"), str) for item in items
        ):
            raise ValueError("标签记录格式无效")
        return items
    except (OSError, ValueError) as error:
        raise RuntimeError("知识库标签文件无法读取，请检查或恢复备份") from error


def require_label(kb_id: str, *, destination: bool = False) -> dict:
    if not isinstance(kb_id, str) or not kb_id:
        raise BadRequest("请指定有效的知识库标签")
    labels = [*BUILTINS, *load_labels()]
    item = next((label for label in labels if label["id"] == kb_id), None)
    if item is None:
        raise NotFound("知识库标签不存在，请重新选择")
    if destination and kb_id == ALL:
        raise BadRequest("全部文档是聚合视图，请选择实际标签")
    return dict(item)


def _name(raw, labels: list[dict], excluding: str = "") -> str:
    if not isinstance(raw, str) or not 1 <= len(raw.strip()) <= 40:
        raise BadRequest("标签名称需要 1–40 个字符")
    name = raw.strip()
    if any(item["id"] != excluding and item["name"].casefold() == name.casefold()
           for item in [*BUILTINS, *labels]):
        raise Conflict("标签名称已存在或属于保留名称")
    return name


def create_label(name) -> dict:
    with metadata_lock:
        labels = load_labels()
        item = {"id": "kb-" + uuid.uuid4().hex, "name": _name(name, labels),
                "created_at": datetime.now(timezone.utc).isoformat(), "builtin": False}
        atomic_json(registry_path(), [*labels, item])
        return item


def rename_label(kb_id: str, name) -> dict:
    with metadata_lock:
        if require_label(kb_id).get("builtin"):
            raise BadRequest("不能重命名系统分类")
        labels = load_labels()
        next_name = _name(name, labels, kb_id)
        item = next(label for label in labels if label["id"] == kb_id)
        item["name"] = next_name
        atomic_json(registry_path(), labels)
        return item


def delete_label(kb_id: str) -> None:
    from .api.documents import load_registry
    with metadata_lock:
        if require_label(kb_id).get("builtin"):
            raise BadRequest("不能删除系统分类")
        if any(item.get("kb_id", UNCLASSIFIED) == kb_id for item in load_registry()):
            raise Conflict("标签内还有文档，请先移动或删除文档")
        atomic_json(registry_path(), [item for item in load_labels() if item["id"] != kb_id])


def resolve_scope(payload: dict) -> list[str] | None:
    """旧接口空列表兼容全库；显式分类的空集合始终表示空范围。"""
    from .api.documents import load_registry
    raw = payload.get("doc_id")
    wanted = [str(item) for item in raw if item] if isinstance(raw, list) else ([str(raw)] if raw else [])
    kb_id = payload.get("kb_id")
    if kb_id is None:
        return wanted or None
    if not isinstance(kb_id, str):
        raise BadRequest("kb_id 必须是字符串")
    require_label(kb_id)
    return [item["doc_id"] for item in load_registry()
            if not item.get("deletion_pending")
            and (kb_id == ALL or item.get("kb_id", UNCLASSIFIED) == kb_id)
            and (not wanted or item["doc_id"] in wanted)]


def resolve_retrieval_scope(payload: dict) -> list[str] | None:
    """显式分类只检索已有向量的文档；未入库分类无需调用模型。"""
    doc_ids = resolve_scope(payload)
    if payload.get("kb_id") is not None and doc_ids:
        from .retrieval import vector_store
        indexed = {item["doc_id"] for item in vector_store.indexed_documents()}
        return [doc_id for doc_id in doc_ids if doc_id in indexed]
    return doc_ids


def list_labels() -> list[dict]:
    from .api.documents import load_registry
    from .retrieval.indexer import load_state
    records = [item for item in load_registry() if not item.get("deletion_pending")]
    indexed = load_state().get("documents", {})
    result = []
    for label in [*BUILTINS, *load_labels()]:
        docs = [item for item in records if label["id"] == ALL
                or item.get("kb_id", UNCLASSIFIED) == label["id"]]
        ids = [item["doc_id"] for item in docs]
        result.append({**label, "docs": len(ids), "docIds": ids,
                       "indexed": sum(indexed.get(doc_id, {}).get("chunks", 0) > 0 for doc_id in ids),
                       "chunks": sum(indexed.get(doc_id, {}).get("chunks", 0) for doc_id in ids)})
    return result
