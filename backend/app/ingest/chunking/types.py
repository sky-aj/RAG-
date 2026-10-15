"""切分层的统一数据结构。

一个 Chunk 是最终要向量化、要喂给模型的单位，
所以它必须自带足够的定位信息：行号区间、来源文件、语言、函数签名、摘要。
步骤 5 拼上下文时只依赖这些字段，不再回头翻原文。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    doc_name: str
    text: str

    # 定位
    line_start: int
    line_end: int
    page: int | None = None
    section: str = ""
    heading_path: list[str] = field(default_factory=list)

    # 语义标记
    language: str = ""
    symbol: str = ""       # 函数签名 / 类签名
    summary: str = ""      # 一句话摘要，步骤 5 用 LLM 补，这里先给启发式结果
    kind: str = "paragraph"

    # 切分溯源：用的哪条策略、第几块、做了哪些处理
    strategy: str = "paragraph"
    index: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def char_count(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["char_count"] = self.char_count
        return data


@dataclass
class ChunkingReport:
    """一次切分的结果 + 可观测信息，前端直接展示。

    report 里刻意保留「为什么这么切」：策略名、命中规则、合并/拆分次数，
    调参时不用靠猜。
    """

    doc_id: str
    doc_name: str
    strategy: str
    chunks: list[Chunk] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def summary(self, preview: int = 12) -> dict[str, Any]:
        """给接口的完整摘要。

        字符统计与切分参数放在同一层（stats），不要再套一层，
        否则调用方要同时看两个地方才知道块多大、参数是多少。
        """
        sizes = [chunk.char_count for chunk in self.chunks] or [0]
        parameter_keys = {"target_tokens", "max_tokens", "overlap_tokens"}
        parameters = {key: self.stats[key] for key in parameter_keys if key in self.stats}
        details = {key: value for key, value in self.stats.items() if key not in parameter_keys}

        return {
            "doc_id": self.doc_id,
            "doc_name": self.doc_name,
            "strategy": self.strategy,
            "chunk_total": len(self.chunks),
            "notes": self.notes,
            "stats": {
                **parameters,
                "char_total": sum(sizes),
                "char_min": min(sizes),
                "char_max": max(sizes),
                "char_avg": round(sum(sizes) / len(sizes)),
            },
            "details": details,
            "chunks": [chunk.to_dict() for chunk in self.chunks[:preview]],
        }
