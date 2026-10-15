"""RAGAS 评测（步骤 6）。

一条样本的流程：
  问题 -> 走真实 RAG 链路（混合检索 + 拼上下文 + 生成）-> 拿到 回答 / 召回上下文
       -> 交给 RAGAS 打分

为什么要自己跑链路、而不是把已有答案喂进去：
评测的必须是**线上真正会走的那条路**，否则分数再高也不代表用户看到的效果。
所以这里直接调 `rag.retrieve` 与 `llm.stream_chat_until_content`，和 `/api/chat` 使用同一条代码路径。

RAGAS 需要一个 LLM 和一个 embedding：
  * LLM 用 sub-coco DeepSeek，备用使用独立智谱 GLM 连接
  * embedding 用硅基流动的 bge-m3（同样 OpenAI 兼容）
这样评测和线上用的是同一套模型，分数才有可比性。
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import threading
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import httpx

from ..config import settings
from ..llm import client as llm
from ..retrieval import context as context_builder
from . import dataset as dataset_module

logger = logging.getLogger(__name__)

# 评测用的一套参数组合：默认取线上配置，也允许一次性对比多组
DEFAULT_VARIANTS = [
    {"name": "当前配置", "hybrid": True, "rerank": None},
]
METRIC_NAMES = ("faithfulness", "answer_relevancy", "context_precision", "context_recall")


@dataclass
class VariantResult:
    """一组参数下的跑分。"""

    name: str
    params: dict
    scores: dict = field(default_factory=dict)
    per_case: list[dict] = field(default_factory=list)
    retrieval: dict = field(default_factory=dict)
    error: str = ""
    score_counts: dict = field(default_factory=dict)


@dataclass
class EvalReport:
    created_at: str
    dataset_count: int
    variants: list[VariantResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "created_at": self.created_at,
            "dataset_count": self.dataset_count,
            "notes": self.notes,
            "variants": [
                {
                    "name": v.name,
                    "params": v.params,
                    "scores": v.scores,
                    "score_counts": v.score_counts,
                    "retrieval": v.retrieval,
                    "error": v.error,
                    "per_case": v.per_case,
                }
                for v in self.variants
            ],
        }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _emit_progress(callback, **event) -> None:
    """Send a best-effort progress event without letting a UI callback break eval."""
    if callback is None:
        return
    try:
        callback(event)
    except Exception:  # noqa: BLE001 - progress sinks are observational only
        logger.warning("评测进度回调失败", exc_info=True)


def _normalise_score(value) -> float | None:
    """Convert a RAGAS cell to a JSON-safe score, treating NaN/inf as missing."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return round(number, 4)


def _score_summary(rows: list[dict]) -> tuple[dict, dict]:
    """Average only valid per-case scores and preserve the denominator."""
    scores, counts = {}, {}
    for metric in METRIC_NAMES:
        values = [_normalise_score((row.get("scores") or {}).get(metric)) for row in rows]
        valid = [value for value in values if value is not None]
        counts[metric] = len(valid)
        scores[metric] = round(sum(valid) / len(valid), 4) if valid else None
    return scores, counts


def _make_metrics(judge, embedder) -> list:
    """Each evaluation owns its metrics; RAGAS mutates their LLM bindings."""
    from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness

    return [
        Faithfulness(llm=judge),
        AnswerRelevancy(llm=judge, embeddings=embedder),
        ContextPrecision(llm=judge),
        ContextRecall(llm=judge),
    ]


