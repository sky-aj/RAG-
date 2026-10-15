"""加载器注册表：按扩展名找到能处理它的加载器。

新增文件类型时只在这里挂一行，router 与 API 都不用动。
"""
from __future__ import annotations

from pathlib import Path

from .base import (
    CHUNK_STRATEGY_BY_DOC_TYPE,
    DOC_TYPE_BY_SUFFIX,
    LANGUAGE_BY_SUFFIX,
    BaseLoader,
    detect_language,
    make_doc_id,
)
from .docx import DocxLoader
from .markdown import MarkdownLoader
from .pdf import PdfLoader
from .spreadsheet import CsvLoader, ExcelLoader
from .text_code import CodeLoader, TextLoader

LOADERS: tuple[BaseLoader, ...] = (
    PdfLoader(),
    DocxLoader(),
    MarkdownLoader(),
    CsvLoader(),
    ExcelLoader(),
    TextLoader(),
    CodeLoader(),
)

# 代码加载器负责 base 里登记过的所有语言扩展名
CODE_SUFFIXES = tuple(LANGUAGE_BY_SUFFIX.keys())
SUPPORTED_SUFFIXES = sorted(
    set(CodeLoader().suffixes) | set(CODE_SUFFIXES) | {s for loader in LOADERS for s in loader.suffixes}
)


def find_loader(suffix: str) -> BaseLoader | None:
    suffix = suffix.lower()
    for loader in LOADERS:
        if loader is not None and loader.supports(suffix):
            return loader
    # 兜底：base 里登记为代码的扩展名
    if suffix in CODE_SUFFIXES:
        return next(loader for loader in LOADERS if isinstance(loader, CodeLoader))
    return None


def resolve_doc_type(suffix: str) -> str:
    suffix = suffix.lower()
    if suffix in DOC_TYPE_BY_SUFFIX:
        return DOC_TYPE_BY_SUFFIX[suffix]
    if suffix in CODE_SUFFIXES:
        return "code"
    return "text"


def resolve_strategy(doc_type: str) -> str:
    return CHUNK_STRATEGY_BY_DOC_TYPE.get(doc_type, "structure_aware")


def parse_file(path: Path, doc_id: str, display_name: str):
    """解析入口：返回 ParsedDocument，失败抛 ValueError 并带上原因。"""
    suffix = path.suffix.lower()
    loader = find_loader(suffix)
    if loader is None:
        raise ValueError(f"暂不支持的文件类型：{suffix or '未知'}")
    document = loader.load(path, doc_id, display_name)
    document.strategy = resolve_strategy(document.doc_type)
    return document


__all__ = [
    "LOADERS",
    "SUPPORTED_SUFFIXES",
    "CODE_SUFFIXES",
    "BaseLoader",
    "detect_language",
    "find_loader",
    "make_doc_id",
    "parse_file",
    "resolve_doc_type",
    "resolve_strategy",
]
