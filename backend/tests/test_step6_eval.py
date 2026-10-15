"""步骤 6 的单元测试：出题解析、评测集落盘、命中判定、硬指标、报告落盘、接口。

原则：不联网。
  * RAG 链路与模型调用全部用假的（monkeypatch）
  * 只验证"我们的逻辑对不对"，不验证"模型答得好不好"
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------- 出题解析 ----------

def test_parse_question_handles_plain_json_and_code_fence():
    from app.eval import dataset as dataset_module

    question, answer = dataset_module._parse_question('{"question": "限额是多少？", "answer": "600"}')
    assert question == "限额是多少？"
    assert answer == "600"

    fenced = '```json\n{"question": "窗口多长？", "answer": "60 秒"}\n```'
    assert dataset_module._parse_question(fenced) == ("窗口多长？", "60 秒")


def test_parse_question_rejects_garbage_without_json():
    from app.eval import dataset as dataset_module

    # 模型没按格式输出时不能被当成一条题，否则坏题会污染所有指标
    assert dataset_module._parse_question("我认为这个片段没有可问的事实。") == ("", "")
    assert dataset_module._parse_question('{"question": "", "answer": ""}') == ("", "")
    assert dataset_module._parse_question("") == ("", "")


# ---------- 评测集落盘 ----------

@pytest.fixture()
def eval_paths(scratch_dir, monkeypatch):
    """把评测集与报告目录指到临时目录，别动真实开发数据。"""
    from app import config as config_module
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    patched = replace(
        config_module.settings,
        eval=replace(
            config_module.settings.eval,
            dataset_path=scratch_dir / "eval" / "dataset.json",
            report_dir=scratch_dir / "eval",
        ),
    )
    for module in (dataset_module, ragas_eval):
        monkeypatch.setattr(module, "settings", patched)
    monkeypatch.setattr(config_module, "settings", patched, raising=False)
    return patched


def test_dataset_roundtrip_persists_cases(eval_paths):
    from app.eval import dataset as dataset_module

    built = dataset_module.EvalDataset(
        name="corpus-eval",
        created_at="2026-10-09T00:00:00+00:00",
        cases=[
            dataset_module.EvalCase(
                case_id="case-001",
                question="默认限额是多少？",
                ground_truth="600",
                doc_id="d1",
                doc_name="quota.py",
                line_start=12,
                line_end=13,
                source_text="DEFAULT_LIMIT = 600",
            )
        ],
    )
    path = dataset_module.save_dataset(built)
    assert Path(path).exists()

    loaded = dataset_module.load_dataset()
    assert loaded is not None
    assert loaded.cases[0].case_id == "case-001"
    assert loaded.cases[0].expected["line_start"] == 12
    assert dataset_module.describe()["count"] == 1


def test_load_dataset_returns_none_when_absent(eval_paths):
    from app.eval import dataset as dataset_module

    assert dataset_module.load_dataset() is None


# ---------- 命中判定 ----------

def _case(dataset_module):
    return dataset_module.EvalCase(
        case_id="case-001",
        question="默认限额是多少？",
        ground_truth="600",
        doc_id="d1",
        doc_name="quota.py",
        line_start=12,
        line_end=14,
    )


def _stub_retrieve(monkeypatch, citations, context_text="内容：\nDEFAULT_LIMIT = 600"):
    from app import rag

    monkeypatch.setattr(
        rag,
        "retrieve",
        lambda question, **kwargs: {
            "query": question,
            "hits": [],
            "citations": citations,
            "context_text": context_text,
            "diagnostics": {"returned": len(citations)},
        },
    )


def _stub_answer(monkeypatch, text="默认限额是 600。【片段 1】", usage=None):
    from app.llm import client as zhipu

    def fake_stream(messages, **overrides):
        yield {"type": "reasoning", "text": "想了想"}
        yield {"type": "content", "text": text}
        yield {"type": "done", "finish_reason": "stop", "usage": usage or {"completion_tokens": 10}}

    monkeypatch.setattr(zhipu, "stream_chat", fake_stream)


def test_run_one_marks_hit_on_line_overlap(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    _stub_retrieve(
        monkeypatch,
        [{"doc_id": "d1", "doc_name": "quota.py", "line_start": 10, "line_end": 15, "quote": "DEFAULT_LIMIT = 600"}],
    )
    _stub_answer(monkeypatch)

    row = ragas_eval._run_one(_case(dataset_module), {"hybrid": True})
    assert row["hit"] is True
    assert row["contexts"] == ["DEFAULT_LIMIT = 600"]
    assert row["answer"].startswith("默认限额是 600")


def test_run_one_misses_when_lines_do_not_overlap(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    _stub_retrieve(
        monkeypatch,
        [{"doc_id": "d1", "doc_name": "quota.py", "line_start": 30, "line_end": 40, "quote": "别的代码"}],
    )
    _stub_answer(monkeypatch)

    assert ragas_eval._run_one(_case(dataset_module), {})["hit"] is False


def test_run_one_misses_when_document_differs(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    _stub_retrieve(
        monkeypatch,
        [{"doc_id": "other", "doc_name": "x.py", "line_start": 12, "line_end": 14, "quote": "同行的其他文档"}],
    )
    _stub_answer(monkeypatch)

    assert ragas_eval._run_one(_case(dataset_module), {})["hit"] is False


def test_run_one_falls_back_to_snippet_body_when_quote_missing(eval_paths, monkeypatch):
    """老数据里 citations 没有 quote，评测不能因此拿到空上下文。"""
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    _stub_retrieve(
        monkeypatch,
        [{"doc_id": "d1", "doc_name": "quota.py", "line_start": 12, "line_end": 14}],
        context_text="[片段 1] quota.py 行 12-14\n内容：\nDEFAULT_LIMIT = 600",
    )
    _stub_answer(monkeypatch)

    row = ragas_eval._run_one(_case(dataset_module), {})
    assert row["contexts"] == ["DEFAULT_LIMIT = 600"]


def test_failed_generation_preserves_partial_answer_notice_and_attempts_in_report(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval
    from app.llm import client as llm

    _stub_retrieve(monkeypatch, [])
    attempts = [{"model": "deepseek-v4-flash", "provider": "sub-coco", "usage": {"total_tokens": 80}},
                {"model": "glm-4-flash", "provider": "zhipu", "usage": None, "error": "断流"}]
    notice = "已切换至 glm-4-flash，正在重新生成"

    def failing_stream(*args, **kwargs):
        yield {"type": "notice", "text": notice}
        yield {"type": "content", "text": "部分答案", "actual_model": "glm-4-flash", "actual_provider": "zhipu"}
        raise llm.LLMError("GLM 备用流式调用失败", attempts=attempts,
                           actual_model="glm-4-flash", actual_provider="zhipu")

    monkeypatch.setattr(llm, "stream_chat_until_content", failing_stream)
    saved = dataset_module.EvalDataset(name="test", created_at="now", cases=[_case(dataset_module)])
    monkeypatch.setattr(dataset_module, "load_dataset", lambda: saved)
    report = ragas_eval.run(with_ragas=False)
    row = report.variants[0].per_case[0]
    assert row["answer"] == "部分答案"
    assert row["notices"] == [notice] and notice in report.notes
    assert row["actual_model"] == "glm-4-flash"
    assert row["usage"]["attempts"] == attempts
    assert row["usage"]["usage_known"] is False
    assert row["usage"]["error"] == "GLM 备用流式调用失败"
    assert report.variants[0].retrieval["failed_answers"] == 1
    ragas_eval.save_report(report)
    assert ragas_eval.load_latest()["variants"][0]["per_case"][0] == row


def test_successful_eval_answer_preserves_separate_usage_records(monkeypatch):
    from app.eval import ragas_eval
    from app.llm import client as llm

    attempts = [{"model": "deepseek-v4-flash", "usage": {"total_tokens": 80}},
                {"model": "glm-4-flash", "usage": {"total_tokens": 5}}]

    def stream(*args, **kwargs):
        yield {"type": "content", "text": "ok", "actual_model": "glm-4-flash"}
        yield {"type": "done", "actual_model": "glm-4-flash", "usage": {"total_tokens": 5}, "attempts": attempts}

    monkeypatch.setattr(llm, "stream_chat_until_content", stream)
    answer, metadata = ragas_eval._answer("q", "ctx")
    assert answer == "ok" and metadata["total_tokens"] == 5
    assert metadata["attempts"] == attempts and metadata["usage_known"] is True


# ---------- 硬指标 ----------

def test_variant_metrics_counts_empty_and_failed():
    from app.eval import ragas_eval

    rows = [
        {"hit": True, "answer": "有答案", "contexts": [1, 2], "usage": {}},
        {"hit": False, "answer": "", "contexts": [1], "usage": {}},
        {"hit": False, "answer": "", "contexts": [], "usage": {"error": "429"}},
    ]
    metrics = ragas_eval._variant_metrics(rows)

    assert metrics["case_count"] == 3
    assert metrics["hit_rate"] == round(1 / 3, 4)
    assert metrics["avg_contexts"] == 1.0
    assert metrics["empty_answers"] == 2
    assert metrics["failed_answers"] == 1


def test_variant_metrics_empty_input():
    from app.eval import ragas_eval

    assert ragas_eval._variant_metrics([]) == {}


# ---------- 串行与节流 ----------

def test_run_cases_serial_sleeps_between_requests(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    # settings 是 frozen dataclass，只能整个换掉，不能改字段
    monkeypatch.setattr(
        ragas_eval,
        "settings",
        replace(eval_paths, eval=replace(eval_paths.eval, request_interval=1.5)),
    )

    slept: list[float] = []
    monkeypatch.setattr(ragas_eval, "time", SimpleNamespace(sleep=slept.append))

    order: list[str] = []
    monkeypatch.setattr(
        ragas_eval, "_run_one", lambda case, variant: order.append(case.case_id) or {"case_id": case.case_id}
    )

    cases = [
        dataset_module.EvalCase(case_id=f"case-{i:03d}", question="q", ground_truth="a", doc_id="d", doc_name="n", line_start=1, line_end=2)
        for i in (1, 2, 3)
    ]

    rows = ragas_eval._run_cases(cases, {}, workers=1)

    assert [row["case_id"] for row in rows] == ["case-001", "case-002", "case-003"]
    assert order == ["case-001", "case-002", "case-003"], "默认必须串行，并发会撞账户级限流"
    assert slept == [1.5, 1.5, 1.5]


# ---------- 报告落盘与历史 ----------

def test_report_roundtrip_and_history(eval_paths):
    from app.eval import ragas_eval

    report = ragas_eval.EvalReport(created_at="2026-10-09T10:00:00+00:00", dataset_count=2)
    report.variants.append(
        ragas_eval.VariantResult(
            name="当前配置",
            params={"hybrid": True},
            scores={"faithfulness": 0.8, "context_recall": 1.0},
            retrieval={"hit_rate": 0.5},
        )
    )
    ragas_eval.save_report(report)

    latest = ragas_eval.load_latest()
    assert latest is not None
    assert latest["variants"][0]["scores"]["faithfulness"] == 0.8

    items = ragas_eval.history()
    assert len(items) == 1
    assert items[0]["dataset_count"] == 2
    assert items[0]["variants"][0]["scores"]["context_recall"] == 1.0


def test_report_latest_survives_history_archive_failure(eval_paths, monkeypatch):
    from app.eval import ragas_eval

    report = ragas_eval.EvalReport(created_at="2026-10-09T10:00:00+00:00", dataset_count=1)
    calls = []
    original = ragas_eval._write_json_atomic

    def write(path, payload):
        calls.append(path.name)
        if len(calls) == 2:
            raise OSError("history volume unavailable")
        return original(path, payload)

    monkeypatch.setattr(ragas_eval, "_write_json_atomic", write)
    path = ragas_eval.save_report(report)

    assert path.name == "latest.json"
    assert ragas_eval.load_latest()["dataset_count"] == 1
    assert calls[0] == "latest.json"
    assert calls[1].startswith("report-")


def test_load_latest_handles_corrupt_file(eval_paths):
    from app.eval import ragas_eval

    path = eval_paths.eval.report_dir / "latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 JSON", encoding="utf-8")

    assert ragas_eval.load_latest() is None


# ---------- 追踪 ----------

def test_trace_records_spans_and_totals():
    from app import tracing

    trace = tracing.new_trace("chat", question="你好")
    with trace.span("检索", top_k=12) as span:
        span.fields.update({"returned": 3})
    with trace.span("生成", model="glm-4.7-flash"):
        pass

    payload = trace.to_dict()
    assert [item["name"] for item in payload["spans"]] == ["检索", "生成"]
    assert payload["spans"][0]["fields"]["returned"] == 3
    assert payload["total_ms"] >= 0
    assert isinstance(payload["langsmith"], bool)


def test_trace_span_records_error_and_reraises():
    from app import tracing

    trace = tracing.new_trace("chat")
    with pytest.raises(ValueError):
        with trace.span("检索"):
            raise ValueError("检索炸了")

    assert "ValueError" in trace.spans[0].error


# ---------- 接口 ----------

def test_eval_status_endpoint(client, eval_paths):
    response = client.get("/api/eval")
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["dataset"]["exists"] is False
    assert "faithfulness" in payload["metrics"]
    assert payload["config"]["max_workers"] >= 1


def test_dataset_endpoint_returns_generated_cases(client, eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module

    built = dataset_module.EvalDataset(
        name="corpus-eval",
        created_at="2026-10-09T00:00:00+00:00",
        cases=[
            dataset_module.EvalCase(
                case_id="case-001", question="限额？", ground_truth="600",
                doc_id="d1", doc_name="quota.py", line_start=12, line_end=13,
            )
        ],
    )
    monkeypatch.setattr(dataset_module, "generate", lambda **kwargs: built)

    response = client.post("/api/eval/dataset", json={"limit": 1})
    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["cases"][0]["expected"]["doc_name"] == "quota.py"


def test_dataset_endpoint_reports_missing_model(client, eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module

    def boom(**kwargs):
        raise RuntimeError("一条题都没出出来")

    monkeypatch.setattr(dataset_module, "generate", boom)
    response = client.post("/api/eval/dataset", json={})
    assert response.status_code == 409
    assert "一条题都没出出来" in response.get_json()["error"]


def test_latest_endpoint_404_before_any_run(client, eval_paths):
    assert client.get("/api/eval/latest").status_code == 404


def test_run_endpoint_saves_report(client, eval_paths, monkeypatch):
    from app.eval import ragas_eval

    report = ragas_eval.EvalReport(created_at="2026-10-09T10:00:00+00:00", dataset_count=1)
    report.variants.append(ragas_eval.VariantResult(name="当前配置", params={}, scores={"faithfulness": 0.5}))
    monkeypatch.setattr(ragas_eval, "run", lambda **kwargs: report)

    response = client.post("/api/eval/run", json={"limit": 1})
    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert ragas_eval.load_latest()["variants"][0]["scores"]["faithfulness"] == 0.5


def test_run_endpoint_requires_dataset(client, eval_paths, monkeypatch):
    from app.eval import ragas_eval

    def boom(**kwargs):
        raise RuntimeError("还没有评测集，先跑 POST /api/eval/dataset")

    monkeypatch.setattr(ragas_eval, "run", boom)
    assert client.post("/api/eval/run", json={}).status_code == 409


def test_report_flags_per_case_missing_scores_and_does_not_call_budget_retry_glm(eval_paths, monkeypatch):
    from app.eval import dataset as dataset_module
    from app.eval import ragas_eval

    saved = dataset_module.EvalDataset(name="test", created_at="now", cases=[_case(dataset_module)])
    monkeypatch.setattr(dataset_module, "load_dataset", lambda: saved)
    row = {"case_id": "case-001", "answer": "answer", "contexts": ["context"], "hit": True,
           "usage": {}, "question": "q", "notices": []}
    monkeypatch.setattr(ragas_eval, "_run_cases", lambda *args: [row])
    def score(rows, notices, fallback_cases):
        notices.append("deepseek-v4-flash 裁判达到 8192 token 输出预算，已用 16384 重新判分一次")
        rows[0]["scores"] = {"faithfulness": None, "answer_relevancy": 1.0}
        rows[0]["judge_attempts"] = [{"model": "deepseek-v4-flash"}]
        return {"faithfulness": 0.8, "answer_relevancy": 1.0, "context_precision": 1.0, "context_recall": 1.0}, ""
    monkeypatch.setattr(ragas_eval, "_ragas_scores", score)
    report = ragas_eval.run()
    assert row["judge_model"] == "deepseek-v4-flash"
    assert any("case-001" in note and "faithfulness" in note for note in report.notes)
    assert not any("部分判分使用了 GLM" in note for note in report.notes)
