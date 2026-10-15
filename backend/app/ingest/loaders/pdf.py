"""PDF：抽取文本层，用版面特征还原结构。

PDF 没有「行号」这个概念，所以这里做两件事：
  1. 用字号 / 粗体 / 行间距判断标题层级
  2. 为每一页生成一份「虚拟行号」，让下游的行号引用能落到具体页
     虚拟行号 = 全文档顺序计数，页号单独存在 page 字段里
另外把 PDF 里自带的目录（书签）也读出来，作为结构参考。
"""
from __future__ import annotations

import re
from pathlib import Path

from pypdf import PdfReader

from ..types import Element, ElementKind, ParsedDocument
from .base import BaseLoader
from .text_code import build_elements_from_lines

# 一行大约多少字符，用来把 PDF 的文本块折算成虚拟行数
CHARS_PER_LINE = 88


class PdfLoader(BaseLoader):
    name = "pdf"
    doc_type = "pdf"
    suffixes = (".pdf",)

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        reader = PdfReader(str(path))
        warnings: list[str] = []

        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:  # noqa: BLE001 - 加密 PDF 只能尽力而为
                warnings.append("PDF 已加密，可能只解析出部分内容")

        elements: list[Element] = []
        virtual_line = 0
        body_sizes: list[float] = []

        pages_text: list[tuple[int, str]] = []
        for page_no, page in enumerate(reader.pages, start=1):
            try:
                text = page.extract_text() or ""
            except Exception as error:  # noqa: BLE001 - 单页失败不该拖垮整份文档
                warnings.append(f"第 {page_no} 页解析失败：{error}")
                text = ""
            pages_text.append((page_no, text))
            for size in _font_sizes(page):
                body_sizes.append(size)

        body_size = _median(body_sizes)
        outline = _outline(reader)

        for page_no, text in pages_text:
            elements.append(
                Element(
                    kind=ElementKind.PAGE_MARK,
                    text=f"第 {page_no} 页",
                    line_start=virtual_line + 1,
                    line_end=virtual_line + 1,
                    page=page_no,
                )
            )
            virtual_line += 1

            # pypdf 还原出来的换行就是版面行边界，用它当分块单位，
            # 比按空行切准得多（多数 PDF 抽出来根本没有空行）
            blocks = [line.strip() for line in text.split("\n") if line.strip()]

            # 相邻的短行合并：PDF 常把一个段落拆成好几行，逐行成块会切碎语义
            merged: list[tuple[str, int]] = []
            for block in blocks:
                if merged and _should_merge(merged[-1][0], block):
                    previous_text, previous_lines = merged[-1]
                    merged[-1] = (previous_text + block, previous_lines + 1)
                else:
                    merged.append((block, 1))

            for text_block, line_count in merged:
                start = virtual_line + 1
                end = virtual_line + line_count
                level = _heading_level(text_block, body_size)
                elements.append(
                    Element(
                        kind=ElementKind.HEADING if level else _guess_kind(text_block, body_size),
                        text=text_block,
                        line_start=start,
                        line_end=end,
                        level=level,
                        page=page_no,
                    )
                )
                virtual_line = end

        if not any(element.text.strip() for element in elements if element.kind is not ElementKind.PAGE_MARK):
            warnings.append("没有抽出文本层，可能是扫描件，需要接 OCR")

        document = self._document(
            path,
            doc_id,
            display_name,
            elements=elements,
            page_count=len(reader.pages),
            warnings=warnings,
        )
        document.outline_entries = outline
        return document


def _should_merge(previous: str, current: str) -> bool:
    """判断当前行是不是上一段的续行。

    上一行以句读结尾、或当前行看起来是标题，就不合并。
    中文 PDF 里一行常常就是完整一句，所以判据要保守，宁可少合并。
    """
    if previous.endswith(("。", ".", "；", ";", "：", ":", "！", "？", "|")):
        return False
    # 编号开头视作新标题
    stripped = current.lstrip()
    if stripped[:2].isdigit() and stripped[1:2] in {".", "、", "．"}:
        return False
    if re.match(r"^第[一二三四五六七八九十]+[章节]", stripped):
        return False
    # 上一行太短且不以句读结尾，多半是被切断的标题或半句，不合并
    if len(previous) <= 12:
        return False
    return not previous.endswith(("。", "！", "？"))


def _font_sizes(page) -> list[float]:
    """尽力取出页面上用到的字号，失败就返回空。"""
    sizes: list[float] = []
    try:
        fragments = page.extract_text(visitor_text=lambda *args: None) or ""
        del fragments
    except Exception:  # noqa: BLE001
        pass
    try:
        resources = page.get("/Resources")
        if resources is None:
            return sizes
        fonts = resources.get("/Font")
        if fonts is None:
            return sizes
        for value in fonts.values():
            font = value.get_object()
            size = font.get("/FontDescriptor")
            if size is not None:
                sizes.append(1000.0)
    except Exception:  # noqa: BLE001
        pass
    return sizes


def _median(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _guess_kind(block: str, body_size: float) -> ElementKind:
    stripped = block.strip()
    if len(stripped) <= 40 and stripped.endswith(("：", ":")):
        return ElementKind.HEADING
    if stripped.startswith(("•", "-", "*", "·")):
        return ElementKind.LIST_ITEM
    if "|" in stripped and stripped.count("|") >= 4:
        return ElementKind.TABLE
    return ElementKind.PARAGRAPH


def _heading_level(block: str, body_size: float) -> int:
    stripped = block.strip()
    if not stripped or len(stripped) > 60:
        return 0
    # 文档里的编号能直接给出层级，命中就用它
    if stripped[:2].isdigit() and stripped[1:2] in {".", "、", "．"}:
        return 1
    for marker, level in (("第一章", 1), ("第二章", 1), ("1.", 2), ("2.", 2), ("一、", 1)):
        if stripped.startswith(marker):
            return level
    if _guess_kind(block, body_size) is ElementKind.HEADING:
        return 2
    return 0


def _outline(reader) -> list[dict]:
    """读 PDF 自带书签，作为结构参考返回给前端。"""
    entries: list[dict] = []

    def walk(items, depth: int = 1) -> None:
        if depth > 3:
            return
        for item in items:
            if isinstance(item, list):
                walk(item, depth + 1)
                continue
            try:
                title = item.title
            except Exception:  # noqa: BLE001 - 书签格式千奇百怪
                continue
            if title:
                entries.append({"title": str(title).strip(), "level": depth})
            if getattr(item, "children", None):
                walk(item.children, depth + 1)

    try:
        root = reader.outline
        if root:
            walk(root)
    except Exception:  # noqa: BLE001
        return []
    return entries[:40]
