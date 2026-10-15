"""步骤 3 的切分测试：四条策略的行为与不变量。

重点覆盖两类：
  1. 结构性不变量（行号递增、区间合法、非空、不超上限）
  2. 每条策略「该做对的那件事」——标题路径、函数签名、表格完整性、块不跨页
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.ingest import parse_file
from app.ingest.chunking import available_strategies, chunk_document, resolve_strategy_name

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


def build(name: str, strategy: str | None = None, **kwargs):
    path = SAMPLES / name
    assert path.exists(), f"缺少样例 {name}，先跑 scripts/make_samples.py"
    document = parse_file(path, "test0003", name)
    return document, chunk_document(document, strategy=strategy, **kwargs)


def assert_invariants(report) -> None:
    """所有策略都必须满足的硬性条件。"""
    assert report.chunks, "至少要切出一块"
    previous_start = 0
    for chunk in report.chunks:
        assert chunk.text.strip(), f"空块 {chunk.chunk_id}"
        assert chunk.line_start >= 1
        assert chunk.line_start <= chunk.line_end, f"行号区间非法 {chunk.chunk_id}"
        # 行号必须随块序单调不减，否则引用会乱跳
        assert chunk.line_start >= previous_start, f"行号倒退 {chunk.chunk_id}"
        previous_start = chunk.line_start
        assert chunk.doc_id and chunk.doc_name and chunk.chunk_id
        assert chunk.strategy == report.strategy


def test_router_maps_doc_types_to_strategies():
    mapping = {
        "运维笔记.txt": "paragraph",
        "产品需求文档.md": "markdown",
        "quota.py": "code",
        "客服问答记录.csv": "structure_aware",
        "运维 Runbook.docx": "structure_aware",
        "API 参考手册.pdf": "structure_aware",
    }
    for name, expected in mapping.items():
        document_user = SAMPLES / name
        assert document_user.exists(), name
        document = parse_file(document_user, "t", name)
        assert resolve_strategy_name(document) == expected, name


def test_available_strategies_lists_all_four():
    names = {item["name"] for item in available_strategies()}
    assert names == {"paragraph", "markdown", "code", "structure_aware"}
    for item in available_strategies():
        assert item["description"], item["name"]


def test_paragraph_strategy_merges_and_keeps_heading_context():
    report = _report("运维笔记.txt")
    assert report.strategy == "paragraph"
    assert_invariants(report)
    # 章节标题应当成为块的 section
    sections = {chunk.section for chunk in report.chunks}
    assert any("灰度发布" in section for section in sections), sections


def test_paragraph_strategy_respects_target_by_merging():
    small = _report("运维笔记.txt", target_tokens=25)
    large = _report("运维笔记.txt", target_tokens=400)
    assert len(small.chunks) >= len(large.chunks), "目标调小后块数不应减少"
    # 合并后每块都不该超过上限太多
    for chunk in large.chunks:
        assert len(chunk.text) <= 400 * 4, chunk.chunk_id


def test_markdown_chunks_carry_heading_path():
    report = _report("产品需求文档.md")
    assert report.strategy == "markdown"
    assert_invariants(report)

    paths = [chunk.heading_path for chunk in report.chunks]
    assert any(len(path) >= 3 for path in paths), "应当有三级标题路径"
    deep = next(chunk for chunk in report.chunks if len(chunk.heading_path) >= 3)
    assert deep.heading_path[0].startswith("产品需求文档"), deep.heading_path
    assert any("限流策略" in part for part in deep.heading_path)


def test_markdown_keeps_table_and_code_intact():
    report = _report("产品需求文档.md")
    tables = [chunk for chunk in report.chunks if chunk.kind == "table"]
    codes = [chunk for chunk in report.chunks if chunk.kind == "code"]
    assert tables, "表格应当单独成块"
    assert codes, "代码块应当单独成块"
    # 表格块要完整包含表头和所有数据行
    table_text = tables[0].text
    assert "场景" in table_text and "企业版" in table_text
    # 代码块不能被截断
    assert "def check_quota" in codes[0].text


def test_code_strategy_splits_python_by_definition():
    report = _report("quota.py")
    assert report.strategy == "code"
    assert_invariants(report)
    assert report.stats["language"] == "python"

    symbols = [chunk.symbol for chunk in report.chunks]
    assert any(symbol.startswith("class SlidingWindowCounter") for symbol in symbols), symbols
    assert any(symbol.startswith("class QuotaResult") for symbol in symbols), symbols

    # 模块头部单独成块
    assert any(chunk.symbol == "module-level" for chunk in report.chunks)

    # 每个定义块的行号要能对上源码里那一行
    source = (SAMPLES / "quota.py").read_text(encoding="utf-8").splitlines()
    counter = next(chunk for chunk in report.chunks if chunk.symbol.startswith("class SlidingWindowCounter"))
    assert source[counter.line_start - 1].startswith("class SlidingWindowCounter")

    # 装饰器要算进块里：QuotaResult 带 @dataclass，行号应当从装饰器那行开始
    dataclass_chunk = next(chunk for chunk in report.chunks if chunk.symbol.startswith("class QuotaResult"))
    assert source[dataclass_chunk.line_start - 1].strip() == "@dataclass"
    assert "@dataclass" in dataclass_chunk.text


def test_code_strategy_falls_back_for_other_languages():
    """非 Python 语言没有 ast，要能走 tree-sitter 或明确退化，而不是崩掉。"""
    report = _report("quota.py")
    assert report.chunks

    # 构造一份 TS 代码验证 tree-sitter 路径
    from app.ingest.types import Element, ElementKind, ParsedDocument

    source = (
        "import { x } from 'y'\n\n"
        "export class Limit {\n"
        "  check(id: string): boolean {\n    return true;\n  }\n}\n\n"
        "export function helper(a: number) {\n  return a * 2;\n}\n"
    )
    document = ParsedDocument(
        doc_id="ts0001",
        name="limit.ts",
        suffix=".ts",
        parser="code",
        doc_type="code",
        strategy="code",
        elements=[
            Element(
                kind=ElementKind.CODE,
                text=source,
                line_start=1,
                line_end=len(source.splitlines()),
                language="typescript",
            )
        ],
    )
    ts_report = chunk_document(document)
    assert ts_report.chunks
    assert_invariants(ts_report)

    symbols = " ".join(chunk.symbol for chunk in ts_report.chunks)
    names = " ".join(chunk.section for chunk in ts_report.chunks)
    # 能拿到定义就算成功；拿不到也要明确退化而不是静默丢弃
    if ts_report.stats.get("definitions"):
        assert "Limit" in symbols + names or "helper" in symbols + names, symbols + names


def test_structure_aware_keeps_table_whole():
    report = _report("客服问答记录.csv")
    assert report.strategy == "structure_aware"
    assert_invariants(report)
    assert report.stats["tables_whole"] >= 1
    # 整表保留时，最后一行数据也要在
    assert "王芳" in report.chunks[0].text or "李伟" in report.chunks[0].text


def test_structure_aware_does_not_cross_pages():
    report = _report("API 参考手册.pdf")
    assert report.strategy == "structure_aware"
    assert_invariants(report)
    assert report.stats["page_breaks"] >= 1, "PDF 应当出现页边界"

    # 每块只能落在一页里
    for chunk in report.chunks:
        assert chunk.page is not None, chunk.chunk_id
        assert 1 <= chunk.page <= 2


def test_structure_aware_word_ignores_heading_blocks_in_text():
    report = _report("运维 Runbook.docx")
    assert_invariants(report)
    body = "\n".join(chunk.text for chunk in report.chunks)
    # 标题不应该被当成正文内容重复一遍
    assert body.count("运维 Runbook") == 0 or body.count("运维 Runbook") == 1
    assert "回滚包保留最近三个版本" in body


def test_overlong_unit_is_split_but_content_is_preserved():
    """超长段落必须被拆，但拆出来的内容加起来要和原文一致（允许重叠）。"""
    long_text = "。".join(f"这是第 {index} 句用于测试拆分的内容" for index in range(1, 400)) + "。"
    from app.ingest.types import Element, ElementKind, ParsedDocument

    document = ParsedDocument(
        doc_id="long0001",
        name="长文.txt",
        suffix=".txt",
        parser="text",
        doc_type="text",
        strategy="paragraph",
        elements=[Element(kind=ElementKind.PARAGRAPH, text=long_text, line_start=1, line_end=3)],
    )
    report = chunk_document(document)
    assert len(report.chunks) > 1, "超长段落应当被拆成多块"
    assert_invariants(report)

    for chunk in report.chunks:
        assert len(chunk.text) / 3.5 <= 800 * 1.6, f"{chunk.chunk_id} 超出上限过多"

    # 首句和末句都要在，不能被截掉
    assert "这是第 1 句" in report.chunks[0].text
    assert "这是第 399 句" in report.chunks[-1].text


def test_empty_document_yields_no_chunks():
    from app.ingest.types import ParsedDocument

    document = ParsedDocument(
        doc_id="empty", name="空.txt", suffix=".txt", parser="text", doc_type="text", strategy="paragraph"
    )
    report = chunk_document(document)
    assert report.chunks == []
    assert report.summary()["chunk_total"] == 0


def test_summary_shape_matches_api_contract():
    report = _report("产品需求文档.md")
    payload = report.summary()
    for key in ("doc_id", "doc_name", "strategy", "chunk_total", "notes", "stats", "details", "chunks"):
        assert key in payload, key
    for key in ("target_tokens", "max_tokens", "overlap_tokens", "char_min", "char_max", "char_avg"):
        assert key in payload["stats"], key
    assert isinstance(payload["chunks"], list)
    assert payload["chunks"][0]["chunk_id"]


def _report(name: str, **kwargs):
    _, report = build(name, **kwargs)
    return report