def _answer(question: str, context_text: str) -> tuple[str, dict]:
    """走真实生成链路，攒完整答案再返回（评测不需要流式）。"""
    messages = [{"role": "user", "content": question}]
    from .. import rag as rag_module

    messages = rag_module.build_messages(question, context_text)
    chunks: list[str] = []
    reasoning_chars = 0
    usage: dict | None = None
    notices = []
    actual_model = None
    actual_provider = None
    attempts = []
    error_message = None
    try:
        for event in llm.stream_chat_until_content(messages, max_tokens=settings.eval.generation_max_tokens):
            actual_model = event.get("actual_model") or actual_model
            actual_provider = event.get("actual_provider") or actual_provider
            if event["type"] == "content":
                chunks.append(event["text"])
            elif event["type"] == "reasoning":
                reasoning_chars += len(event["text"])
            elif event["type"] == "done":
                usage = event.get("usage")
                attempts = event.get("attempts") or []
            elif event["type"] == "notice":
                notices.append(event.get("text") or "")
    except llm.LLMError as error:
        error_message = str(error)
        attempts = error.attempts or attempts
        actual_model = error.actual_model or actual_model
        actual_provider = error.actual_provider or actual_provider
    metadata = {"reasoning_chars": reasoning_chars, **(usage or {}), "usage_known": usage is not None,
                "actual_model": actual_model, "actual_provider": actual_provider,
                "notices": notices, "attempts": attempts}
    if error_message is not None:
        metadata["error"] = error_message
    return "".join(chunks).strip(), metadata


def _run_one(case: dataset_module.EvalCase, variant: dict) -> dict:
    """跑一条样本：检索 -> 生成 -> 记录，顺便算"命中率"。"""
    from .. import rag as rag_module

    found = rag_module.retrieve(
        case.question,
        top_k=variant.get("top_k"),
        use_hybrid=variant.get("hybrid", True),
        use_rerank=variant.get("rerank"),
        threshold=variant.get("threshold"),
        # 评测不追求「上下文越多越好」，压到预算内才能横向比参数，
        # 也顺带省掉一大截 prompt token（这台 Key 是按量限流的）。
        budget=variant.get("budget") or settings.eval.context_budget,
        # 步骤 7：A/B 对比要靠这个开关区分「改写关」与「改写开」两组
        use_rewrite=variant.get("rewrite"),
    )

    citations = found["citations"]
    contexts = [item.get("quote") or item.get("summary") or "" for item in citations]
    # 引用里没带正文时，退回用片段体（建上下文时保留过原文）
    contexts = [text for text in contexts if text.strip()]
    if not contexts:
        contexts = [
            block.split("内容：", 1)[-1].strip()
            for block in found["context_text"].split("\n\n")
            if "内容：" in block
        ]

    try:
        answer, usage = _answer(case.question, found["context_text"])
    except llm.LLMError as error:
        answer, usage = "", {"error": str(error)}

    # 命中判定：召回的片段里有没有覆盖期望的行号区间
    hit = False
    for citation in citations:
        if citation.get("doc_id") != case.doc_id:
            continue
        if citation.get("line_start", 0) <= case.line_end and case.line_start <= citation.get("line_end", 0):
            hit = True
            break

    return {
        "case_id": case.case_id,
        "question": case.question,
        "ground_truth": case.ground_truth,
        "answer": answer,
        "contexts": contexts,
        "expected": case.expected,
        "hit": hit,
        "top_doc": citations[0].get("doc_name") if citations else "",
        "diagnostics": found["diagnostics"],
        "usage": usage,
        "actual_model": usage.get("actual_model"),
        "notices": usage.get("notices") or [],
    }


