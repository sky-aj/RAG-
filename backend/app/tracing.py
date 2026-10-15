"""链路追踪（步骤 6）。

做两件事：
  1. 把「检索 → 拼上下文 → 生成」每一段的耗时与关键参数记下来，
     即使没配 LangSmith 也能在接口返回值里看到，页面直接展示。
  2. 配了 LangSmith 就把同一条链路推上去，能在网页上按时间回放。

为什么不用 LangSmith 的 traceable 装饰器包住所有函数：
装饰器要求 LangSmith 环境变量在**导入时**就设好，而我们的配置是运行时读的；
而且链路里有个生成器（流式对话），装饰器对生成器的耗时统计是错的
（它只算"创建生成器"那一刻）。所以这里自己写 span，
只在真正要上报时调 langsmith，控制权在自己手里。
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .config import settings

logger = logging.getLogger(__name__)


@dataclass
class Span:
    """链路里的一段。"""

    name: str
    started_at: float
    ended_at: float = 0.0
    fields: dict = field(default_factory=dict)
    error: str = ""

    @property
    def ms(self) -> float:
        end = self.ended_at or time.perf_counter()
        return round((end - self.started_at) * 1000, 1)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "ms": self.ms,
            "fields": self.fields,
            "error": self.error,
        }


@dataclass
class Trace:
    """一次完整请求。"""

    name: str
    run_id: str
    spans: list[Span] = field(default_factory=list)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    inputs: dict = field(default_factory=dict)
    outputs: dict = field(default_factory=dict)

    @contextmanager
    def span(self, name: str, **fields):
        """with trace.span("检索", top_k=12) as s: ..."""
        item = Span(name=name, started_at=time.perf_counter(), fields=dict(fields))
        self.spans.append(item)
        try:
            yield item
        except Exception as error:  # noqa: BLE001 - 记下来再抛，不能吞异常
            item.error = f"{type(error).__name__}: {error}"
            item.ended_at = time.perf_counter()
            raise
        else:
            item.ended_at = time.perf_counter()

    def set(self, **fields) -> None:
        """给最近一个 span 补字段（例如检索跑完才知道命中几条）。"""
        if self.spans:
            self.spans[-1].fields.update(fields)

    @property
    def total_ms(self) -> float:
        if not self.spans:
            return 0.0
        return round(sum(span.ms for span in self.spans), 1)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "total_ms": self.total_ms,
            "langsmith": settings.eval.langsmith_enabled,
            "spans": [span.to_dict() for span in self.spans],
        }


def new_trace(name: str, **inputs) -> Trace:
    run_id = f"{int(time.time() * 1000):x}-{abs(hash((name, inputs.get('question', '')))) % 100000:05d}"
    return Trace(name=name, run_id=run_id, inputs=inputs)


def report(trace: Trace, outputs: dict | None = None) -> None:
    """上报到 LangSmith。没配或失败都只记日志，绝不影响主流程。"""
    trace.outputs = outputs or {}
    if not settings.eval.langsmith_enabled:
        return
    try:
        from langsmith import Client

        client = Client(api_key=settings.eval.langsmith_api_key)
        client.create_run(
            name=trace.name,
            run_type="chain",
            inputs={**trace.inputs, "spans_ms": {s.name: s.ms for s in trace.spans}},
            outputs={**trace.outputs, "total_ms": trace.total_ms},
            project_name=settings.eval.langsmith_project,
            extra={"spans": [span.to_dict() for span in trace.spans]},
        )
    except Exception as error:  # noqa: BLE001 - 追踪坏了不能影响回答
        logger.warning("LangSmith 上报失败（不影响本次请求）：%s", error)


def describe() -> dict:
    return {
        "langsmith_enabled": settings.eval.langsmith_enabled,
        "project": settings.eval.langsmith_project,
        "note": "没配 LangSmith 时，耗时明细仍会随接口返回，页面照常展示",
    }
