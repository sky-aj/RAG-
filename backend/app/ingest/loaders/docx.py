"""Word：按正文段落、样式、表格走。

python-docx 能拿到段落样式名（Heading 1 / 标题 1），
中文版 Word 和英文版样式名不同，所以两种前缀都要认。
表格单独抽成 Element，保留二维结构，步骤 3 可以整表切或按行组切。
"""
from __future__ import annotations

from pathlib import Path

from docx import Document as DocxDocument

from ..types import Element, ElementKind, ParsedDocument
from .base import BaseLoader

HEADING_PREFIXES = ("heading", "标题", "title")
LIST_STYLES = ("list", "列表", "bullet")


def _heading_level(style_name: str) -> int:
    """从样式名里取标题级别：Heading 2 / 标题 2 / Title。"""
    normalized = (style_name or "").strip().lower()
    if not normalized:
        return 0
    if normalized.startswith(("title", "标题")) and not normalized[-1].isdigit():
        return 1
    for prefix in HEADING_PREFIXES:
        if normalized.startswith(prefix):
            tail = normalized[len(prefix):].strip()
            if tail.isdigit():
                return int(tail)
            for char in tail:
                if char.isdigit():
                    return int(char)
            return 1
    return 0


class DocxLoader(BaseLoader):
    name = "docx"
    doc_type = "docx"
    suffixes = (".docx", ".doc")

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        document = DocxDocument(str(path))
        elements: list[Element] = []
        warnings: list[str] = []

        virtual_line = 0
        # 按 body 的原始顺序遍历，这样段落和表格的交错关系才是对的
        for block in _iter_blocks(document):
            if block["type"] == "paragraph":
                paragraph = block["obj"]
                text = (paragraph.text or "").strip()
                if not text:
                    virtual_line += 1
                    continue
                style_name = ""
                try:
                    style_name = paragraph.style.name or ""
                except Exception:  # noqa: BLE001 - 个别文档样式损坏
                    style_name = ""
                level = _heading_level(style_name)
                lines_taken = max(1, round(len(text) / 88) or 1)
                elements.append(
                    Element(
                        kind=(
                            ElementKind.HEADING
                            if level
                            else ElementKind.LIST_ITEM
                            if style_name.lower().startswith(LIST_STYLES)
                            else ElementKind.PARAGRAPH
                        ),
                        text=text,
                        line_start=virtual_line + 1,
                        line_end=virtual_line + lines_taken,
                        level=level,
                        section=style_name,
                    )
                )
                virtual_line += lines_taken
                continue

            table = block["obj"]
            rows = [[cell.text.strip() for cell in row.cells] for row in table.rows]
            rows = [row for row in rows if any(cell for cell in row)]
            if not rows:
                continue
            elements.append(
                Element(
                    kind=ElementKind.TABLE,
                    text="\n".join(" | ".join(row) for row in rows),
                    line_start=virtual_line + 1,
                    line_end=virtual_line + len(rows),
                    rows=rows,
                )
            )
            virtual_line += len(rows)

        if not elements:
            warnings.append("文档里没有解析出内容")

        return self._document(
            path, doc_id, display_name, elements=elements, warnings=warnings, page_count=0
        )


def _iter_blocks(document) -> list[dict]:
    """把段落和表格按文档顺序拉平。

    python-docx 没有直接给「顺序遍历」，只能靠 XML 的 body 子元素顺序，
    再用 rId 去关系表里找回对应的 Table 对象。
    """
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    blocks: list[dict] = []
    body = document.element.body
    for child in body.iterchildren():
        tag = child.tag.split("}")[-1]
        if tag == "p":
            blocks.append({"type": "paragraph", "obj": Paragraph(child, document)})
        elif tag == "tbl":
            blocks.append({"type": "table", "obj": Table(child, document)})
    return blocks
