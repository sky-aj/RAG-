"""Markdown 策略：按标题层级建树，叶子小节为一个块。

标题路径（「产品需求文档 v4.2 > 1. 限流策略 > 1.1 默认额度」）
写进每个 chunk，喂给模型时能补上「这段话属于哪一节」。
同一小节内的段落 / 表格 / 代码块会合并到目标大小，超长再拆。
"""
from __future__ import annotations

from ..types import Element, ElementKind, ParsedDocument
from .base import ChunkingStrategy, estimate_tokens, heading_path_for
from .types import Chunk

SOFT_RATIO = 1.15


class MarkdownStrategy(ChunkingStrategy):
    name = "markdown"
    handles = ("markdown",)

    def split(self, document: ParsedDocument) -> tuple[list[Chunk], list[str], dict]:
        notes: list[str] = []
        chunks: list[Chunk] = []

        stack: list[str] = []
        pending: list[Element] = []
        sections = 0
        merged_within_section = 0

        def flush() -> None:
            nonlocal pending, sections, merged_within_section
            if not pending:
                return
            sections += 1
            path = heading_path_for(_first_heading(pending), stack) if _first_heading(pending) else list(stack)
            title = stack[-1] if stack else ""

            # 小节内先按目标大小打包，再逐块产出
            group: list[Element] = []
            group_tokens = 0

            def emit(elements: list[Element]) -> None:
                text = "\n\n".join(element.text.strip() for element in elements if element.text.strip())
                if not text:
                    return
                pieces = self.split_long_text(text)
                for piece in pieces:
                    chunks.append(
                        self.make_chunk(
                            document,
                            len(chunks),
                            piece,
                            line_start=elements[0].line_start,
                            line_end=elements[-1].line_end,
                            page=elements[0].page,
                            kind=_dominant_kind(elements),
                            language=next((e.language for e in elements if e.language), ""),
                            section=title,
                            heading_path=path,
                            meta={"elements": len(elements), "kinds": sorted({e.kind.value for e in elements})},
                        )
                    )

            for element in pending:
                size = estimate_tokens(element.text)
                # 代码块和表格保持完整，不和别的元素混在一起
                if group and (
                    group_tokens + size > self.target * SOFT_RATIO
                    or element.kind in {ElementKind.CODE, ElementKind.TABLE}
                    or group[-1].kind in {ElementKind.CODE, ElementKind.TABLE}
                ):
                    if len(group) > 1:
                        merged_within_section += 1
                    emit(group)
                    group, group_tokens = [], 0
                group.append(element)
                group_tokens += size

            if group:
                if len(group) > 1:
                    merged_within_section += 1
                emit(group)

            pending = []

        for element in document.elements:
            if element.kind is ElementKind.PAGE_MARK:
                continue
            if element.kind is ElementKind.HEADING and element.level:
                # 新标题出现 -> 上一小节收尾
                flush()
                heading_path_for(element, stack)
                continue
            pending.append(element)

        flush()

        if merged_within_section:
            notes.append(f"小节内合并了 {merged_within_section} 组相邻元素")
        if sections:
            notes.append(f"按标题树切成 {sections} 个小节")

        deep = max((len(chunk.heading_path) for chunk in chunks), default=0)
        stats = {"sections": sections, "max_heading_depth": deep, "merged_groups": merged_within_section}
        return chunks, notes, stats


def _first_heading(elements: list[Element]) -> Element | None:
    return next((e for e in elements if e.kind is ElementKind.HEADING), None)


def _dominant_kind(elements: list[Element]) -> str:
    """一组元素的主类型，用于给 chunk 打标记。"""
    if len(elements) == 1:
        return elements[0].kind.value
    kinds = [element.kind.value for element in elements]
    for candidate in ("table", "code", "quote", "list_item"):
        if kinds.count(candidate) * 2 >= len(kinds):
            return candidate
    return "paragraph"