def _run_cases(
    cases: list[dataset_module.EvalCase],
    variant: dict,
    workers: int,
    on_progress=None,
) -> list[dict]:
    """跑完一组样本。

    默认串行（workers=1）。并发只在显式调大时才用，而且每条前后补一个间隔——
    这台 Key 的限流是账户级的，并发不节流等于把两条请求同时撞死。
    """
    interval = max(0.0, settings.eval.request_interval)

    progress_lock = threading.Lock()
    completed = 0
    case_indices = {id(case): index for index, case in enumerate(cases, start=1)}

    def emit(case: dataset_module.EvalCase, status: str, completed_count: int) -> None:
        _emit_progress(
            on_progress,
            type="progress",
            phase="answer",
            status=status,
            case_id=case.case_id,
            index=case_indices.get(id(case), 0),
            completed=completed_count,
            total=len(cases),
            variant=variant.get("name") or "配置",
        )

    def guarded(case: dataset_module.EvalCase) -> dict:
        with progress_lock:
            current = completed
        emit(case, "started", current)
        try:
            return _run_one(case, variant)
        finally:
            if interval:
                time.sleep(interval)

    def on_finished(case: dataset_module.EvalCase) -> None:
        nonlocal completed
        with progress_lock:
            completed += 1
            current = completed
        emit(case, "completed", current)

    if workers <= 1:
        rows = []
        for index, case in enumerate(cases, start=1):
            logger.info("样本 %d/%d 开始：%s", index, len(cases), case.question)
            row = guarded(case)
            on_finished(case)
            logger.info(
                "样本 %d/%d 完成：命中=%s 答案=%d 字 上下文=%d 段%s",
                index, len(cases), row.get("hit"), len(row.get("answer") or ""),
                len(row.get("contexts") or []),
                "（调用失败：" + str((row.get("usage") or {}).get("error")) + "）" if (row.get("usage") or {}).get("error") else "",
            )
            rows.append(row)
        return rows

    rows: list[dict | None] = [None] * len(cases)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(guarded, case): (index, case) for index, case in enumerate(cases)}
        for future in as_completed(futures):
            index, case = futures[future]
            try:
                rows[index] = future.result()
            finally:
                on_finished(case)
    return [row for row in rows if row is not None]


# ---------------------------------------------------------------------------
# judge 节流。
#
# 这里踩过一个坑：智谱的限流在我们自己的 zhipu.py 里已经做了全局闸，但
# **RAGAS 的 judge 走的是它自带的 langchain OpenAI 客户端**，根本不经过那道闸。
# 于是 context_precision 这种「每个上下文判一次」的指标（10 个上下文 × 3 条
# 样本 = 约 30 次调用）会在几秒内把 Key 打穿，分数直接变 null。
#
# 所以这里再包一层，在 judge 每次真正发请求之前留出间隔。
# 同步（_generate）与异步（_agenerate）两条路都要包：RAGAS 内部走的是异步。
# ---------------------------------------------------------------------------
_judge_lock = threading.Lock()
_judge_next_slot = 0.0

def _judge_reserve(gap: float) -> float:
    """预约一个时间槽，返回还需要等多久。"""
    global _judge_next_slot
    with _judge_lock:
        now = time.monotonic()
        wait = max(0.0, _judge_next_slot - now)
        _judge_next_slot = max(now, _judge_next_slot) + gap
    return wait


