"""Qdrant 向量库封装。

两种模式共用一个接口：
  embedded  本地文件库（data/qdrant），免 Docker，适合单机开发
  server    连真实的 Qdrant 服务，改 .env 的 QDRANT_MODE=server 即可

embedded 模式一次只能开一个客户端（文件锁），所以这里做成进程内单例，
不要在每个请求里新建 QdrantClient，那样第二个请求就会报锁冲突。

payload 存完整的 chunk 元数据：步骤 5 拼上下文时只靠 payload 就够，
不用回头再解析一遍原文件。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any, Iterable, Sequence

from ..config import settings
from ..ingest.chunking.types import Chunk

logger = logging.getLogger(__name__)

_client = None
_client_dimension: int | None = None


class VectorStoreError(RuntimeError):
    """向量库操作失败。"""


def point_id_for(chunk_id: str) -> int:
    """Qdrant 的 id 只接受 UUID 或无符号整数。

    chunk_id 是我们自己的 "doc-index-hash" 形式，所以映射成一个稳定的
    64 位整数：同一个 chunk_id 永远得到同一个 point id，重复入库就是覆盖。
    """
    digest = hashlib.sha1(chunk_id.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & 0x7FFF_FFFF_FFFF_FFFF


def _payload_for(chunk: Chunk) -> dict[str, Any]:
    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "doc_name": chunk.doc_name,
        "text": chunk.text,
        "line_start": chunk.line_start,
        "line_end": chunk.line_end,
        "page": chunk.page,
        "section": chunk.section,
        "heading_path": list(chunk.heading_path),
        "language": chunk.language,
        "symbol": chunk.symbol,
        "summary": chunk.summary,
        "kind": chunk.kind,
        "strategy": chunk.strategy,
        "index": chunk.index,
    }


def close_client() -> None:
    """显式关闭。embedded 模式持有文件锁，不关就不能删目录。"""
    global _client, _client_dimension

    if _client is None:
        return
    try:
        _client.close()
    except Exception as error:  # noqa: BLE001 - 关闭失败不值得把程序弄崩
        logger.warning("关闭 Qdrant 客户端失败：%s", error)
    finally:
        _client = None
        _client_dimension = None


def _close_on_exit() -> None:
    """进程退出时主动关闭。

    不这么做的话，QdrantClient 只能靠 __del__ 在解释器关闭阶段被回收，
    那时 sys.meta_path 已经是 None，会抛出一堆 ImportError 噪音。
    """
    import atexit

    atexit.register(close_client)


_close_on_exit()


def get_client(recreate: bool = False):
    """进程内单例。embedded 模式只能开一个，重复新建会报文件锁冲突。"""
    global _client, _client_dimension

    if _client is None or recreate:
        from qdrant_client import QdrantClient

        if recreate and _client is not None:
            close_client()

        if settings.qdrant.is_embedded:
            settings.qdrant.path.mkdir(parents=True, exist_ok=True)
            _client = QdrantClient(path=str(settings.qdrant.path))
            logger.info("Qdrant embedded 就绪：%s", settings.qdrant.path)
        else:
            _client = QdrantClient(
                url=settings.qdrant.url,
                api_key=settings.qdrant.api_key or None,
            )
            logger.info("Qdrant server 就绪：%s", settings.qdrant.url)
        _client_dimension = None

    return _client


def _points_api():
    """qdrant-client 1.12 起把 search/count/scroll 移到了 query_points / 新签名，
    但本地配置可能装到旧版。这里只在一处处理差异。
    实测 1.19 两套签名都能用，所以优先用新的。
    """
    client = get_client()
    return client


def collection_exists() -> bool:
    from qdrant_client.http.exceptions import UnexpectedResponse

    client = get_client()
    try:
        client.get_collection(settings.qdrant.collection)
        return True
    except (UnexpectedResponse, ValueError):
        return False
    except Exception as error:  # noqa: BLE001 - embedded 模式抛的是通用异常
        text = str(error)
        if "doesn't exist" in text or "Not found" in text or "not found" in text:
            return False
        raise


def ensure_collection(dimension: int, recreate: bool = False) -> None:
    """按向量维度建 collection，幂等。"""
    from qdrant_client.http import models as qmodels

    global _client_dimension

    client = get_client()
    exists = collection_exists()

    if exists and recreate:
        client.delete_collection(settings.qdrant.collection)
        exists = False
        _client_dimension = None

    if exists:
        if _client_dimension is None:
            info = client.get_collection(settings.qdrant.collection)
            _client_dimension = _extract_dimension(info)
        if _client_dimension and _client_dimension != dimension:
            raise VectorStoreError(
                f"collection 维度是 {_client_dimension}，当前模型是 {dimension}。"
                "换过嵌入模型的话请先清库（DELETE /api/index）。"
            )
        return

    client.create_collection(
        collection_name=settings.qdrant.collection,
        vectors_config=qmodels.VectorParams(size=dimension, distance=qmodels.Distance.COSINE),
    )
    _client_dimension = dimension

    # 按 doc_id 过滤删除 / 检索都要用，建个索引。
    # embedded 模式不支持负载索引（会打一条 UserWarning），它本来就是内存查询，
    # 没必要建，所以直接跳过。
    if settings.qdrant.is_embedded:
        return

    try:
        client.create_payload_index(
            collection_name=settings.qdrant.collection,
            field_name="doc_id",
            field_schema=qmodels.PayloadSchemaType.KEYWORD,
        )
    except Exception as error:  # noqa: BLE001 - 索引建不上不影响功能，只影响速度
        logger.warning("doc_id 负载索引创建失败（不影响检索）：%s", error)


def _extract_dimension(info) -> int | None:
    try:
        vectors = info.config.params.vectors
        return int(vectors.size)
    except AttributeError:
        return None


def upsert_chunks(chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> dict:
    """写库。chunk 与向量必须一一对应。"""
    from qdrant_client.http import models as qmodels

    if len(chunks) != len(vectors):
        raise VectorStoreError(f"chunk 数 {len(chunks)} 与向量数 {len(vectors)} 不一致")
    if not chunks:
        return {"written": 0, "documents": 0}

    dimension = len(vectors[0])
    ensure_collection(dimension)

    points: list[qmodels.PointStruct] = []
    for chunk, vector in zip(chunks, vectors):
        if len(vector) != dimension:
            raise VectorStoreError(f"{chunk.chunk_id} 的向量维度 {len(vector)} 与首条 {dimension} 不一致")
        points.append(
            qmodels.PointStruct(
                id=point_id_for(chunk.chunk_id),
                vector=list(vector),
                payload=_payload_for(chunk),
            )
        )

    client = get_client()
    # 分批，避免单请求过大
    written = 0
    for start in range(0, len(points), 128):
        batch = points[start:start + 128]
        client.upsert(collection_name=settings.qdrant.collection, points=batch)
        written += len(batch)

    doc_ids = {chunk.doc_id for chunk in chunks}
    return {"written": written, "documents": len(doc_ids)}


def _doc_filter(doc_ids: Iterable[str] | None):
    from qdrant_client.http import models as qmodels

    if doc_ids is None:
        return None
    ids = [doc_id for doc_id in doc_ids if doc_id]
    return qmodels.Filter(
        must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchAny(any=ids))]
    )


def search(vector: Sequence[float], top_n: int = 80, doc_ids: Iterable[str] | None = None) -> list[dict]:
    """纯语义检索。返回带分数的 payload，分数是余弦相似度（越大越像）。"""
    if doc_ids is not None:
        doc_ids = list(doc_ids)
        if not doc_ids:
            return []
    client = get_client()
    if not collection_exists():
        return []

    response = client.query_points(
        collection_name=settings.qdrant.collection,
        query=list(vector),
        limit=top_n,
        query_filter=_doc_filter(doc_ids),
        with_payload=True,
    )

    hits: list[dict] = []
    for point in response.points:
        payload = dict(point.payload or {})
        payload["score"] = float(point.score)
        payload["point_id"] = point.id
        hits.append(payload)
    return hits


def delete_document(doc_id: str) -> int:
    from qdrant_client.http import models as qmodels

    client = get_client()
    if not collection_exists():
        return 0

    before = count_document(doc_id)
    client.delete(
        collection_name=settings.qdrant.collection,
        points_selector=qmodels.FilterSelector(
            filter=qmodels.Filter(
                must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
            )
        ),
    )
    return before


def fetch_chunks(chunk_ids: Sequence[str]) -> list[dict]:
    """按 chunk_id 取回完整 payload（含正文）。

    引用卡片要能展开看原文，但检索结果里的 text 可能被截断或合并过，
    所以这里按 point_id 直接回库拿干净的那一份。
    """
    ids = [str(item) for item in chunk_ids if item]
    if not ids:
        return []
    client = get_client()
    if not collection_exists():
        return []

    wanted = {point_id_for(item): item for item in ids}
    records = client.retrieve(
        collection_name=settings.qdrant.collection,
        ids=list(wanted.keys()),
        with_payload=True,
    )

    found: list[dict] = []
    for record in records:
        payload = dict(record.payload or {})
        payload["point_id"] = record.id
        found.append(payload)
    # 按传入顺序返回，前端渲染引用时不用再排
    order = {chunk_id: index for index, chunk_id in enumerate(ids)}
    found.sort(key=lambda item: order.get(str(item.get("chunk_id")), 1 << 30))
    return found


def count() -> int:
    client = get_client()
    if not collection_exists():
        return 0
    return int(client.count(settings.qdrant.collection, exact=True).count)


def count_document(doc_id: str) -> int:
    from qdrant_client.http import models as qmodels

    client = get_client()
    if not collection_exists():
        return 0
    result = client.count(
        settings.qdrant.collection,
        count_filter=qmodels.Filter(
            must=[qmodels.FieldCondition(key="doc_id", match=qmodels.MatchValue(value=doc_id))]
        ),
        exact=True,
    )
    return int(result.count)


def indexed_documents() -> list[dict]:
    """库里已有哪些文档、各多少块。前端列表用。"""
    client = get_client()
    if not collection_exists():
        return []

    tally: dict[str, dict] = {}
    offset = None
    while True:
        records, offset = client.scroll(
            collection_name=settings.qdrant.collection,
            limit=256,
            offset=offset,
            with_payload=["doc_id", "doc_name"],
            with_vectors=False,
        )
        for record in records:
            payload = record.payload or {}
            doc_id = str(payload.get("doc_id", ""))
            if not doc_id:
                continue
            entry = tally.setdefault(doc_id, {"doc_id": doc_id, "doc_name": payload.get("doc_name", ""), "chunks": 0})
            entry["chunks"] += 1
        if offset is None:
            break

    return sorted(tally.values(), key=lambda item: item["doc_name"])


def reset() -> None:
    """明确清空整个 collection，下次入库重新建立维度。"""
    global _client_dimension
    client = get_client()
    if collection_exists():
        if settings.qdrant.is_embedded:
            from qdrant_client.http import models as qmodels
            # Windows 下 SDK 删除目录可能被尚未释放的 SQLite 句柄阻止，
            # 先从持久层删掉点，避免重建/重启时旧向量再次出现。
            client.delete(
                collection_name=settings.qdrant.collection,
                points_selector=qmodels.FilterSelector(filter=qmodels.Filter()),
            )
        client.delete_collection(settings.qdrant.collection)
        _client_dimension = None
        logger.info("collection %s 已清空", settings.qdrant.collection)


def info() -> dict:
    """给 /api/health 和 /api/index 的现状描述。"""
    from ..retrieval import embedder

    payload = {
        "mode": settings.qdrant.mode,
        "collection": settings.qdrant.collection,
        "location": str(settings.qdrant.path) if settings.qdrant.is_embedded else settings.qdrant.url,
        "ready": False,
        "points": 0,
        "documents": 0,
    }
    try:
        payload["ready"] = True
        payload["points"] = count()
        payload["documents"] = len(indexed_documents())
        client = get_client()
        if collection_exists():
            payload["dimension"] = _extract_dimension(client.get_collection(settings.qdrant.collection))
    except Exception as error:  # noqa: BLE001 - 探测接口不该把异常抛给调用方
        payload["error"] = f"{type(error).__name__}: {error}"
        payload["ready"] = False
    payload["embedder"] = embedder.describe()
    return payload
