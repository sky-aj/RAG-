"""入库编排：把已上传的文档切成 chunk、向量化、写进 Qdrant。

分开成三个函数的理由：每个阶段都能单独跑、单独看结果，
出问题时能立刻定位是切分、向量化还是写库坏了。
  build_chunks()   只切分，不联网
  embed_chunks()   只向量化，不写库
  index_chunks()   只写库，向量已经拿到

「全量 vs 增量」用 doc_id 粒度判断：某份文档在库里的块数与重新切分后不一致、
或者该文档根本不在库里，就重灌这份文档。删掉的文档不会自动清理，
避免误删——要清就显式调 DELETE /api/index 或按文档删。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from ..config import settings
from ..ingest import parse_file
from ..ingest.chunking import chunk_document
from ..ingest.chunking.types import Chunk
from . import embedder, vector_store

logger = logging.getLogger(__name__)

def state_path() -> Path:
    """每次读而不缓存，这样环境变量改了立刻生效（测试靠这个）。"""
    return settings.index.state_path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    path = state_path()
    if not path.exists():
        return {"documents": {}, "last_run": None}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"documents": {}, "last_run": None}


def save_state(state: dict) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------- 阶段 1：切分 ----------

def build_chunks(records: Sequence[dict], strategy: str | None = None) -> tuple[list[Chunk], list[dict]]:
    """按注册表切分文档。返回 (全部 chunk, 每份文档的切分摘要)。

    缺文件不抛异常，记进摘要里继续处理下一份——批量入库时最怕一份坏文件
    让整批停下。
    """
    chunks: list[Chunk] = []
    per_document: list[dict] = []

    for record in records:
        path = settings.server.upload_dir / record["stored_name"]
        if not path.exists():
            per_document.append({
                "doc_id": record["doc_id"],
                "doc_name": record["name"],
                "ok": False,
                "error": "文件已丢失，请重新上传",
            })
            continue

        try:
            document = parse_file(path, record["doc_id"], record["name"])
            report = chunk_document(document, strategy=strategy)
            if not report.chunks:
                raise ValueError("未生成可检索片段，请检查文档是否包含可提取文本（扫描件需先识别文字）")
        except Exception as error:  # noqa: BLE001 - 单份失败不影响整批
            logger.exception("切分失败：%s", record["name"])
            per_document.append({
                "doc_id": record["doc_id"],
                "doc_name": record["name"],
                "ok": False,
                "error": f"{type(error).__name__}: {error}",
            })
            continue

        chunks.extend(report.chunks)
        per_document.append({
            "doc_id": record["doc_id"],
            "doc_name": record["name"],
            "ok": True,
            "strategy": report.strategy,
            "chunks": len(report.chunks),
            "chars": sum(chunk.char_count for chunk in report.chunks),
        })

    return chunks, per_document


# ---------- 阶段 2：向量化 ----------

def embed_chunks(chunks: Sequence[Chunk]) -> list[list[float]]:
    """把 chunk 文本送进嵌入模型。

    送进模型的是「带定位信息的文本」而不是裸文本：标题路径、语言、函数签名
    本身就是很强的检索线索，丢掉它们会让召回率变差。
    """
    return embedder.embed_texts([chunk_embedding_text(chunk) for chunk in chunks])


def chunk_embedding_text(chunk: Chunk) -> str:
    """拼出实际送给嵌入模型的文本。"""
    lines: list[str] = []
    if chunk.heading_path:
        lines.append(" · ".join(chunk.heading_path))
    if chunk.symbol:
        lines.append(chunk.symbol)
    lines.append(chunk.text)
    return "\n".join(lines).strip()


# ---------- 阶段 3：写库 ----------

def index_chunks(chunks: Sequence[Chunk], replace_doc_ids: Sequence[str] = ()) -> dict:
    if not chunks:
        return {"written": 0, "documents": 0}
    vectors = embed_chunks(chunks)
    if len(vectors) != len(chunks) or not vectors or any(len(vector) != len(vectors[0]) for vector in vectors):
        raise vector_store.VectorStoreError("返回的向量数量或维度不一致，保留原索引")
    vector_store.ensure_collection(len(vectors[0]))
    for doc_id in replace_doc_ids:
        vector_store.delete_document(doc_id)
    result = vector_store.upsert_chunks(chunks, vectors)
    # 新块进来了，BM25 的内存索引就过期了，主动通知它重建
    from . import bm25

    bm25.invalidate()
    return result


# ---------- 对外：一次跑完 ----------

def index_documents(records: Sequence[dict], strategy: str | None = None, force: bool = False) -> dict:
    """把文档灌进向量库。

    force=False 时跳过「已经在库里且块数相同」的文档，省掉重复的嵌入开销。
    """
    if not records:
        return {"ok": True, "documents": [], "written": 0, "skipped": [], "mode": "none"}

    state = load_state()
    existing = {item["doc_id"]: item for item in vector_store.indexed_documents()}

    if force:
        pending = list(records)
        skipped: list[dict] = []
    else:
        pending = []
        skipped = []
        for record in records:
            in_store = existing.get(record["doc_id"])
            if not in_store:
                pending.append(record)
                continue
            skipped.append({
                "doc_id": record["doc_id"],
                "doc_name": record["name"],
                "reason": f"已在库中（{in_store['chunks']} 块）",
            })

    if not pending:
        return {
            "ok": True,
            "mode": "incremental",
            "documents": [],
            "written": 0,
            "skipped": skipped,
            "total_points": vector_store.count(),
        }

    chunks, per_document = build_chunks(pending, strategy=strategy)
    failed = [item for item in per_document if not item["ok"]]

    result = {"written": 0, "documents": 0}
    if chunks:
        result = index_chunks(chunks, replace_doc_ids=[item["doc_id"] for item in per_document if item["ok"]] if force else ())

    for item in per_document:
        if not item["ok"]:
            continue
        state["documents"][item["doc_id"]] = {
            "doc_name": item["doc_name"],
            "strategy": item.get("strategy"),
            "chunks": item.get("chunks", 0),
            "indexed_at": _now(),
        }
        # 把真实块数写回文档注册表，前端列表与统计直接读它。
        # 这里失败不应诩断整批入库，所以只记录。
        try:
            from ..api.documents import update_document_fields

            update_document_fields(item["doc_id"], chunks=item.get("chunks", 0))
        except Exception as error:  # noqa: BLE001 - 回写失败不影响向量库已写入
            logger.warning("回写块数失败（%s）：%s", item["doc_name"], error)
    for item in failed:
        state["documents"].pop(item["doc_id"], None)
    state["last_run"] = _now()
    save_state(state)

    return {
        "ok": not failed,
        "mode": "forced" if force else "incremental",
        "documents": per_document,
        "written": result["written"],
        "skipped": skipped,
        "failed": failed,
        "total_points": vector_store.count(),
        "chunk_total": len(chunks),
    }


def remove_document(doc_id: str) -> int:
    """文档被删除时同步清掉它的块。"""
    removed = vector_store.delete_document(doc_id)
    if removed:
        from . import bm25

        bm25.invalidate()
    state = load_state()
    if state["documents"].pop(doc_id, None) is not None:
        save_state(state)
    from ..api.documents import update_document_fields
    update_document_fields(doc_id, chunks=0)
    return removed


def reset_index() -> dict:
    vector_store.reset()
    from . import bm25

    bm25.invalidate()
    save_state({"documents": {}, "last_run": _now()})
    from ..api.documents import load_registry, update_document_fields
    for record in load_registry():
        update_document_fields(record["doc_id"], chunks=0)
    return {"ok": True, "total_points": vector_store.count()}


def status() -> dict:
    """给 /api/index 的 GET 和健康检查用。"""
    state = load_state()
    store = vector_store.info()
    return {
        "store": store,
        "indexed": sorted(((doc_id, item) for doc_id, item in state["documents"].items()
                           if item.get("chunks", 0) > 0), key=lambda item: item[1].get("doc_name", "")),
        "indexed_count": sum(item.get("chunks", 0) > 0 for item in state["documents"].values()),
        "last_run": state.get("last_run"),
    }


def search_semantic(query: str, top_n: int | None = None, doc_ids: Iterable[str] | None = None) -> dict:
    """步骤 4 的验收口：纯语义检索。混合检索在步骤 5。"""
    limit = top_n or settings.retrieval.vector_top_n
    if doc_ids is not None:
        doc_ids = list(doc_ids)
    hits = [] if doc_ids == [] else vector_store.search(embedder.embed_query(query), top_n=limit, doc_ids=doc_ids)
    threshold = settings.retrieval.similarity_threshold
    kept = [hit for hit in hits if hit["score"] >= threshold]
    return {
        "query": query,
        "requested_top_n": limit,
        "returned": len(hits),
        "kept": len(kept),
        "threshold": threshold,
        "hits": kept,
    }