def _make_throttled_chat_openai(interval: float, notices: list[str] | None = None,
                               fallback_cases: set[str] | None = None,
                               questions: dict[str, str] | None = None,
                               judge_attempts: list[dict] | None = None):
    """返回带独立 GLM 备用连接的 ChatOpenAI 子类；interval<=0 时仅关闭节流。"""
    from langchain_openai import ChatOpenAI

    notice_lock = threading.Lock()

    class ThrottledChatOpenAI(ChatOpenAI):
        """Throttle Judge calls and keep provider-specific fallback parameters."""

        def _safe_temperature(self) -> float:
            try:
                value = float(self.temperature)
            except (TypeError, ValueError):
                return settings.llm.temperature
            if value != value or value < 0:
                return settings.llm.temperature
            return value

        def _fallback(self):
            if not settings.llm.fallback_configured:
                raise RuntimeError("GLM 备用裁判未配置")
            if notices is not None:
                with notice_lock:
                    notices.append(f"DeepSeek 裁判暂不可用，已尝试切换至 {settings.llm.fallback_model}")
            return ChatOpenAI(model=settings.llm.fallback_model,
                              api_key=settings.llm.fallback_api_key,
                              base_url=settings.llm.fallback_base_url,
                              temperature=settings.llm.fallback_temperature,
                              max_tokens=settings.llm.fallback_max_tokens,
                              timeout=settings.llm.timeout, max_retries=0)

        def _mark_fallback_case(self, messages) -> None:
            if fallback_cases is None or questions is None:
                return
            prompt = " ".join(str(getattr(message, "content", "")) for message in messages)
            matched = [case_id for case_id, question in questions.items() if question and question in prompt]
            with notice_lock:
                fallback_cases.update(matched or {"__unattributed__"})

        def _record_result(self, result, messages, model: str, budget: int, provider: str) -> None:
            if judge_attempts is None:
                return
            prompt = " ".join(str(getattr(message, "content", "")) for message in messages)
            matched = [case_id for case_id, question in (questions or {}).items() if question and question in prompt]
            attempt = {"model": model, "provider": provider, "max_tokens": budget,
                       "case_ids": matched, "usage": (getattr(result, "llm_output", None) or {}).get("token_usage"),
                       "finish_reasons": [(generation.generation_info or {}).get("finish_reason")
                                          for generation in getattr(result, "generations", [])]}
            with notice_lock:
                judge_attempts.append(attempt)

        def _expanded_budget(self, result, params) -> int | None:
            if not any((generation.generation_info or {}).get("finish_reason") == "length"
                       for generation in getattr(result, "generations", [])):
                return None
            current = params.get("max_tokens") or self.max_tokens or settings.eval.judge_max_tokens
            expanded = min(current * 2, settings.llm.max_output_tokens)
            if expanded <= current:
                return None
            if notices is not None:
                with notice_lock:
                    notices.append(f"{self.model_name} 裁判达到 {current} token 输出预算，已用 {expanded} 重新判分一次")
            return expanded

        @staticmethod
        def _recoverable(error) -> bool:
            from openai import APIConnectionError, APITimeoutError

            if isinstance(error, (httpx.TransportError, APIConnectionError, APITimeoutError, ConnectionError)):
                return True
            status = getattr(error, "status_code", None)
            return isinstance(status, int) and (status == 429 or 500 <= status <= 599)

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            if interval > 0:
                wait = _judge_reserve(interval)
                if wait:
                    time.sleep(wait)
            params = {**kwargs, "temperature": self._safe_temperature()}
            try:
                result = super()._generate(messages, stop=stop, run_manager=run_manager, **params)
                self._record_result(result, messages, self.model_name,
                                    params.get("max_tokens") or self.max_tokens or settings.eval.judge_max_tokens,
                                    settings.llm.provider)
                expanded = self._expanded_budget(result, params)
                if expanded:
                    wait = _judge_reserve(interval) if interval > 0 else 0
                    if wait:
                        time.sleep(wait)
                    result = super()._generate(messages, stop=stop, run_manager=run_manager,
                                               **{**params, "max_tokens": expanded})
                    self._record_result(result, messages, self.model_name, expanded, settings.llm.provider)
                return result
            except Exception as error:
                if not self._recoverable(error):
                    raise
                result = self._fallback()._generate(messages, stop=stop, run_manager=run_manager,
                                                     **{**kwargs, "temperature": settings.llm.fallback_temperature,
                                                        "max_tokens": settings.llm.fallback_max_tokens})
                self._record_result(result, messages, settings.llm.fallback_model,
                                    settings.llm.fallback_max_tokens, "zhipu")
                if notices is not None:
                    with notice_lock:
                        notices.append(f"部分判分使用了 {settings.llm.fallback_model}")
                self._mark_fallback_case(messages)
                return result

        async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            if interval > 0:
                wait = _judge_reserve(interval)
                if wait:
                    await asyncio.sleep(wait)
            params = {**kwargs, "temperature": self._safe_temperature()}
            try:
                result = await super()._agenerate(messages, stop=stop, run_manager=run_manager, **params)
                self._record_result(result, messages, self.model_name,
                                    params.get("max_tokens") or self.max_tokens or settings.eval.judge_max_tokens,
                                    settings.llm.provider)
                expanded = self._expanded_budget(result, params)
                if expanded:
                    wait = _judge_reserve(interval) if interval > 0 else 0
                    if wait:
                        await asyncio.sleep(wait)
                    result = await super()._agenerate(messages, stop=stop, run_manager=run_manager,
                                                      **{**params, "max_tokens": expanded})
                    self._record_result(result, messages, self.model_name, expanded, settings.llm.provider)
                return result
            except Exception as error:
                if not self._recoverable(error):
                    raise
                result = await self._fallback()._agenerate(messages, stop=stop, run_manager=run_manager,
                                                            **{**kwargs, "temperature": settings.llm.fallback_temperature,
                                                               "max_tokens": settings.llm.fallback_max_tokens})
                self._record_result(result, messages, settings.llm.fallback_model,
                                    settings.llm.fallback_max_tokens, "zhipu")
                if notices is not None:
                    with notice_lock:
                        notices.append(f"部分判分使用了 {settings.llm.fallback_model}")
                self._mark_fallback_case(messages)
                return result

    return ThrottledChatOpenAI


