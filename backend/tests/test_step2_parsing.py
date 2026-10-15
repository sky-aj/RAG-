"""步骤 2 的解析测试：每种文件类型都要产出带行号的结构块。"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest import SUPPORTED_SUFFIXES, parse_file, resolve_strategy
from app.ingest.types import ElementKind

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


def parse(name: str):
    path = SAMPLES / name
    assert path.exists(), f"缺少样例文件 {name}"
    return parse_file(path, "test0001", name)


def test_supported_suffixes_cover_core_types():
    for suffix in (".pdf", ".docx", ".md", ".txt", ".csv", ".xlsx", ".py", ".ts", ".go"):
        assert suffix in SUPPORTED_SUFFIXES, suffix


def test_markdown_keeps_heading_levels():
    document = parse("产品需求文档.md")
    assert document.doc_type == "markdown"
    assert document.strategy == "markdown"

    headings = [element for element in document.elements if element.kind is ElementKind.HEADING]
    assert headings, "应当解析出标题"
    assert headings[0].level == 1
    assert any(element.level >= 2 for element in headings), "应当有多级标题"

    tables = [element for element in document.elements if element.kind is ElementKind.TABLE]
    assert tables and tables[0].rows, "表格应当保留二维结构"

    codes = [element for element in document.elements if element.kind is ElementKind.CODE]
    assert codes, "代码块应当被识别"
    assert codes[0].language == "python", "代码块应当带语言标记"


def test_markdown_line_numbers_are_real():
    path = SAMPLES / "产品需求文档.md"
    total_lines = len(path.read_text(encoding="utf-8").splitlines())
    document = parse("产品需求文档.md")

    # 行号必须落在真实文件范围内，且单调不减
    for element in document.elements:
        assert 1 <= element.line_start <= element.line_end <= total_lines + 1, element
    starts = [element.line_start for element in document.elements]
    assert starts == sorted(starts), "块的行号应当保持原文顺序"

    # 标题「## 1. 限流策略」在第 5 行，且行号应当与其文档位置严格一致
    source_lines = path.read_text(encoding="utf-8").splitlines()
    for element in document.elements:
        if element.kind is ElementKind.HEADING:
            actual = source_lines[element.line_start - 1]
            assert element.text in actual, f"行号对不上：第 {element.line_start} 行是 {actual!r}"

    heading = next(e for e in document.elements if e.text.startswith("1. 限流策略"))
    assert heading.line_start == 5, f"标题行号应当精确，实际 {heading.line_start}"
    assert heading.level == 2


def test_plain_text_builds_paragraphs():
    document = parse("运维笔记.txt")
    assert document.doc_type == "text"
    assert document.strategy == "paragraph"

    paragraphs = [element for element in document.elements if element.kind is ElementKind.PARAGRAPH]
    assert len(paragraphs) >= 4, "空行分段应当被识别"

    headings = [element for element in document.elements if element.kind is ElementKind.HEADING]
    assert headings, "纯文本里的章节标题应当被识别，而不是全平铺成段落"
    assert any("灰度发布" in element.text for element in headings)
    assert any("回滚" in element.text for element in headings)
    # 正文不能被误判成标题
    assert not any(element.text.startswith("先切 5%") for element in headings)


def test_code_file_is_single_code_block_with_language():
    document = parse("quota.py")
    assert document.doc_type == "code"
    assert document.strategy == "code"

    assert len(document.elements) == 1
    element = document.elements[0]
    assert element.kind is ElementKind.CODE
    assert element.language == "python"
    assert element.line_start == 1
    assert element.line_end == document.line_total
    assert "class SlidingWindowCounter" in element.text


def test_csv_builds_row_groups_with_header():
    document = parse("客服问答记录.csv")
    assert document.doc_type == "csv"
    assert document.strategy == "structure_aware"

    tables = [element for element in document.elements if element.kind is ElementKind.TABLE]
    assert tables, "应当有数据行组"

    header_meta = tables[0].meta.get("header")
    assert header_meta and "问题" in header_meta, "表头应当被单独记录"

    # 每一行都要带上列名，而不是一串裸值
    assert "问题:" in tables[0].text
    assert tables[0].line_start == 2, "数据行从第 2 行开始（第 1 行是表头）"


@pytest.fixture()
def scratch(tmp_path_factory):
    """沙箱里系统临时目录不可写，把测试产物放到项目内。"""
    root = Path(__file__).resolve().parent.parent / "data" / "tmp" / "tests"
    root.mkdir(parents=True, exist_ok=True)
    return root


def test_binary_content_does_not_crash_parser(scratch):
    bad = scratch / "broken.md"
    bad.write_bytes(b"\xff\xfe\x00\x01 not really markdown")
    document = parse_file(bad, "x", "broken.md")
    # 二进制内容不该让解析器崩掉
    assert document.doc_type == "markdown"
    bad.unlink(missing_ok=True)


def test_unsupported_suffix_raises(scratch):
    weird = scratch / "a.exe"
    weird.write_bytes(b"MZ")
    with pytest.raises(ValueError, match="暂不支持"):
        parse_file(weird, "x", "a.exe")
    weird.unlink(missing_ok=True)


def test_csv_rows_carry_column_names():
    document = parse("客服问答记录.csv")
    table = next(e for e in document.elements if e.kind is ElementKind.TABLE)
    # 每一行都要带列名，喂给模型时才不是一串裸值
    assert table.text.count("问题:") == len(table.rows) - 1
    assert "处理人:" in table.text


def test_markdown_table_is_not_dropped():
    """回归：commonmark 预设不含表格，管道表格曾被整段丢掉。"""
    document = parse("产品需求文档.md")
    tables = [element for element in document.elements if element.kind is ElementKind.TABLE]
    assert tables, "Markdown 管道表格必须被解析出来"
    assert len(tables[0].rows) >= 4, "表头 + 数据行都要在"
    assert tables[0].rows[0][0] == "场景"
    # 表格行号要落在源文件里，且能对上原文的竖线
    source = (SAMPLES / "产品需求文档.md").read_text(encoding="utf-8").splitlines()
    line = source[tables[0].line_start - 1]
    assert "|" in line


# ---------------------------------------------------------------------------
# DOCX / PDF：这两个是二进制格式，样例由 scripts/make_samples.py 生成。
# 生成脚本必须等于解析器的上游，所以样例文件缺失时测试要明确报出来，
# 而不是默默跳过。
# ---------------------------------------------------------------------------

def require_sample(name: str) -> Path:
    path = SAMPLES / name
    if not path.exists():
        pytest.fail(f"缺少样例 {name}，先跑 scripts/make_samples.py")
    return path


def test_docx_headings_styles_and_table():
    document = parse("运维 Runbook.docx")
    assert document.doc_type == "docx"
    assert document.strategy == "structure_aware"

    headings = [e for e in document.elements if e.kind is ElementKind.HEADING]
    assert headings, "Word 标题样式应当被识别成 HEADING"
    titles = [e.text for e in headings]
    assert "运维 Runbook" in titles
    assert any(e.level == 2 and "发布流程" in e.text for e in headings)

    tables = [e for e in document.elements if e.kind is ElementKind.TABLE]
    assert tables, "Word 表格应当被抽出"
    assert tables[0].rows[0] == ["节点类型", "QPS 上限", "说明"]
    assert len(tables[0].rows) == 4


def test_docx_paragraphs_keep_order_and_lines():
    document = parse("运维 Runbook.docx")
    starts = [e.line_start for e in document.elements]
    assert starts == sorted(starts), "块顺序应当与文档顺序一致"
    for element in document.elements:
        assert element.line_start <= element.line_end

    body = "\n".join(e.text for e in document.elements)
    # 段落正文和图注都要在，且不能被截断
    assert "回滚包保留最近三个版本" in body
    assert "回滚后必须核对指标看板" in body


def test_pdf_extracts_real_text_and_pages():
    document = parse("API 参考手册.pdf")
    assert document.doc_type == "pdf"
    assert document.strategy == "structure_aware"
    assert document.page_count == 2

    pages = [e for e in document.elements if e.kind is ElementKind.PAGE_MARK]
    assert [e.page for e in pages] == [1, 2], "两页都要有页边界标记"

    body = "\n".join(e.text for e in document.elements)
    assert "GET /v1/search" in body
    assert "Retry-After" in body
    # 回归：生成样例的字体路径曾经写坏，PDF 里汉字全变 ■（U+25A0）
    assert "\u25a0" not in body, "PDF 文本层出现方块字符，样例生成或解析有问题"
    assert sum(1 for c in body if "\u4e00" <= c <= "\u9fff") > 50


def test_pdf_detects_headings_and_line_spans():
    document = parse("API 参考手册.pdf")
    headings = [e for e in document.elements if e.kind is ElementKind.HEADING]
    assert headings, "PDF 里的编号小节应当被识别为标题"
    assert any("检索接口" in e.text for e in headings)

    # 每页的行号区间必须落在该页范围内，不能跨页乱跳
    for element in document.elements:
        assert element.line_start <= element.line_end
    spans = [(e.line_start, e.line_end) for e in document.elements]
    assert spans == sorted(spans)
