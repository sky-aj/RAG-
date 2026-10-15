"""切分策略的公共能力：token 估算、句子边界、语义单位打包。

这里的核心是「按语义单位打包」而不是按字数硬切：
策略先把文档切成原子单位（段落 / 小节 / 函数），
再由 packer 按目标大小合并相邻单位，超长的单位才按句子边界拆。
所以任何一条策略都不会从句子中间切断。
"""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

from ...config import settings
from ..types import Element, ParsedDocument
from .types import Chunk, ChunkingReport

# 中文常用字 + 英文，粗略估算：CJK 每字约 1 token，英文约 4 字符 1 token
CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")
# 句子边界：中英文句读，保留标点
SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|(?<=[.])\s+(?=[A-Z\u4e00-\u9fff])")


def estimate_tokens(text: str) -> int:
    """估算 token 数。

    用轻量近似而不是真的加载 tokenizer：切分只需要相对大小正确，
    真正的 token 预算在步骤 5 由模型侧再精算一次。
    """
    if not text:
        return 0
    cjk = len(CJK_PATTERN.findall(text))
    other = len(text) - cjk
    return int(cjk + other / 3.5) + 1


def split_sentences(text: str) -> list[str]:
    """按句子边界切开，段落里没有句读时整段返回。"""
    parts = [part.strip() for part in SENTENCE_SPLIT.split(text) if part and part.strip()]
    return parts or [text.strip()]


def chunk_id_for(doc_id: str, index: int, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:8]
    return f"{doc_id}-{index:04d}-{digest}"


class ChunkingStrategy(ABC):
    """策略契约：输入解析结果，输出切分报告。"""

    name = "base"
    # 这个策略适合哪些 doc_type，router 用它分流
    handles: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.target = settings.chunking.target_tokens
        self.maximum = settings.chunking.max_tokens
        self.overlap = settings.chunking.overlap_tokens

    @abstractmethod
    def split(self, document: ParsedDocument) -> tuple[list[Chunk], list[str], dict]:
        """返回 (chunks, notes, stats)。"""
        raise NotImplementedError

    # ---------- 公共工具 ----------

    def make_chunk(
        self,
        document: ParsedDocument,
        index: int,
        text: str,
        **kwargs,
    ) -> Chunk:
        chunk = Chunk(
            chunk_id=chunk_id_for(document.doc_id, index, text),
            doc_id=document.doc_id,
            doc_name=document.name,
            text=text.strip(),
            line_start=kwargs.pop("line_start", 1),
            line_end=kwargs.pop("line_end", 1),
            strategy=self.name,
            index=index,
            **kwargs,
        )
        return chunk

    def split_long_text(self, text: str) -> list[str]:
        """超长单位按句子边界拆，并保留少量重叠，避免答案刚好卡在接缝上。"""
        if estimate_tokens(text) <= self.maximum:
            return [text]

        pieces: list[str] = []
        buffer: list[str] = []
        size = 0

        for sentence in split_sentences(text):
            sentence_size = estimate_tokens(sentence)
            # 单句就超上限：只能按字符切，这是最后手段
            if sentence_size > self.maximum:
                if buffer:
                    pieces.append("".join(buffer).strip())
                    buffer, size = [], 0
                step = self.maximum * 3  # 中文约 1 字 1 token
                for offset in range(0, len(sentence), step):
                    pieces.append(sentence[offset : offset + step].strip())
                continue

            if size + sentence_size > self.target and buffer:
                pieces.append("".join(buffer).strip())
                overlap_text = self._tail("".join(buffer), self.overlap)
                buffer = [overlap_text] if overlap_text else []
                size = estimate_tokens(overlap_text)

            buffer.append(sentence)
            size += sentence_size

        if buffer:
            pieces.append("".join(buffer).strip())

        return [piece for piece in pieces if piece]

    @staticmethod
    def _tail(text: str, tokens: int) -> str:
        if tokens <= 0 or not text:
            return ""
        return text[-tokens * 2 :]

    def finalize(
        self,
        document: ParsedDocument,
        chunks: list[Chunk],
        notes: list[str],
        stats: dict,
    ) -> ChunkingReport:
        """收尾：重建索引、去掉空块、统计。"""
        cleaned = [chunk for chunk in chunks if chunk.text.strip()]
        for index, chunk in enumerate(cleaned):
            chunk.index = index
            chunk.chunk_id = chunk_id_for(chunk.doc_id, index, chunk.text)
        return ChunkingReport(
            doc_id=document.doc_id,
            doc_name=document.name,
            strategy=self.name,
            chunks=cleaned,
            notes=notes,
            stats={
                "doc_type": document.doc_type,
                "target_tokens": self.target,
                "max_tokens": self.maximum,
                "overlap_tokens": self.overlap,
                **stats,
            },
        )


def heading_path_for(element: Element, stack: list[str]) -> list[str]:
    """维护标题栈，返回该元素所处的标题路径。"""
    if element.kind.value == "heading" and element.level:
        del stack[element.level - 1 :]
        while len(stack) < element.level - 1:
            stack.append("")
        stack.append(element.text.strip())
    return [part for part in stack if part]
