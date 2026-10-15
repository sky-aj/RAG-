"""纯文本与代码：直接按物理行切。

这是最保真的一类，因为文件本来就有真实行号，
后面算 chunk 的行号区间时不用做任何映射。
"""
from __future__ import annotations

from pathlib import Path

from ..types import Element, ElementKind, ParsedDocument
from .base import BaseLoader, detect_language

# 按顺序尝试，中文文档在 Windows 上常见 gbk
ENCODINGS = ("utf-8", "utf-8-sig", "gb18030", "big5", "latin-1")


def read_text(path: Path) -> tuple[str, str]:
    """返回 (文本, 实际使用的编码)。"""
    raw = path.read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace"), "utf-8(replace)"


def split_lines(text: str) -> list[str]:
    """统一换行符后按行切开。"""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.split("\n")


# 纯文本里的标题样式：中文章节号、数字编号、Markdown 井号
HEADING_PATTERNS: tuple[tuple[str, int], ...] = (
    ("第一章", 1),
    ("第二章", 1),
    ("第三章", 1),
    ("第一节", 2),
    ("一、", 1),
    ("二、", 1),
    ("三、", 1),
    ("四、", 1),
    ("五、", 1),
    ("六、", 1),
    ("七、", 1),
    ("八、", 1),
    ("九、", 1),
    ("十、", 1),
)


def guess_heading_level(line: str) -> int:
    """纯文本没有样式信息，只能按写法猜，猜不中就返回 0。"""
    stripped = line.strip()
    if not stripped or len(stripped) > 40:
        return 0
    # 以句读结尾的通常是正文
    if stripped.endswith(("。", ".", "；", ";", "：", ":", "！", "？", "，", ",")):
        return 0

    if stripped.startswith("#"):
        marks = len(stripped) - len(stripped.lstrip("#"))
        return min(max(marks, 1), 6)
    for prefix, level in HEADING_PATTERNS:
        if stripped.startswith(prefix):
            return level
    # 1. 标题 / 1.1 标题 / 1、标题
    digits = ""
    for index, char in enumerate(stripped):
        if char.isdigit():
            digits += char
            continue
        if char in "．.、）)" and digits:
            rest = stripped[index + 1 :]
            if rest and not rest.startswith((" ", "\t")):
                return 0
            return 2 if ("." in stripped[:index] and stripped.count(".") > 1) else 1
        break
    return 0


def build_elements_from_lines(lines: list[str], language: str = "", quote_marker: str = ">") -> list[Element]:
    """把行序列转成 Element 序列。

    连续的普通行合并成一个 PARAGRAPH，空行作为边界，
    这样切分层能直接看到「段落」这个语义单位，而不是逐行处理。
    """
    elements: list[Element] = []
    buffer: list[str] = []
    buffer_start = 0

    def flush(end_line: int) -> None:
        nonlocal buffer, buffer_start
        if not buffer:
            return
        text = "\n".join(buffer).strip("\n")
        if text:
            # 单行且长得像标题的，直接标成 HEADING，
            # 这样纯文本也能给出层级，而不是一整片平铺段落
            level = guess_heading_level(text) if len(buffer) == 1 else 0
            elements.append(
                Element(
                    kind=ElementKind.HEADING if level else ElementKind.PARAGRAPH,
                    text=text,
                    line_start=buffer_start,
                    line_end=end_line,
                    level=level,
                    language=language,
                )
            )
        buffer = []
        buffer_start = 0

    for index, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            flush(index - 1)
            continue
        if buffer_start == 0:
            buffer_start = index
        buffer.append(line)

    flush(len(lines))
    return elements


class TextLoader(BaseLoader):
    name = "text"
    doc_type = "text"
    suffixes = (".txt",)

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        text, encoding = read_text(path)
        lines = split_lines(text)
        elements = build_elements_from_lines(lines)
        return self._document(
            path,
            doc_id,
            display_name,
            elements=elements,
            encoding=encoding,
            warnings=[] if elements else ["文件里没有可解析的文本"],
        )


class CodeLoader(BaseLoader):
    name = "code"
    doc_type = "code"
    # 具体扩展名在注册表里按 base.LANGUAGE_BY_SUFFIX 动态匹配
    suffixes = ()

    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        language = detect_language(path.suffix)
        text, encoding = read_text(path)
        lines = split_lines(text)

        # 代码不按空行合并，先保留成「整块」，步骤 3 会用 AST 按函数/类重新切。
        body = "\n".join(lines).strip("\n")
        elements: list[Element] = []
        if body:
            elements.append(
                Element(
                    kind=ElementKind.CODE,
                    text=body,
                    line_start=1,
                    line_end=len(lines),
                    language=language,
                )
            )
        return self._document(
            path,
            doc_id,
            display_name,
            elements=elements,
            encoding=encoding,
            warnings=[] if elements else ["文件内容为空"],
        )
