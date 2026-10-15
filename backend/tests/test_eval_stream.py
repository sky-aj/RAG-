"""Offline tests for incremental evaluation, persistence, and request exclusion."""
from __future__ import annotations

import json
import threading
from dataclasses import replace
from pathlib import Path

import pytest


@pytest.fixture()
def stream_paths(scratch_dir, monkeypatch):
    from app import config
    from app.api import eval as eval_api
    from app.eval import dataset, ragas_eval

    patched = replace(
        config.settings,
        llm=replace(config.settings.llm, api_key="stream-test-key"),
        eval=replace(config.settings.eval, dataset_path=scratch_dir / "dataset.json",
                     report_dir=scratch_dir / "reports"),
    )
    for module in (eval_api, dataset, ragas_eval):
        monkeypatch.setattr(module, "settings", patched)
    assert not eval_api._operation_lock.locked()
    yield patched
    assert not eval_api._operation_lock.locked()


def _built():
    from app.eval import dataset

    return dataset.EvalDataset(
        name="stream-test", created_at="2026-10-13T00:00:00+00:00",
        cases=[dataset.EvalCase(case_id="case-001", question="限额？", ground_truth="600",
                                doc_id="d1", doc_name="quota.py", line_start=1, line_end=2)],
    )


def _events(response) -> list[dict]:
    return [json.loads(line[6:]) for line in response.get_data(as_text=True).splitlines()
            if line.startswith("data: ")]


def test_generation_reports_skips_failures_and_new_questions(stream_paths, monkeypatch):
    from app.eval import dataset

    records = [{"text": "片段事实" * 40, "chunk_id": f"chunk-{index}", "doc_id": "d1"}
               for index in range(5)]
    monkeypatch.setattr(dataset, "_candidates", lambda *args: records)
    responses = iter([
        "not json", dataset.llm.LLMError("HTTP 503"),
        '{"question":"限额？","answer":"600"}',
        '{"question":"限额？","answer":"600"}',
        '{"question":"窗口？","answer":"60 秒"}',
    ])

    def chat(*args, **kwargs):
        item = next(responses)
        if isinstance(item, Exception):
            raise item
        return {"content": item, "actual_model": "deepseek-v4-flash"}

    monkeypatch.setattr(dataset.llm, "chat", chat)
    events = []
    result = dataset.generate(limit=2, on_progress=events.append)
    questions = [event for event in events if event["type"] == "question"]
    assert len(result.cases) == 2
    assert [event["case"]["question"] for event in questions] == ["限额？", "窗口？"]
    assert [event["completed"] for event in questions] == [1, 2]
    assert questions[-1]["candidate_completed"] == 5
    assert questions[-1]["total"] == 2
    assert {event.get("status") for event in events} >= {"skipped", "failed", "duplicate", "generated"}
    assert events[-1]["completed"] == 2


def test_dataset_stream_sends_question_then_done_after_save(client, stream_paths, monkeypatch):
    from app.eval import dataset

    built = _built()
    saved = []

    def generate(**kwargs):
        kwargs["on_progress"]({"type": "progress", "phase": "question", "completed": 0, "total": 1})
        kwargs["on_progress"]({"type": "question", "case": built.to_dict()["cases"][0]})
        return built

    original_save = dataset.save_dataset

    def save(result):
        path = original_save(result)
        saved.append(path)
        return path

    monkeypatch.setattr(dataset, "generate", generate)
    monkeypatch.setattr(dataset, "save_dataset", save)
    response = client.post("/api/eval/dataset/stream", json={"limit": 1})
    assert response.mimetype == "text/event-stream"
    events = _events(response)
    assert [event["type"] for event in events] == ["progress", "question", "done"]
    assert len(saved) == 1
    assert Path(events[-1]["path"]).exists()
    assert events[-1]["cases"][0]["question"] == "限额？"
    assert dataset.load_dataset().cases[0].case_id == "case-001"


