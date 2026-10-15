"""段落策略：小说 / 长文 / 无结构纯文本。

按空行切段（解析层已经把连续行并成了 PARAGRAPH 元素），
再把相邻的短段合并到目标大小，超长段按句子边界拆。
不做任何正则硬切，段落本身是语义单位。
"""
from __future__ import annotations

from ..types import ElementKind, ParsedDocument
from .base import ChunkingStrategy, estimate_tokens
from .types import Chunk

# 合并时允许轻微超过目标，避免为了一点点尾数把段切开
SOFT_RATIO = 1.15


class ParagraphStrategy(ChunkingStrategy):
    name = "paragraph"
    handles = ("text", "novel")

    def split(self, document: ParsedDocument) -> tuple[list[Chunk], list[str], dict]:
        notes: list[str] = []
        chunks: list[Chunk] = []

        pending: list[str] = []
        pending_tokens = 0
        start_line = 0
        end_line = 0
        heading = ""
        # 当前待合并块起点的标题，合并后只保留起点的上下文
        block_heading = ""
        merged_segments = 0
        split_segments = 0

        def flush() -> None:
            nonlocal pending, pending_tokens, start_line, end_line, block_heading, merged_segments, split_segments
            if not pending:
                return
            text = "\n\n".join(pending).strip()
            pieces = self.split_long_text(text)
            if len(pieces) > 1:
                split_segments += 1
            for piece in pieces:
                chunks.append(
                    self.make_chunk(
                        document,
                        len(chunks),
                        piece,
                        line_start=start_line,
                        line_end=end_line,
                        kind="paragraph",
                        section=block_heading,
                        heading_path=[block_heading] if block_heading else [],
                    )
                )
            if len(pending) > 1:
                merged_segments += 1
            pending = []
            pending_tokens = 0

        for element in document.elements:
            if element.kind is ElementKind.HEADING:
                flush()
                heading = element.text.strip()
                continue
            if element.kind in {ElementKind.PAGE_MARK, ElementKind.SHEET_MARK}:
                continue

            text = element.text.strip()
            if not text:
                continue

            size = estimate_tokens(text)
            # 超长单段直接单独成块，不要拖着一堆短段一起爆掉
            if size > self.maximum:
                flush()
                pending = [text]
                pending_tokens = size
                start_line, end_line = element.line_start, element.line_end
                flush()
                continue

            if pending and pending_tokens + size > self.target * SOFT_RATIO:
                flush()

            if not pending:
                # 新块开始：记下起点行号和当前标题，作为本块的上下文
                start_line = element.line_start
                block_heading = heading
            pending.append(text)
            pending_tokens += size
            end_line = element.line_end

        flush()

        if merged_segments:
            notes.append(f"合并了 {merged_segments} 组相邻短段，避免碎片化")
        if split_segments:
            notes.append(f"{split_segments} 个段落超过 {self.maximum} token，已按句子边界拆分")

        stats = {
            "elements_used": sum(1 for e in document.elements if e.kind not in {ElementKind.PAGE_MARK, ElementKind.SHEET_MARK}),
            "merged_groups": merged_segments,
            "split_segments": split_segments,
        }
        return chunks, notes, stats