def _ragas_scores(rows: list[dict], notices: list[str] | None = None,
                  fallback_cases: set[str] | None = None, on_progress=None) -> tuple[dict, str]:
    """逐样本交给 RAGAS 打分。返回 (分数, 错误信息)。"""
    try:
        from langchain_openai import OpenAIEmbeddings
        from ragas import EvaluationDataset, SingleTurnSample, evaluate
        from ragas.embeddings import LangchainEmbeddingsWrapper
        from ragas.llms import LangchainLLMWrapper
        from ragas.metrics import AnswerRelevancy, ContextPrecision, ContextRecall, Faithfulness
        from ragas.run_config import RunConfig
    except ImportError as error:
        return {}, f"RAGAS 依赖导入失败：{error}"

    questions = {row["case_id"]: row["question"] for row in rows if row.get("case_id")}
    judge_attempts: list[dict] = []
    ThrottledChatOpenAI = _make_throttled_chat_openai(settings.eval.judge_interval, notices,
                                                      fallback_cases, questions, judge_attempts)

    def make_judge():
        """Create one judge client per sample/evaluate call.

        RAGAS executes each ``evaluate`` call with ``asyncio.run``.  The
        async OpenAI client created by LangChain is bound to that event loop;
        reusing it after the loop closes makes the next sample fail with
        ``Event loop is closed``.  Keeping the client lifetime inside one
        sample also prevents the embedding client from crossing loops.
        """
        return LangchainLLMWrapper(
            ThrottledChatOpenAI(
                model=settings.eval.judge_model,
                api_key=settings.llm.api_key,
                base_url=settings.llm.base_url,
                temperature=settings.llm.temperature,
                # judge 也是推理模型：给不够正文就空，RAGAS 解析不出分只会得到 null。
                max_tokens=settings.eval.judge_max_tokens,
                timeout=settings.llm.timeout,
                # 关键：必须关掉 OpenAI SDK 自带的重试。
                # 它是在 create() 内部循环发请求，**不经过我们的 _generate/_agenerate 包装**，
                # 所以每次重试都是无间隔猛发——日志里连续几十个 429 就是这么来的。
                # 关掉之后，重试交给 RAGAS，而 RAGAS 的重试会重新走节流包装。
                max_retries=0,
            )
        )

    def make_embedder():
        return LangchainEmbeddingsWrapper(
            OpenAIEmbeddings(
                model=settings.siliconflow.embedding_model,
                api_key=settings.siliconflow.api_key,
                base_url=settings.siliconflow.base_url,
                check_embedding_ctx_length=False,
            )
        )

    logger.info(
        "开始 RAGAS 打分：%d 条样本，%d 个指标，judge=%s 并发=%d 间隔=%.1fs",
        len(rows), len(METRIC_NAMES), settings.eval.judge_model,
        settings.eval.judge_max_workers, settings.eval.judge_interval,
    )
    errors = []
    for index, row in enumerate(rows):
        _emit_progress(on_progress, type="progress", phase="judge", status="started",
                       case_id=row["case_id"], index=index + 1, completed=index, total=len(rows))
        attempt_start = len(judge_attempts)
        row["scores"] = {metric: None for metric in METRIC_NAMES}
        try:
            # Do not move these constructions above the loop.  RAGAS's
            # evaluate() creates and closes an event loop per invocation, so
            # its async HTTP clients must not be shared by two samples.
            judge = make_judge()
            embedder = make_embedder()
            # RAGAS 0.2 mutates metric instances during evaluate() and clears
            # bindings it supplied. Never share its module-level singletons.
            result = evaluate(
                dataset=EvaluationDataset(samples=[SingleTurnSample(
                    user_input=row["question"],
                    response=row["answer"] or "（模型没有返回内容）",
                    retrieved_contexts=row["contexts"] or ["（没有召回任何片段）"],
                    reference=row["ground_truth"],
                )]),
                metrics=_make_metrics(judge, embedder),
                llm=judge,
                embeddings=embedder,
                raise_exceptions=False,
                show_progress=False,
                run_config=RunConfig(
                    max_workers=max(1, settings.eval.judge_max_workers),
                    max_retries=1,
                    max_wait=60,
                    timeout=300,
                ),
            )
            frame = result.to_pandas()
            if len(frame):
                row["scores"] = {
                    metric: _normalise_score(frame[metric].iloc[0]) if metric in frame else None
                    for metric in METRIC_NAMES
                }
        except Exception as error:  # noqa: BLE001 - keep later samples and partial scores
            logger.exception("RAGAS 样本 %s 打分失败", row["case_id"])
            message = f"RAGAS 打分失败（{row['case_id']}）：{type(error).__name__}: {error}"
            row["judge_error"] = message
            errors.append(message)
        # Every attempt between these boundaries belongs to this sample, even
        # when an individual metric prompt omits the original question.
        row["judge_attempts"] = judge_attempts[attempt_start:]
        scores, counts = _score_summary(rows[:index + 1])
        _emit_progress(on_progress, type="progress", phase="judge", status="completed",
                       case_id=row["case_id"], index=index + 1, completed=index + 1,
                       total=len(rows), scores=dict(row["scores"]), running_scores=scores,
                       score_counts=counts, error=row.get("judge_error"))

    logger.info("RAGAS 打分完成")
    scores, _ = _score_summary(rows)
    return scores, "；".join(errors)


