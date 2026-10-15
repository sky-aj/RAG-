"""Markdown：按标题层级建树，标题路径写进元数据。

关键点是「一个块只产出一次」：
markdown-it 的 token 流里 heading_open 和它的 inline 内容在同一行，
早期实现两者都收，导致标题被重复算成一个 heading 加一个 paragraph。
这里按容器边界收块，inline 只有在非标题的容器里才会成为正文。

产出保留标题 / 段落 / 代码块 / 表格 / 列表 / 引用的边界，
步骤 3 的 markdown 策略直接按标题树切，标题路径（「指南 > 安装 > Linux」）
会跟着每个 chunk 走，喂给模型时能补上上下文。
"""
from __future__ import annotations

from pathlib import Path

from markdown_it import MarkdownIt

from ..types import Element, ElementKind, ParsedDocument
from .base import BaseLoader
from .text_code import read_text, split_lines

CODE_TOKENS = {"fence", "code_block"}
# 这些容器关闭时，把攒着的行落成一个块
FLUSH_ON = {"paragraph_close", "blockquote_close", "paragraph_open", "blockquote_open", "list_item_open"}


def _span(token_map) -> tuple[int, int]:
    """markdown-it 的 map 是 0 起、左闭右开，转成 1 起闭区间。"""
    if not token_map:
        return 1, 1
    return token_map[0] + 1, max(token_map[0] + 1, token_map[1])


class MarkdownLoader(BaseLoader):
    name = "markdown"
    doc_type = "markdown"
    suffixes = (".md", ".markdown")

    def __init__(self) -> None:
        # 关键：commonmark 预设不含表格，管道表格会被整个丢掉，
        # 所以显式打开 table 规则（GFM 规格）。不启用 linkify，避免额外依赖。
        self.md = MarkdownIt("commonmark", {"html": False}).enable("table")

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        raw, encoding = read_text(path)
        lines = split_lines(raw)
        tokens = self.md.parse(raw)

        elements: list[Element] = []
        warnings: list[str] = []
        pending: list[tuple[int, int, str]] = []
        quote_depth = 0
        list_depth = 0

        def flush() -> None:
            if not pending:
                return
            start = min(item[0] for item in pending)
            end = max(item[1] for item in pending)
            text = "\n".join(item[2] for item in pending).strip()
            pending.clear()
            if not text:
                return
            if quote_depth:
                kind = ElementKind.QUOTE
            elif list_depth:
                kind = ElementKind.LIST_ITEM
            else:
                kind = ElementKind.PARAGRAPH
            elements.append(Element(kind=kind, text=text, line_start=start, line_end=end))

        index = 0
        while index < len(tokens):
            token = tokens[index]
            token_type = token.type

            if token_type == "heading_open":
                flush()
                level = int(token.tag[1]) if token.tag[:1] == "h" and token.tag[1:].isdigit() else 1
                start, end = _span(token.map)
                inline = tokens[index + 1] if index + 1 < len(tokens) else None
                title = (inline.content if inline is not None else "").strip()
                if title:
                    elements.append(
                        Element(
                            kind=ElementKind.HEADING,
                            text=title,
                            line_start=start,
                            line_end=end,
                            level=level,
                        )
                    )
                # heading_open 后面固定跟 inline + heading_close，整段跳过，
                # 否则标题会被再当成正文收一次（早期就是踩了这个坑）
                index += 3
                continue

            if token_type in CODE_TOKENS:
                flush()
                start, end = _span(token.map)
                language = (token.info or "").strip().split(" ")[0]
                elements.append(
                    Element(
                        kind=ElementKind.CODE,
                        text=token.content.rstrip("\n"),
                        line_start=start,
                        line_end=end,
                        language=language,
                    )
                )
                index += 1
                continue

            if token_type == "table_open":
                flush()
                start, _ = _span(token.map)
                rows: list[list[str]] = []
                current_row: list[str] = []
                cursor = index
                end = start
                while cursor < len(tokens) and tokens[cursor].type != "table_close":
                    current = tokens[cursor]
                    if current.type == "tr_open":
                        current_row = []
                    elif current.type == "inline":
                        current_row.append(current.content.strip())
                    elif current.type == "tr_close":
                        rows.append(current_row)
                    # table_close 的 map 是空的，所以逐 token 推进右边界
                    if current.map:
                        end = max(end, _span(current.map)[1])
                    cursor += 1
                elements.append(
                    Element(
                        kind=ElementKind.TABLE,
                        text="\n".join(" | ".join(cell for cell in row) for row in rows),
                        line_start=start,
                        line_end=end,
                        rows=rows,
                    )
                )
                index = cursor + 1
                continue

            if token_type == "blockquote_open":
                flush()
                quote_depth += 1
            elif token_type == "blockquote_close":
                flush()
                quote_depth = max(0, quote_depth - 1)
            elif token_type in {"bullet_list_open", "ordered_list_open"}:
                flush()
                list_depth += 1
            elif token_type in {"bullet_list_close", "ordered_list_close"}:
                flush()
                list_depth = max(0, list_depth - 1)
            elif token_type in FLUSH_ON:
                flush()
            elif token_type == "inline":
                # 只有在正文容器里的 inline 才算内容，标题的 inline 已经处理过
                start, end = _span(token.map)
                if token.content.strip():
                    pending.append((start, end, token.content.strip()))

            index += 1

        flush()

        if not elements:
            warnings.append("没有解析出结构，已按纯文本兜底")
            from .text_code import build_elements_from_lines

            elements = build_elements_from_lines(lines)

        return self._document(
            path, doc_id, display_name, elements=elements, encoding=encoding, warnings=warnings
        )
