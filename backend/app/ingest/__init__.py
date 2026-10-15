"""解析层入口。

步骤 2 只做解析：文件 -> ParsedDocument（带行号与结构的 Element 序列）。
步骤 3 的分块策略接在 parse_file 之后，输入同样是 ParsedDocument，两边解耦。
"""
from .loaders import SUPPORTED_SUFFIXES, parse_file, resolve_doc_type, resolve_strategy
from .types import Element, ElementKind, ParsedDocument

__all__ = [
    "SUPPORTED_SUFFIXES",
    "parse_file",
    "resolve_doc_type",
    "resolve_strategy",
    "Element",
    "ElementKind",
    "ParsedDocument",
]