def test_run_stream_forwards_stages_and_scoring_then_saves_report(client, stream_paths, monkeypatch):
    from app.eval import ragas_eval

    report = ragas_eval.EvalReport(created_at="2026-10-13T00:00:00+00:00", dataset_count=1)
    report.variants.append(ragas_eval.VariantResult(name="当前配置", params={}, scores={"faithfulness": 1.0}))

    def run(**kwargs):
        callback = kwargs["on_progress"]
        callback({"type": "progress", "phase": "answer", "case_id": "case-001"})
        callback({"type": "progress", "phase": "judge", "case_id": "case-001"})
        callback({"type": "case_scored", "scores": {"faithfulness": 1.0, "answer_relevancy": float("nan")},
                  "score_counts": {"faithfulness": 1}})
        return report

    monkeypatch.setattr(ragas_eval, "run", run)
    response = client.post("/api/eval/run/stream", json={"limit": 1, "with_ragas": True})
    events = _events(response)
    assert [event["type"] for event in events] == ["progress", "progress", "case_scored", "done"]
    assert events[2]["scores"]["answer_relevancy"] is None
    assert events[-1]["report"]["dataset_count"] == 1
    assert ragas_eval.load_latest()["variants"][0]["scores"]["faithfulness"] == 1.0
    assert Path(events[-1]["path"]).exists()


@pytest.mark.parametrize("endpoint", ["dataset", "run"])
def test_stream_runtime_failure_has_one_terminal_and_releases_lock(client, stream_paths, monkeypatch, endpoint):
    from app.eval import dataset, ragas_eval

    def fail(**kwargs):
        kwargs["on_progress"]({"type": "progress", "phase": "question"})
        raise RuntimeError("不能继续 stream-test-key")

    monkeypatch.setattr(dataset if endpoint == "dataset" else ragas_eval,
                        "generate" if endpoint == "dataset" else "run", fail)
    events = _events(client.post(f"/api/eval/{endpoint}/stream", json={}))
    assert [event["type"] for event in events] == ["progress", "error"]
    assert "不能继续" in events[-1]["message"]
    assert "stream-test-key" not in events[-1]["message"]


def test_save_failure_keeps_partial_question_and_never_sends_done(client, stream_paths, monkeypatch):
    from app.eval import dataset

    previous = _built()
    dataset.save_dataset(previous)

    def generate(**kwargs):
        kwargs["on_progress"]({"type": "question", "case": {"case_id": "new", "question": "新题"}})
        return _built()

    def fail_save(*args):
        raise OSError("permission denied stream-test-key")

    monkeypatch.setattr(dataset, "generate", generate)
    monkeypatch.setattr(dataset, "save_dataset", fail_save)
    events = _events(client.post("/api/eval/dataset/stream", json={}))
    assert [event["type"] for event in events] == ["question", "error"]
    assert events[-1]["message"] == "生成评测集失败"
    assert dataset.load_dataset().cases[0].case_id == "case-001"


def test_stream_rejects_invalid_variants_before_starting_worker(client, stream_paths):
    response = client.post("/api/eval/run/stream", json={"variants": "invalid"})
    assert response.status_code == 400
    assert response.get_json()["error"] == "variants 必须是数组"


def test_stream_and_json_mutations_share_lock_until_disconnected_worker_finishes(client, stream_paths, monkeypatch):
    from app.api import eval as eval_api
    from app.eval import dataset

    release = threading.Event()
    finished = threading.Event()

    def generate(**kwargs):
        kwargs["on_progress"]({"type": "progress", "phase": "question"})
        if not release.wait(timeout=5):
            raise RuntimeError("test worker timed out")
        return _built()

    original_save = dataset.save_dataset

    def save(built):
        try:
            return original_save(built)
        finally:
            finished.set()

    monkeypatch.setattr(dataset, "generate", generate)
    monkeypatch.setattr(dataset, "save_dataset", save)
    response = client.post("/api/eval/dataset/stream", json={}, buffered=False)
    try:
        assert next(response.response).startswith(b"data: ")
        for endpoint in ("dataset", "run", "dataset/stream", "run/stream"):
            blocked = client.post(f"/api/eval/{endpoint}", json={})
            assert blocked.status_code == 409
            assert "正在运行" in blocked.get_json()["error"]
        response.close()
        assert eval_api._operation_lock.locked()
    finally:
        response.close()
        release.set()
        assert finished.wait(timeout=5)
        # Acquiring the same lock waits for the worker's finally block, without polling.
        assert eval_api._operation_lock.acquire(timeout=5)
        eval_api._operation_lock.release()
    assert dataset.load_dataset().cases[0].case_id == "case-001"
