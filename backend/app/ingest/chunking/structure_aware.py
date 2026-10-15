"""结构感知策略：PDF / Word / 表格。

这三类的共同点是「版面本身就是语义」：
  PDF      页边界要保留，跨页的段落要带上页号
  Word     标题层级给上下文，表格不能被拦腰切断
  表格     表头 + 成组数据行才是一个可读单位
所以这里不用固定窗口，而是沿解析层给的结构块走，
按「同页 + 同小节 + 到目标大小」合并，表格整表成块。
"""
from __future__ import annotations

from ..types import Element, ElementKind, ParsedDocument
from .base import ChunkingStrategy, estimate_tokens, heading_path_for
from .types import Chunk

SOFT_RATIO = 1.15
# 表格超过这个 token 数才按行组拆，否则整表保留
TABLE_KEEP_TOKENS = 900


class StructureAwareStrategy(ChunkingStrategy):
    name = "structure_aware"
    handles = ("pdf", "docx", "spreadsheet", "csv")

    def split(self, document: ParsedDocument) -> tuple[list[Chunk], list[str], dict]:
        notes: list[str] = []
        chunks: list[Chunk] = []

        stack: list[str] = []
        pending: list[Element] = []
        pending_tokens = 0
        tables_whole = 0
        tables_split = 0
        page_breaks = 0

        def emit(elements: list[Element]) -> None:
            nonlocal tables_whole, tables_split
            if not elements:
                return
            first = elements[0]
            header = "\n\n".join(element.text.strip() for element in elements if element.text.strip())
            if not header:
                return

            # 整组就是一个表格且不太大 -> 整表成块，保证表格可读
            if len(elements) == 1 and first.kind is ElementKind.TABLE:
                if estimate_tokens(header) <= TABLE_KEEP_TOKENS:
                    tables_whole += 1
                    chunks.append(
                        self._chunk(document, len(chunks), header, first, elements, stack, "table")
                    )
                    return
                tables_split += 1
                for piece in self.split_long_text(header):
                    chunks.append(
                        self._chunk(document, len(chunks), piece, first, elements, stack, "table")
                    )
                return

            for piece in self.split_long_text(header):
                kind = first.kind.value if len(elements) == 1 else _dominant_kind(elements)
                chunks.append(self._chunk(document, len(chunks), piece, first, elements, stack, kind))

        for element in document.elements:
            if element.kind is ElementKind.PAGE_MARK:
                # 页边界：把当前块收掉，避免跨页块的行号区间含糊
                if pending:
                    emit(pending)
                    pending, pending_tokens = [], 0
                    page_breaks += 1
                continue
            if element.kind is ElementKind.SHEET_MARK:
                if pending:
                    emit(pending)
                    pending, pending_tokens = [], 0
                stack = []
                continue
            if element.kind is ElementKind.HEADING and element.level:
                if pending:
                    emit(pending)
                    pending, pending_tokens = [], 0
                heading_path_for(element, stack)
                continue

            text = element.text.strip()
            if not text:
                continue

            size = estimate_tokens(text)
            # 表格不与其他内容混在一个块里
            is_table = element.kind is ElementKind.TABLE
            pending_has_table = bool(pending) and pending[-1].kind is ElementKind.TABLE

            if pending and (
                pending_tokens + size > self.target * SOFT_RATIO or is_table or pending_has_table
            ):
                emit(pending)
                pending, pending_tokens = [], 0

            pending.append(element)
            pending_tokens += size

        if pending:
            emit(pending)

        # 标题也可能是资料本身（例如获奖清单），不能把有文本的文档切成空库。
        # 常规文档仍沿用正文块与标题路径，仅在没有正文块时保留标题。
        if not chunks:
            stack = []
            for element in document.elements:
                if element.kind is ElementKind.HEADING and element.text.strip():
                    heading_path_for(element, stack)
                    emit([element])
            if chunks:
                notes.append("文档仅含标题，已保留标题文本作为检索片段")

        if page_breaks:
            notes.append(f"遇到 {page_breaks} 个页边界，块不跨页")
        if tables_whole:
            notes.append(f"{tables_whole} 个表格整表保留")
        if tables_split:
            notes.append(f"{tables_split} 个表格过大，按行组拆分")

        page_min = min((e.page for e in document.elements if e.page), default=None)
        stats = {
            "pages": document.page_count,
            "tables_whole": tables_whole,
            "tables_split": tables_split,
            "page_breaks": page_breaks,
            "starts_at_page": page_min,
        }
        return chunks, notes, stats

    def _chunk(
        self,
        document: ParsedDocument,
        index: int,
        text: str,
        first: Element,
        elements: list[Element],
        stack: list[str],
        kind: str,
    ) -> Chunk:
        return self.make_chunk(
            document,
            index,
            text,
            line_start=first.line_start,
            line_end=elements[-1].line_end,
            page=first.page,
            kind=kind,
            section=stack[-1] if stack else first.section,
            heading_path=list(stack),
            meta={
                "elements": len(elements),
                "kinds": sorted({element.kind.value for element in elements}),
            },
        )


def _dominant_kind(elements: list[Element]) -> str:
    kinds = [element.kind.value for element in elements]
    for candidate in ("table", "code", "quote", "list_item"):
        if kinds.count(candidate) * 2 >= len(kinds):
            return candidate
    return "paragraph"
