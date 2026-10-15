"""硅基流动（SiliconFlow）向量化客户端。

为什么放在 retrieval 而不是 llm：这里只做「文本 -> 向量」这一件事，
属于检索链路；步骤 5 的对话生成才走 llm/。

为什么用远端 API 而不是本地推理：这台机器没有 GPU，bge-m3 本地跑要下
2 GB 权重、CPU 推理也慢；远端跑的是同一个模型，换来零下载与可并发。
接口是 OpenAI 兼容的 /v1/embeddings。

批量失败会自动二分重试：与其猜远端的条数 / token 限额，不如失败了就拆半，
拆到单条还失败才算真失败。
"""
from __future__ import annotations

import logging
import time
from typing import Sequence

import httpx

from ..config import settings

logger = logging.getLogger(__name__)

# 远端限流、网关抖动时值得重试的状态码
_RETRY_STATUS = {429, 500, 502, 503, 504}


class SiliconFlowError(RuntimeError):
    """调用硅基流动失败。"""


def _endpoint(path: str) -> str:
    return settings.siliconflow.base_url.rstrip("/") + path


def _headers() -> dict[str, str]:
    if not settings.siliconflow.configured:
        raise SiliconFlowError("SILICONFLOW_API_KEY 未配置，检查 backend/.env")
    return {
        "Authorization": "Bearer " + settings.siliconflow.api_key,
        "Content-Type": "application/json",
    }


def _post(path: str, payload: dict) -> dict:
    """带退避重试的 POST。"""
    url = _endpoint(path)
    headers = _headers()
    attempts = max(1, settings.embedding.max_retries)
    last_error = ""

    for attempt in range(attempts):
        try:
            with httpx.Client(timeout=settings.embedding.timeout) as client:
                response = client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as error:
            last_error = f"{type(error).__name__}: {error}"
        else:
            if response.status_code == 200:
                return response.json()
            last_error = f"HTTP {response.status_code}: {response.text[:300]}"
            if response.status_code not in _RETRY_STATUS:
                raise SiliconFlowError(last_error)

        if attempt + 1 < attempts:
            wait = 1.5 * (attempt + 1)
            logger.warning(
                "硅基流动请求失败（第 %d/%d 次），%.1f 秒后重试：%s",
                attempt + 1, attempts, wait, last_error,
            )
            time.sleep(wait)

    raise SiliconFlowError(f"重试 {attempts} 次仍失败：{last_error}")


def _embed_once(window: Sequence[str]) -> list[list[float]]:
    payload = {
        "model": settings.siliconflow.embedding_model,
        "input": list(window),
        "encoding_format": "float",
    }
    data = _post("/embeddings", payload)
    rows = data.get("data") or []
    if len(rows) != len(window):
        raise SiliconFlowError(f"返回条数不符：请求 {len(window)} 条，返回 {len(rows)} 条")
    rows.sort(key=lambda item: item.get("index", 0))
    return [list(row["embedding"]) for row in rows]


def _embed_window(window: Sequence[str]) -> list[list[float]]:
    """一批不行就拆两半，递归到单条。"""
    try:
        return _embed_once(window)
    except SiliconFlowError:
        if len(window) <= 1:
            raise
        half = len(window) // 2
        logger.warning("整批 %d 条嵌入失败，拆成 %d + %d 重试", len(window), half, len(window) - half)
        return _embed_window(window[:half]) + _embed_window(window[half:])


def embed_texts(texts: Sequence[str], batch_size: int | None = None) -> list[list[float]]:
    """批量向量化，顺序与入参一一对应。

    空文本换成空格：远端对空串的处理不一致，统一成非空更省心。
    """
    items = [text if text and text.strip() else " " for text in texts]
    if not items:
        return []

    size = max(1, batch_size or settings.embedding.batch_size)
    vectors: list[list[float]] = []
    for start in range(0, len(items), size):
        vectors.extend(_embed_window(items[start:start + size]))
    return vectors


def embed_query(text: str) -> list[float]:
    """单条查询向量。查询比文档短，单独包一层便于以后加查询前缀。"""
    vectors = embed_texts([text], batch_size=1)
    if not vectors:
        raise SiliconFlowError("查询向量为空")
    return vectors[0]


def describe() -> dict:
    """给 /api/health 用的现状描述。"""
    return {
        "backend": settings.embedding.backend,
        "provider": "siliconflow",
        "base_url": settings.siliconflow.base_url,
        "model": settings.siliconflow.embedding_model,
        "declared_dimension": settings.embedding.dimension,
        "batch_size": settings.embedding.batch_size,
        "configured": settings.siliconflow.configured,
    }
