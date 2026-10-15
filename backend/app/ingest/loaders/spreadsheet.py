"""表格：CSV 与 Excel。

表格是典型的「结构决定语义」的文件：
表头和数据行的关系比行本身重要，所以这里把
表头单独存一份（header），每个数据行组也带上表头，
步骤 3 切分时就能把「列名: 值」写进片段，而不是丢一堆裸数字。
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from openpyxl import load_workbook

from ..types import Element, ElementKind, ParsedDocument
from .base import BaseLoader
from .text_code import ENCODINGS

MAX_ROWS_PER_ELEMENT = 40


def _sniff_rows(text: str) -> list[list[str]]:
    """先按逗号试一次，列数太少就退到制表符 / 分号。"""
    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    return [[cell.strip() for cell in row] for row in reader]


class CsvLoader(BaseLoader):
    name = "csv"
    doc_type = "csv"
    suffixes = (".csv",)

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        raw = path.read_bytes()
        text = ""
        encoding_used = "utf-8"
        for encoding in ENCODINGS:
            try:
                text = raw.decode(encoding)
                encoding_used = encoding
                break
            except UnicodeDecodeError:
                continue
        else:
            text = raw.decode("utf-8", errors="replace")
            encoding_used = "utf-8(replace)"

        rows = [row for row in _sniff_rows(text) if any(cell for cell in row)]
        elements: list[Element] = []
        warnings: list[str] = []

        if not rows:
            warnings.append("CSV 里没有数据")
            return self._document(
                path, doc_id, display_name, elements=elements, encoding=encoding_used, warnings=warnings
            )

        header = rows[0]
        body_rows = rows[1:]
        elements.append(
            Element(
                kind=ElementKind.HEADING,
                text="表头：" + " | ".join(header),
                line_start=1,
                line_end=1,
                level=1,
                meta={"header": header},
            )
        )

        # 每 MAX_ROWS_PER_ELEMENT 行一个 Element，避免整表变成一个巨型块
        for offset in range(0, len(body_rows), MAX_ROWS_PER_ELEMENT):
            group = body_rows[offset : offset + MAX_ROWS_PER_ELEMENT]
            start = offset + 2  # 第 1 行是表头
            end = start + len(group) - 1
            lines = []
            for index, row in enumerate(group, start=start):
                pairs = [
                    f"{header[i] if i < len(header) else f'列{i + 1}'}: {cell}"
                    for i, cell in enumerate(row)
                    if cell
                ]
                lines.append(f"[行 {index}] " + "；".join(pairs))
            elements.append(
                Element(
                    kind=ElementKind.TABLE,
                    text="\n".join(lines),
                    line_start=start,
                    line_end=end,
                    rows=[header, *group],
                    meta={"header": header, "row_offset": start},
                )
            )

        return self._document(
            path,
            doc_id,
            display_name,
            elements=elements,
            encoding=encoding_used,
            warnings=warnings,
        )


class ExcelLoader(BaseLoader):
    name = "spreadsheet"
    doc_type = "spreadsheet"
    suffixes = (".xlsx", ".xlsm")

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        workbook = load_workbook(str(path), read_only=True, data_only=True)
        elements: list[Element] = []
        warnings: list[str] = []
        virtual_line = 0

        for sheet in workbook.worksheets:
            virtual_line += 1
            elements.append(
                Element(
                    kind=ElementKind.SHEET_MARK,
                    text=f"工作表 {sheet.title}",
                    line_start=virtual_line,
                    line_end=virtual_line,
                    section=sheet.title,
                )
            )

            rows: list[list[str]] = []
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value).strip() for value in row]
                if any(cells):
                    rows.append(cells)

            if not rows:
                warnings.append(f"工作表 {sheet.title} 是空的")
                continue

            header = rows[0]
            elements.append(
                Element(
                    kind=ElementKind.HEADING,
                    text=f"{sheet.title} 表头：" + " | ".join(header),
                    line_start=virtual_line,
                    line_end=virtual_line,
                    level=2,
                    section=sheet.title,
                    meta={"header": header},
                )
            )

            body_rows = rows[1:]
            for offset in range(0, len(body_rows), MAX_ROWS_PER_ELEMENT):
                group = body_rows[offset : offset + MAX_ROWS_PER_ELEMENT]
                start = virtual_line + 1
                end = start + len(group)
                lines = []
                for index, row in enumerate(group, start=offset + 2):
                    pairs = [
                        f"{header[i] if i < len(header) else f'列{i + 1}'}: {cell}"
                        for i, cell in enumerate(row)
                        if cell
                    ]
                    lines.append(f"[行 {index}] " + "；".join(pairs))
                elements.append(
                    Element(
                        kind=ElementKind.TABLE,
                        text="\n".join(lines),
                        line_start=start,
                        line_end=end,
                        rows=[header, *group],
                        section=sheet.title,
                        meta={"header": header, "sheet": sheet.title, "row_offset": offset + 2},
                    )
                )
                virtual_line = end

        return self._document(
            path, doc_id, display_name, elements=elements, warnings=warnings,
            page_count=len(workbook.worksheets),
        )
