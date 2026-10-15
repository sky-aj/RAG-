"""\u4e0a\u4e0b\u6587\u62fc\u88c5\u3002

\u8fd9\u4e00\u6b65\u53ea\u505a\u4e00\u4ef6\u4e8b\uff1a\u628a\u68c0\u7d22\u5230\u7684\u7247\u6bb5\u6574\u7406\u6210\u53ef\u4ee5\u76f4\u63a5\u5582\u7ed9\u6a21\u578b\u7684\u6587\u672c\u3002
\u770b\u7740\u7b80\u5355\uff0c\u4f46\u56db\u4e2a\u5751\u90fd\u5728\u8fd9\u91cc\uff1a

1. **\u4e0d\u91cd\u590d\u62fc\u63a5**\uff1a\u540c\u4e00\u6587\u6863\u7684\u7247\u6bb5\u53ef\u80fd\u884c\u53f7\u91cd\u53e0\uff08\u5207\u5206\u65f6\u4fdd\u7559\u4e86\u91cd\u53e0\uff09\u3001
   \u4e5f\u53ef\u80fd\u56e0\u4e3a\u4e24\u8def\u90fd\u547d\u4e2d\u800c\u91cd\u590d\u51fa\u73b0\u3002\u91cd\u53e0\u533a\u95f4\u53ea\u4fdd\u7559\u4e00\u6b21\u3002
2. **\u76f8\u90bb\u5408\u5e76**\uff1a\u540c\u4e00\u6587\u6863\u91cc\u884c\u53f7\u76f8\u63a5\u7684\u7247\u6bb5\u5408\u6210\u4e00\u6bb5\uff0c\u5426\u5219\u540c\u4e00\u8282\u5185\u5bb9\u88ab\u62c6\u6210
   \u597d\u51e0\u5757\u5582\u7ed9\u6a21\u578b\uff0c\u53c8\u8d35\u53c8\u788e\u3002
3. **\u7247\u6bb5\u683c\u5f0f**\uff1a\u884c\u53f7 + \u8bed\u8a00 + \u51fd\u6570\u7b7e\u540d + \u4e00\u53e5\u6458\u8981 + \u7247\u6bb5\u4f53\u3002
   \u7f3a\u4e0d\u5f97\u2014\u2014\u5f15\u7528\u8981\u80fd\u56de\u5230\u539f\u6587\uff0c\u6a21\u578b\u4e5f\u9760\u7b7e\u540d\u4e0e\u6458\u8981\u5224\u65ad\u8fd9\u5757\u662f\u4e0d\u662f\u5b83\u8981\u7684\u3002
4. **\u9884\u7b97\u63a7\u5236**\uff1a\u6309\u5206\u6570\u8d2a\u5fc3\u88c5\u5165\uff0c\u8d85\u9884\u7b97\u5c31\u505c\uff0c\u4e0d\u80fd\u628a\u63d0\u793a\u8bcd\u649e\u7206\u3002
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from ..config import settings
from ..ingest.chunking.base import estimate_tokens

logger = logging.getLogger(__name__)


@dataclass
class Snippet:
    """\u4e00\u4e2a\u88c5\u8fdb\u4e0a\u4e0b\u6587\u7684\u7247\u6bb5\u3002"""

    doc_id: str
    doc_name: str
    line_start: int
    line_end: int
    text: str
    language: str = ""
    symbol: str = ""
    summary: str = ""
    heading_path: list[str] = field(default_factory=list)
    score: float = 0.0
    chunk_ids: list[str] = field(default_factory=list)
    page: int | None = None

    @property
    def label(self) -> str:
        """\u7ed9\u6a21\u578b\u770b\u7684\u5b9a\u4f4d\u6807\u7b7e\u3002"""
        parts = [f"{self.doc_name} \u884c{self.line_start}-{self.line_end}"]
        if self.page:
            parts.append(f"P{self.page}")
        if self.language:
            parts.append(self.language)
        return " | ".join(parts)

    def render(self, index: int) -> str:
        """\u6309\u7ea6\u5b9a\u683c\u5f0f\u6e32\u67d3\uff1a\u884c\u53f7 + \u8bed\u8a00 + \u51fd\u6570\u7b7e\u540d + \u4e00\u53e5\u6458\u8981 + \u7247\u6bb5\u4f53\u3002"""
        head = [f"[\u7247\u6bb5 {index}] {self.doc_name} \u884c {self.line_start}-{self.line_end}"]
        if self.page:
            head.append(f"\u7b2c {self.page} \u9875")
        if self.language:
            head.append(f"\u8bed\u8a00 {self.language}")
        lines = ["\u3001".join(head)]

        if self.heading_path:
            lines.append("\u6807\u9898\u8def\u5f84\uff1a" + " \u203a ".join(self.heading_path))
        if self.symbol:
            lines.append("\u7b7e\u540d\uff1a" + self.symbol)
        if self.summary:
            lines.append("\u6458\u8981\uff1a" + self.summary)
        lines.append("\u5185\u5bb9\uff1a")
        lines.append(self.text)
        return "\n".join(lines)


def _estimate(text: str) -> int:
    return estimate_tokens(text)


def _overlaps(a: Snippet, b: Snippet) -> bool:
    """\u4e24\u4e2a\u7247\u6bb5\u884c\u53f7\u662f\u5426\u6709\u91cd\u53e0\uff08\u542b\u76f8\u63a5\uff09\u3002"""
    return a.line_start <= b.line_end and b.line_start <= a.line_end


def _merge_text(base: str, incoming: str) -> str:
    """把 incoming 接到 base 后面，去掉边界上重复的那一段。

    做法：找 base 的最长后缀，同时是 incoming 的前缀，只保留 incoming 的剩余部分。
    重叠行号导致的重复段落因此不会出现两次——这正是“不要重复拼接”要求的那件事。
    """
    if not base:
        return incoming
    if not incoming:
        return base
    if incoming in base:
        return base
    if base in incoming:
        return incoming

    limit = min(len(base), len(incoming))
    overlap = 0
    for size in range(limit, 1, -1):
        if base[-size:] == incoming[:size]:
            overlap = size
            break
    # overlap 只有 1 个字符时不裁：单个字符撞上太常见，裁掉会丢字
    return base + (incoming[overlap:] if overlap >= 2 else incoming)


def merge_snippets(hits: Sequence[dict], gap: int = 2) -> list[Snippet]:
    """去重 + 相邻合并。

    gap：两段之间行号差不超过 gap 就当作“相邻”合并。
    为什么不要求严格相接：原文里常有空行，切分时被归到两边，
    严格相接会让本来连着的两块合不了。
    """
    grouped: dict[str, list[dict]] = {}
    order: list[str] = []

    for hit in hits:
        doc_id = str(hit.get("doc_id", ""))
        if not doc_id:
            continue
        if doc_id not in grouped:
            grouped[doc_id] = []
            order.append(doc_id)
        grouped[doc_id].append(hit)

    snippets: list[Snippet] = []
    for doc_id in order:
        chunks = sorted(grouped[doc_id], key=lambda item: (item.get("line_start", 0), item.get("line_end", 0)))
        current: Snippet | None = None

        for hit in chunks:
            start = int(hit.get("line_start") or 0)
            end = int(hit.get("line_end") or start)
            text = (hit.get("text") or "").strip()
            if not text:
                continue

            if current is not None and start <= current.line_end + gap:
                # 重叠或相邻：合并进当前片段。
                # 重叠行号只保留一次：用「最长后缀=前缀」裁掉重复的接缝。
                if end > current.line_end:
                    current.text = _merge_text(current.text, text)
                    current.line_end = end
                elif text not in current.text:
                    current.text = _merge_text(current.text, text)
                current.chunk_ids.append(str(hit.get("chunk_id", "")))
                current.score = max(current.score, float(hit.get("score") or 0.0))
                # 保留更完整的元信息
                current.symbol = current.symbol or str(hit.get("symbol") or "")
                current.summary = current.summary or str(hit.get("summary") or "")
                if not current.heading_path:
                    current.heading_path = list(hit.get("heading_path") or [])
                if not current.language:
                    current.language = str(hit.get("language") or "")
                continue

            current = Snippet(
                doc_id=doc_id,
                doc_name=str(hit.get("doc_name") or ""),
                line_start=start,
                line_end=end,
                text=text,
                language=str(hit.get("language") or ""),
                symbol=str(hit.get("symbol") or ""),
                summary=str(hit.get("summary") or ""),
                heading_path=list(hit.get("heading_path") or []),
                score=float(hit.get("score") or 0.0),
                chunk_ids=[str(hit.get("chunk_id", ""))],
                page=hit.get("page"),
            )
            snippets.append(current)

    # 最后按分数排，保证“最相关的排在前面”不受合并顺序影响
    snippets.sort(key=lambda item: item.score, reverse=True)
    return snippets

def build_context(
    hits: Sequence[dict],
    budget: int | None = None,
    gap: int = 2,
) -> dict:
    """\u628a\u68c0\u7d22\u7ed3\u679c\u62fc\u6210\u6b63\u6587\u4e0e\u5f15\u7528\u6e05\u5355\u3002

    \u8fd4\u56de\uff1a
      text        \u5582\u7ed9\u6a21\u578b\u7684\u7247\u6bb5\u6587\u672c
      citations   \u524d\u7aef\u5c55\u793a\u7684\u5f15\u7528\uff08\u4e0e text \u91cc\u7684\u7247\u6bb5\u7f16\u53f7\u4e00\u4e00\u5bf9\u5e94\uff09
      used        \u5b9e\u9645\u88c5\u8fdb\u53bb\u7684\u7247\u6bb5\u6570
      dropped     \u56e0\u9884\u7b97\u88ab\u820d\u5f03\u7684\u7247\u6bb5\u6570
      tokens      \u4f30\u7b97 token
    """
    limit = budget or settings.retrieval.context_token_budget
    snippets = merge_snippets(hits, gap=gap)

    used: list[Snippet] = []
    spent = 0
    dropped = 0

    for snippet in snippets:
        rendered = snippet.render(len(used) + 1)
        cost = _estimate(rendered) + 8  # \u7a7a\u884c\u4e0e\u5206\u9694\u7b26\u7684\u4f59\u91cf
        if used and spent + cost > limit:
            dropped += 1
            continue
        used.append(snippet)
        spent += cost

    blocks: list[str] = []
    citations: list[dict] = []
    for index, snippet in enumerate(used, start=1):
        blocks.append(snippet.render(index))
        citations.append(
            {
                "index": index,
                "doc_id": snippet.doc_id,
                "doc_name": snippet.doc_name,
                "location": f"\u884c {snippet.line_start}-{snippet.line_end}",
                "line_start": snippet.line_start,
                "line_end": snippet.line_end,
                "page": snippet.page,
                "language": snippet.language,
                "symbol": snippet.symbol,
                "summary": snippet.summary,
                "heading_path": snippet.heading_path,
                "score": round(snippet.score, 4),
                "chunk_ids": snippet.chunk_ids,
                # 引用要能回到原文，评测也要拿它当 retrieved_contexts，
                # 所以正文必须跟着出去，不能只给行号。
                "quote": snippet.text,
            }
        )

    return {
        "text": "\n\n".join(blocks),
        "citations": citations,
        "used": len(used),
        "dropped": dropped,
        "merged_from": len([hit for hit in hits if hit.get("doc_id")]),
        "tokens": spent,
        "budget": limit,
    }


def describe() -> dict:
    return {
        "token_budget": settings.retrieval.context_token_budget,
        "format": "\u884c\u53f7 + \u8bed\u8a00 + \u51fd\u6570\u7b7e\u540d + \u4e00\u53e5\u6458\u8981 + \u7247\u6bb5\u4f53",
        "dedupe": "\u540c\u6587\u6863\u91cd\u53e0\u884c\u53f7\u53ea\u4fdd\u7559\u4e00\u6b21",
        "merge": "\u540c\u6587\u6863\u76f8\u90bb\u7247\u6bb5\u5408\u5e76\u4e3a\u4e00\u6bb5",
    }
