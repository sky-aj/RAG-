"""分块路由：按文档类型选策略，并允许单份文档覆盖参数。

路由规则来自解析层的 resolve_strategy，那份映射和 skills 里的约定一致：
  小说 / 长文      -> paragraph        按空行分段后合并
  Markdown / 文档  -> markdown         按标题层级
  代码            -> code             ast / tree-sitter 按函数类
  PDF / Word/表格  -> structure_aware   按版面结构与语义单元
"""
from __future__ import annotations

from ...config import settings
from ..types import ParsedDocument
from .base import ChunkingStrategy
from .code_ast import CodeStrategy
from .markdown import MarkdownStrategy
from .paragraph import ParagraphStrategy
from .structure_aware import StructureAwareStrategy
from .types import ChunkingReport

STRATEGIES: dict[str, ChunkingStrategy] = {
    ParagraphStrategy.name: ParagraphStrategy(),
    MarkdownStrategy.name: MarkdownStrategy(),
    CodeStrategy.name: CodeStrategy(),
    StructureAwareStrategy.name: StructureAwareStrategy(),
}

# doc_type -> 策略名；解析层也会给一个 strategy，那里优先
DOC_TYPE_STRATEGY = {
    "text": "paragraph",
    "novel": "paragraph",
    "markdown": "markdown",
    "code": "code",
    "pdf": "structure_aware",
    "docx": "structure_aware",
    "csv": "structure_aware",
    "spreadsheet": "structure_aware",
}


def resolve_strategy_name(document: ParsedDocument, override: str | None = None) -> str:
    if override and override in STRATEGIES:
        return override
    if document.strategy and document.strategy in STRATEGIES:
        return document.strategy
    return DOC_TYPE_STRATEGY.get(document.doc_type, "structure_aware")


def chunk_document(
    document: ParsedDocument,
    strategy: str | None = None,
    target_tokens: int | None = None,
    max_tokens: int | None = None,
    overlap_tokens: int | None = None,
) -> ChunkingReport:
    """切分入口。允许临时覆盖目标大小，方便前端调参对比。"""
    name = resolve_strategy_name(document, strategy)
    engine = STRATEGIES[name]

    # 参数覆盖要恢复干净，所以临时改完立刻改回去
    original = (engine.target, engine.maximum, engine.overlap)
    if target_tokens:
        engine.target = target_tokens
    if max_tokens:
        engine.maximum = max_tokens
    if overlap_tokens is not None:
        engine.overlap = overlap_tokens

    try:
        chunks, notes, stats = engine.split(document)
        report = engine.finalize(document, chunks, notes, stats)
    finally:
        engine.target, engine.maximum, engine.overlap = original

    report.stats["requested_strategy"] = name
    report.stats["default_target_tokens"] = settings.chunking.target_tokens
    return report


def available_strategies() -> list[dict]:
    return [
        {
            "name": strategy.name,
            "handles": list(strategy.handles),
            "description": _DESCRIPTIONS.get(strategy.name, ""),
        }
        for strategy in STRATEGIES.values()
    ]


_DESCRIPTIONS = {
    "paragraph": "小说 / 长文：按空行分段，相邻短段合并到目标大小，超长段按句子边界拆。",
    "markdown": "Markdown / 文档：按标题层级建树，标题路径写进每个片段。",
    "code": "代码：Python 用 ast、其他语言用 tree-sitter，按函数 / 类切并抽函数签名。",
    "structure_aware": "PDF / Word / 表格：按版面结构与语义单元切，表格整表保留，块不跨页。",
}
