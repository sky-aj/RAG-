"""查询改写（步骤 7）。

一个口语化的问题，往往**不是检索器最容易命中的那种问法**。

例子：「那个限流的事儿是怎么弄的？」
  * 检索器真正要找的是「限流策略 / 默认额度 / 每分钟请求数 / 滑动窗口」这些词
  * 但原句里只有「限流」一个实词，另外还混了「那个……事儿」这种口语填充词

所以这里做两件事：
  1. 把问题改写成 2~3 个**互补的检索子查询**（术语版、拆解版），各自去召回
  2. 多路子查询 + 原本的混合检索一起做 RRF，谁命中并集更大谁说了算

为什么要拆而不是简单同义改写：同义改写召回的是同一批文档，收益≈0；
把「额度」和「超出后的行为」拆成两个子查询，才会召回互补的片段。

失败策略：这一步只是「锦上添花」，模型不可用时原样返回原问题，
绝不因为改写失败而让整个问答挂掉。
"""
from __future__ import annotations

import json
import logging
import re

from .config import settings

logger = logging.getLogger(__name__)

# 改写用的系统提示。要求输出 JSON 数组，方便解析；数量上限写死 3，
# 因为每个子查询都要多算一次向量 + 一次 BM25，是有成本的。
_REWRITE_SYSTEM = """你在为检索系统做查询改写。给定一个用户问题，把它拆成 1~3 个更容易检索到材料的子查询。

要求：
1. 子查询必须互补——分别面向问题的不同侧面（例如「具体数值」和「超限后的行为」），不要只是换同义词。
2. 每条接近专业术语，而不是口语；去掉「那个」「怎么弄」这类填充词。
3. 每条不超过 30 个字，不要加引号。
4. 如果原问题已经很明确，就只返回它本身（数组里只有一个元素）。
5. 严格输出 JSON 数组，不要解释、不要代码块标记：
   ["子查询 1", "子查询 2"]
"""

MAX_SUBQUERIES = 3


def parse_rewrites(raw: str, original: str) -> list[str]:
    """从模型输出里抠出子查询列表。

    模型偶尔会包 ```json 代码块或多说一句，所以先剥壳再找最外层的 []。
    任何一步失败都退回 [original]——改写失败不该影响问答。
    """
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
        text = text.strip()

    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return [original]
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return [original]
    if not isinstance(payload, list):
        return [original]

    items: list[str] = []
    for item in payload:
        value = str(item or "").strip().strip('"').strip()
        value = re.sub(r"\s+", " ", value)
        if value and value not in items:
            items.append(value)
    if not items:
        return [original]
    return items[:MAX_SUBQUERIES]


def rewrite(question: str, count: int = MAX_SUBQUERIES, on_notice=None) -> list[str]:
    """把问题改写成若干子查询。失败时返回 [question]。

    这里**故意不检查** QUERY_REWRITE_ENABLED：「要不要改写」由调用方决定
    （rag.retrieve 会看全局配置，/api/retrieve 还能按请求覆盖）。
    之前在这里也拦一道，导致接口上传 rewrite=true 被短路回原问题，
    开关看起来完全不生效。
    """
    question = (question or "").strip()
    if not question:
        return []
    if not settings.llm.configured:
        logger.info("查询改写跳过：LLM_API_KEY 未配置")
        return [question]

    from .llm import client as llm

    try:
        result = llm.chat(
            [
                {"role": "system", "content": _REWRITE_SYSTEM},
                {"role": "user", "content": question},
            ],
            on_notice=on_notice,
            max_tokens=settings.llm.rewrite_max_tokens,
        )
    except Exception as error:  # noqa: BLE001 - 改写是增强项，失败就退回原问题
        logger.warning("查询改写失败，退回原问题：%s", error)
        return [question]

    items = parse_rewrites(result.get("content", ""), question)
    # 原问题永远保留在第一位：改写只是补充视角，不该丢掉用户的原话
    if question not in items:
        items = [question] + items
    return items[:count]


def describe() -> dict:
    return {
        "enabled": settings.query.rewrite_enabled,
        "max_subqueries": settings.query.max_subqueries,
        "available": bool(settings.llm.configured),
    }