def _variant_metrics(rows: list[dict]) -> dict:
    """不依赖 RAGAS 也能算的硬指标。RAGAS 挂了这些还在，方便对比参数。"""
    if not rows:
        return {}
    hits = sum(1 for row in rows if row["hit"])
    return {
        "case_count": len(rows),
        "hit_rate": round(hits / len(rows), 4),
        "avg_contexts": round(sum(len(row["contexts"]) for row in rows) / len(rows), 2),
        "empty_answers": sum(1 for row in rows if not row["answer"]),
        "failed_answers": sum(1 for row in rows if (row.get("usage") or {}).get("error")),
    }


def run(
    variants: list[dict] | None = None,
    limit: int | None = None,
    with_ragas: bool = True,
    max_workers: int | None = None,
    on_progress=None,
) -> EvalReport:
    """跑一轮评测。`variants` 可以传多组参数做对比。"""
    saved = dataset_module.load_dataset()
    if saved is None or not saved.cases:
        raise RuntimeError("还没有评测集，先跑 POST /api/eval/dataset")

    cases = saved.cases[: limit or len(saved.cases)]
    plans = variants or DEFAULT_VARIANTS
    workers = max(1, max_workers or settings.eval.max_workers)

    report = EvalReport(created_at=_now(), dataset_count=len(cases))
    if len(cases) < len(saved.cases):
        report.notes.append(f"只跑了前 {len(cases)} 条（数据集共 {len(saved.cases)} 条）")

    for variant in plans:
        name = str(variant.get("name") or "配置")
        result = VariantResult(name=name, params={k: v for k, v in variant.items() if k != "name"})

        def variant_progress(event: dict) -> None:
            _emit_progress(on_progress, **{**event, "variant": name})

        try:
            rows = (_run_cases(cases, variant, workers, on_progress=variant_progress)
                    if on_progress is not None else _run_cases(cases, variant, workers))
        except Exception as error:  # noqa: BLE001
            result.error = f"跑样本失败：{type(error).__name__}: {error}"
            report.variants.append(result)
            continue

        result.per_case = rows
        result.retrieval = _variant_metrics(rows)
        for row in rows:
            report.notes.extend(row.get("notices") or [])
            report.notes.extend((row.get("diagnostics") or {}).get("notes") or [])

        if with_ragas:
            judge_notices: list[str] = []
            fallback_cases: set[str] = set()
            scores, error = (_ragas_scores(rows, judge_notices, fallback_cases, on_progress=variant_progress)
                             if on_progress is not None else _ragas_scores(rows, judge_notices, fallback_cases))
            if judge_notices:
                report.notes.extend(judge_notices)
                successful = sum("部分判分使用了" in note for note in judge_notices)
                if successful:
                    report.notes.append(f"{name}：部分判分使用了 GLM（{successful} 次成功）")
                for row in rows:
                    models = sorted({attempt["model"] for attempt in row.get("judge_attempts", [])})
                    row["judge_model"] = (
                        " / ".join(models) if models else
                        "混合：DeepSeek / GLM" if row["case_id"] in fallback_cases else
                        "未知：GLM 调用未能归属样本" if "__unattributed__" in fallback_cases else
                        settings.eval.judge_model
                    )
            else:
                for row in rows:
                    row["judge_model"] = settings.eval.judge_model
            result.scores = scores
            _, result.score_counts = _score_summary(rows)
            if error:
                result.error = error
                report.notes.append(f"{name}：{error}")
            # 四项里缺哪项，说清楚：全 null 和「个别 null」是两种问题
            missing = [key for key in METRIC_NAMES if scores.get(key) is None]
            if missing:
                report.notes.append(f"{name}：这几项没打出分（原因待查）：{'、'.join(missing)}")
            for row in rows:
                incomplete = [key for key in METRIC_NAMES
                              if (row.get("scores") or {}).get(key) is None]
                if incomplete:
                    report.notes.append(f"{name} / {row['case_id']}：未完成判分：{'、'.join(incomplete)}")

        report.variants.append(result)

    return report


