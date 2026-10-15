"""加载器基类与语言识别。

每个加载器只做一件事：把文件读成 ParsedDocument，
分块策略（步骤 3）不在这里做，两者解耦，方便单独替换。
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from pathlib import Path

from ..types import ParsedDocument

# 扩展名 -> 语言名，代码切分（步骤 3）会用到
LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".vue": "vue",
    ".java": "java",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "bash",
    ".ps1": "powershell",
    ".sql": "sql",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
    ".html": "html",
    ".css": "css",
    ".scss": "scss",
}

DOC_TYPE_BY_SUFFIX = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".doc": "docx",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".csv": "csv",
    ".xlsx": "spreadsheet",
    ".xls": "spreadsheet",
    ".json": "code",
}

# 供步骤 3 的分块路由器判断走哪条策略
CHUNK_STRATEGY_BY_DOC_TYPE = {
    "pdf": "structure_aware",
    "docx": "structure_aware",
    "spreadsheet": "structure_aware",
    "csv": "structure_aware",
    "markdown": "markdown",
    "text": "paragraph",
    "code": "code",
    "novel": "paragraph",
}


def detect_language(suffix: str) -> str:
    return LANGUAGE_BY_SUFFIX.get(suffix.lower(), "")


def doc_id_for(path: Path) -> str:
    """用内容哈希做 doc_id，同一份文件重复上传不会重复入库。"""
    digest = hashlib.sha1(path.read_bytes()).hexdigest()
    return digest[:12]


def make_doc_id(stored_name: str, content_hash: str) -> str:
    return content_hash[:12] if content_hash else stored_name[:12]


class BaseLoader(ABC):
    """加载器契约。"""

    name = "base"
    doc_type = "text"
    # 这些扩展名由本加载器负责
    suffixes: tuple[str, ...] = ()

    def supports(self, suffix: str) -> bool:
        return suffix.lower() in self.suffixes

    @abstractmethod
    def load(self, path: Path, doc_id: str, display_name: str) -> ParsedDocument:
        """把 path 解析成 ParsedDocument。"""
        raise NotImplementedError

    def _document(self, path: Path, doc_id: str, display_name: str, **kwargs) -> ParsedDocument:
        return ParsedDocument(
            doc_id=doc_id,
            name=display_name,
            suffix=path.suffix.lower(),
            parser=self.name,
            doc_type=kwargs.pop("doc_type", self.doc_type),
            **kwargs,
        )
