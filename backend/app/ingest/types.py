"""解析层的统一数据结构。

设计取舍：
  所有加载器都产出 ParsedDocument，内部是一串 Element。
  行号用 source_span（原文行号区间），切分与上下文拼装都靠它，
  这样回答里能标出「这段话在第几行」，而不是只给个文件名。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any


class ElementKind(str, Enum):
    HEADING = "heading"        # 标题
    PARAGRAPH = "paragraph"    # 段落正文
    CODE = "code"              # 代码块
    TABLE = "table"            # 表格
    LIST_ITEM = "list_item"    # 列表项
    QUOTE = "quote"            # 引用
    CAPTION = "caption"        # 图注 / 表注
    PAGE_MARK = "page_mark"    # PDF 的页边界（承载 page_no）
    SHEET_MARK = "sheet_mark"  # Excel 的工作表边界


@dataclass
class Element:
    """一个结构单元。这是解析层与切分层之间的唯一契约。"""

    kind: ElementKind
    text: str
    # 原文行号（1 起，闭区间）。PDF / DOCX 没有真实行号，用块序号代替
    line_start: int
    line_end: int
    # 标题层级，1 表示一级标题；非标题为 0
    level: int = 0
    # 语言标记：代码块、表格等
    language: str = ""
    # 表格的二维结构，list[list[str]]
    rows: list[list[str]] = field(default_factory=list)
    # 页号 / 工作表名等定位信息
    page: int | None = None
    section: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def line_count(self) -> int:
        return max(0, self.line_end - self.line_start + 1)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["kind"] = self.kind.value
        return data


@dataclass
class ParsedDocument:
    """一份解析完成的文档。"""

    doc_id: str
    name: str
    suffix: str
    parser: str                      # 用了哪个加载器
    doc_type: str                    # pdf / docx / markdown / text / csv / code ...
    elements: list[Element] = field(default_factory=list)
    encoding: str = ""
    page_count: int = 0
    warnings: list[str] = field(default_factory=list)
    # 步骤 3 会用哪条分块策略（由 loaders.resolve_strategy 决定）
    strategy: str = ""
    # PDF 自带书签，DOCX 可放样式目录，作为结构参考
    outline_entries: list[dict[str, Any]] = field(default_factory=list)

    def counts_by_kind(self) -> dict[str, int]:
        result: dict[str, int] = {}
        for element in self.elements:
            key = element.kind.value
            result[key] = result.get(key, 0) + 1
        return result

    @property
    def line_total(self) -> int:
        return max((element.line_end for element in self.elements), default=0)

    def outline(self, max_items: int = 14) -> list[dict[str, Any]]:
        """给前端看的结构摘要：先列标题，标题不够就用块顶上来。"""
        headings = [
            {
                "kind": element.kind.value,
                "level": element.level,
                "text": element.text[:80],
                "line_start": element.line_start,
                "line_end": element.line_end,
                "page": element.page,
            }
            for element in self.elements
            if element.kind is ElementKind.HEADING
        ]
        if len(headings) >= max_items:
            return headings[:max_items]

        picked = list(headings)
        for element in self.elements:
            if len(picked) >= max_items:
                break
            if element.kind is ElementKind.HEADING:
                continue
            picked.append(
                {
                    "kind": element.kind.value,
                    "level": 0,
                    "text": element.text[:80],
                    "line_start": element.line_start,
                    "line_end": element.line_end,
                    "page": element.page,
                }
            )
        picked.sort(key=lambda item: (item["line_start"], item["line_end"]))
        return picked

    def summary(self, sample: int = 3) -> dict[str, Any]:
        preview = []
        for element in self.elements:
            if len(preview) >= sample:
                break
            if not element.text.strip():
                continue
            preview.append(
                {
                    "kind": element.kind.value,
                    "line_start": element.line_start,
                    "line_end": element.line_end,
                    "text": element.text[:220],
                }
            )
        return {
            "doc_id": self.doc_id,
            "name": self.name,
            "suffix": self.suffix,
            "parser": self.parser,
            "doc_type": self.doc_type,
            "encoding": self.encoding,
            "page_count": self.page_count,
            "line_total": self.line_total,
            "element_total": len(self.elements),
            "counts_by_kind": self.counts_by_kind(),
            "strategy": self.strategy,
            "outline": self.outline(),
            "outline_entries": self.outline_entries,
            "preview": preview,
            "warnings": self.warnings,
        }
