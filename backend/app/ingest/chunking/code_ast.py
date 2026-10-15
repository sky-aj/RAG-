"""代码策略：按函数 / 类切，不做正则硬切。

Python 用标准库 ast，能准确拿到装饰器、docstring、嵌套定义和行号。
其他语言用 tree-sitter 的语法树，按 function_definition / class_declaration
这类节点切，取签名、docstring 和节点行号。

切分单位是「一个可独立理解的定义」：签名 + docstring + 函数体。
模块级的 import / 常量等零散语句会合并成一个「模块头部」块。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from ...config import settings
from ..types import ElementKind, ParsedDocument
from .base import ChunkingStrategy, estimate_tokens
from .types import Chunk

# tree-sitter 里代表「可独立成块的定义」的节点类型
TS_DEFINITION_NODES = {
    "function_definition",
    "function_declaration",
    "method_definition",
    "method_declaration",
    "class_definition",
    "class_declaration",
    "constructor_declaration",
    "interface_declaration",
    "struct_item",
    "impl_item",
    "enum_item",
    "trait_item",
    "function_item",
    "module",
}
TS_LANGUAGE_MAP = {
    "javascript": "javascript",
    "typescript": "typescript",
    "tsx": "tsx",
    "vue": "vue",
    "java": "java",
    "go": "go",
    "rust": "rust",
    "c": "c",
    "cpp": "cpp",
    "ruby": "ruby",
    "php": "php",
    "csharp": "csharp",
    "kotlin": "kotlin",
    "scala": "scala",
    "swift": "swift",
}


@dataclass
class CodeUnit:
    """一个代码切分单位：要么是个定义，要么是模块级零散语句。"""

    symbol: str
    kind: str
    text: str
    line_start: int
    line_end: int
    name: str = ""
    decorators: list[str] = field(default_factory=list)


class CodeStrategy(ChunkingStrategy):
    name = "code"
    handles = ("code",)

    def split(self, document: ParsedDocument) -> tuple[list[Chunk], list[str], dict]:
        notes: list[str] = []
        language = next((e.language for e in document.elements if e.language), "")
        source = "\n".join(element.text for element in document.elements)
        total_lines = document.line_total

        if language == "python":
            units, head = self._split_python(source)
            if units or head:
                notes.append(f"用 Python ast 解析，取到 {len(units)} 个函数 / 类定义")
        else:
            units, head, used_ts = self._split_tree_sitter(source, language)
            if used_ts:
                notes.append(f"用 tree-sitter({language}) 解析，取到 {len(units)} 个定义")
            else:
                notes.append(f"没有可用的 {language or '未知语言'} 语法树，已退化为按语句块切分")

        chunks: list[Chunk] = []

        if head.strip():
            for piece in self.split_long_text(head):
                chunks.append(
                    self.make_chunk(
                        document,
                        len(chunks),
                        piece,
                        line_start=1,
                        line_end=_line_count(head, 1),
                        kind="code_module",
                        language=language,
                        symbol="module-level",
                        summary="模块头部：导入与模块级常量",
                    )
                )

        merged_small = 0
        for unit in units:
            summary = _first_line(unit.text)
            body = unit.text
            pieces = self.split_long_text(body) if estimate_tokens(body) > self.maximum else [body]
            for piece in pieces:
                chunks.append(
                    self.make_chunk(
                        document,
                        len(chunks),
                        piece,
                        line_start=unit.line_start,
                        line_end=unit.line_end,
                        kind=unit.kind,
                        language=language,
                        symbol=unit.symbol,
                        section=unit.name,
                        summary=summary,
                        meta={"decorators": unit.decorators} if unit.decorators else {},
                    )
                )

        if not chunks and source.strip():
            notes.append("没有识别出定义，整份文件作为一个块")
            chunks.append(
                self.make_chunk(
                    document,
                   0,
                    source,
                    line_start=1,
                    line_end=total_lines,
                    kind="code",
                    language=language,
                    symbol="file",
                )
            )

        stats = {
            "language": language,
            "definitions": len(units),
            "has_module_head": bool(head.strip()),
            "merged_small": merged_small,
        }
        return chunks, notes, stats

    # ---------- Python ----------

    def _split_python(self, source: str) -> tuple[list[CodeUnit], str]:
        try:
            tree = ast.parse(source)
        except SyntaxError:
            return [], source

        lines = source.split("\n")
        units: list[CodeUnit] = []
        head_end = 0

        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                start = _decorator_start(node)
                end = node.end_lineno or node.lineno
                decorators = [
                    ast.get_source_segment(source, decorator) or ""
                    for decorator in getattr(node, "decorator_list", [])
                ]
                symbol = _python_signature(node)
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                units.append(
                    CodeUnit(
                        symbol=symbol,
                        kind=kind,
                        text="\n".join(lines[start - 1 : end]).strip(),
                        line_start=start,
                        line_end=end,
                        name=node.name,
                        decorators=[d for d in decorators if d],
                    )
                )
            else:
                head_end = max(head_end, node.end_lineno or node.lineno)

        head = "\n".join(lines[:head_end]).strip() if head_end else ""
        return units, head

    # ---------- tree-sitter ----------

    def _split_tree_sitter(self, source: str, language: str) -> tuple[list[CodeUnit], str, bool]:
        ts_language = TS_LANGUAGE_MAP.get(language)
        if not ts_language:
            return [], source, False
        try:
            from tree_sitter_language_pack import get_parser

            parser = get_parser(ts_language)
            tree = parser.parse(source.encode("utf-8"))
        except Exception:  # noqa: BLE001 - 语法包缺失或该语言不支持
            return [], source, False

        lines = source.split("\n")
        units: list[CodeUnit] = []
        covered: list[tuple[int, int]] = []

        def walk(node, depth: int = 0) -> None:
            if depth > 4:
                return
            for child in node.children:
                if child.type in TS_DEFINITION_NODES:
                    start = child.start_point[0] + 1
                    end = child.end_point[0] + 1
                    text = "\n".join(lines[start - 1 : end]).strip()
                    units.append(
                        CodeUnit(
                            symbol=_ts_signature(lines, child.start_point[0], child.end_point[0]),
                            kind="class" if "class" in child.type or "struct" in child.type else "function",
                            text=text,
                            line_start=start,
                            line_end=end,
                            name=_ts_name(child),
                        )
                    )
                    covered.append((start, end))
                    # 定义内部的嵌套定义不再单独成块，避免切得过碎
                else:
                    walk(child, depth + 1)

        walk(tree.root_node)

        head_lines = [
            line
            for index, line in enumerate(lines, start=1)
            if line.strip() and not any(start <= index <= end for start, end in covered)
        ]
        head = "\n".join(head_lines).strip()
        return units, head, True


def _decorator_start(node) -> int:
    decorators = getattr(node, "decorator_list", None)
    if decorators:
        return min(decorator.lineno for decorator in decorators)
    return node.lineno


def _python_signature(node) -> str:
    """拼出人类可读的函数签名，用于上下文里的「函数签名」字段。"""
    if isinstance(node, ast.ClassDef):
        bases = [ast.unparse(base) for base in node.bases]
        suffix = f"({', '.join(bases)})" if bases else ""
        return f"class {node.name}{suffix}"

    args = ast.unparse(node.args) if node.args else ""
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({args}){returns}"


def _ts_name(node) -> str:
    child = node.child_by_field_name("name")
    if child is not None:
        return child.text.decode("utf-8", errors="replace")
    for item in node.children:
        if item.type in {"identifier", "type_identifier", "property_identifier"}:
            return item.text.decode("utf-8", errors="replace")
    return ""


def _ts_signature(lines: list[str], start_row: int, end_row: int) -> str:
    """取定义的第一行到左花括号为止，作为签名。"""
    for index in range(start_row, min(end_row, start_row + 6)):
        line = lines[index] if index < len(lines) else ""
        if "{" in line:
            return line.split("{")[0].strip()
    return lines[start_row].strip() if start_row < len(lines) else ""


def _first_line(text: str) -> str:
    """摘要的占位实现：取首个非注释行。步骤 5 会换成 LLM 生成的摘要。"""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped and not stripped.startswith(("#", "//", "/*", "*", '"""', "'''")):
            return stripped[:110]
    return text.strip().split("\n")[0][:110]


def _line_count(text: str, start: int) -> int:
    return start + len(text.split("\n")) - 1