def _report_path() -> Path:
    return settings.eval.report_dir / "latest.json"


def _write_json_atomic(path: Path, payload: dict) -> None:
    """Write one report file without exposing a partially written JSON document."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            try:
                temporary.unlink()
            except OSError:
                logger.warning("清理评测报告临时文件失败：%s", temporary)


def save_report(report: EvalReport) -> Path:
    path = _report_path()
    payload = report.to_dict()
    _write_json_atomic(path, payload)

    # 顺手留一份带时间戳的，方便看历史曲线
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    history = settings.eval.report_dir / f"report-{stamp}.json"
    try:
        _write_json_atomic(history, payload)
    except OSError:
        # latest.json is the authoritative result used by the API. A history
        # archive failure must not turn an already saved report into a failed
        # SSE request or make the UI believe scoring did not finish.
        logger.warning("评测历史报告写入失败：%s", history, exc_info=True)
    return path


def load_latest() -> dict | None:
    path = _report_path()
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def history(limit: int = 10) -> list[dict]:
    """历史跑分：只读时间戳与总分，够画曲线就行。"""
    directory = settings.eval.report_dir
    if not directory.exists():
        return []
    files = sorted(directory.glob("report-*.json"), reverse=True)[:limit]
    rows = []
    for path in files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows.append(
            {
                "file": path.name,
                "created_at": payload.get("created_at", ""),
                "dataset_count": payload.get("dataset_count", 0),
                "variants": [
                    {"name": v.get("name"), "scores": v.get("scores") or {},
                     "score_counts": v.get("score_counts") or {}, "retrieval": v.get("retrieval") or {}}
                    for v in payload.get("variants", [])
                ],
            }
        )
    return rows


def describe() -> dict:
    latest = load_latest()
    return {
        "dataset": dataset_module.describe(),
        "report_dir": str(settings.eval.report_dir),
        "max_workers": settings.eval.max_workers,
        "has_report": latest is not None,
        "last_report_at": (latest or {}).get("created_at", ""),
        "metrics": ["faithfulness", "answer_relevancy", "context_precision", "context_recall"],
        "context_format": context_builder.describe()["format"],
    }
