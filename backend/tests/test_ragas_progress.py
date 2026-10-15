"""Offline regressions for isolated Judge bindings and incremental scoring."""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import pytest


def _rows(count=3):
    return [
        {"case_id": f"case-{index}", "question": f"question-{index}",
         "ground_truth": "reference", "answer": "answer", "contexts": ["context"],
         "hit": True, "usage": {}, "notices": [], "diagnostics": {}}
        for index in range(1, count + 1)
    ]


@pytest.fixture()
def offline_ragas(monkeypatch):
    """Use the installed RAGAS metric classes but never construct API clients."""
    import langchain_openai
    import ragas
    import ragas.embeddings
    import ragas.llms
    from app.eval import ragas_eval

    monkeypatch.setattr(ragas_eval, "_make_throttled_chat_openai", lambda *args: lambda **kwargs: object())
    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", lambda **kwargs: object())
    monkeypatch.setattr(ragas.llms, "LangchainLLMWrapper", lambda value: value)
    monkeypatch.setattr(ragas.embeddings, "LangchainEmbeddingsWrapper", lambda value: value)
    return ragas_eval, ragas


def test_concurrent_runs_own_metric_instances_with_explicit_judge(offline_ragas, monkeypatch):
    """A second evaluate cannot clear the first evaluation's LLM binding."""
    import pandas as pd
    import ragas.metrics

    ragas_eval, ragas = offline_ragas
    barrier = threading.Barrier(2)
    captured = []
    lock = threading.Lock()
    global_metrics = [ragas.metrics.faithfulness, ragas.metrics.answer_relevancy,
                      ragas.metrics.context_precision, ragas.metrics.context_recall]
    original_bindings = [metric.llm for metric in global_metrics]

    def evaluate(**kwargs):
        metrics = kwargs["metrics"]
        assert [metric.name for metric in metrics] == list(ragas_eval.METRIC_NAMES)
        assert all(metric.llm is kwargs["llm"] for metric in metrics)
        assert metrics[1].embeddings is kwargs["embeddings"]
        with lock:
            captured.append(metrics)
        barrier.wait(timeout=10)
        # Emulate cleanup in one evaluation. Its changes must be local.
        if kwargs["dataset"].samples[0].user_input == "question-1":
            for metric in metrics:
                metric.llm = None
        barrier.wait(timeout=10)
        if kwargs["dataset"].samples[0].user_input == "question-2":
            assert all(metric.llm is kwargs["llm"] for metric in metrics)
        return SimpleNamespace(to_pandas=lambda: pd.DataFrame([{key: 1.0 for key in ragas_eval.METRIC_NAMES}]))

    monkeypatch.setattr(ragas, "evaluate", evaluate)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(ragas_eval._ragas_scores, [row]) for row in _rows(2)]
        results = [future.result(timeout=30) for future in futures]
    assert all(error == "" and scores["faithfulness"] == 1.0 for scores, error in results)
    assert all(first is not second for first, second in zip(*captured))
    assert [metric.llm for metric in global_metrics] == original_bindings


def test_each_sample_gets_fresh_judge_and_embedding_clients(offline_ragas, monkeypatch):
    """Per-sample evaluate() loops must not reuse clients tied to a closed loop."""
    import pandas as pd
    import langchain_openai
    import ragas.embeddings
    import ragas.llms

    ragas_eval, ragas = offline_ragas
    judges, embedders, calls = [], [], []

    class FakeJudge:
        def __init__(self, **kwargs):
            self.closed = False
            judges.append(self)

    class FakeEmbedder:
        def __init__(self):
            self.closed = False
            embedders.append(self)

    monkeypatch.setattr(ragas_eval, "_make_throttled_chat_openai", lambda *args: FakeJudge)
    monkeypatch.setattr(langchain_openai, "OpenAIEmbeddings", lambda **kwargs: FakeEmbedder())
    monkeypatch.setattr(ragas.llms, "LangchainLLMWrapper", lambda value: value)
    monkeypatch.setattr(ragas.embeddings, "LangchainEmbeddingsWrapper", lambda value: value)

    def evaluate(**kwargs):
        judge, embedder = kwargs["llm"], kwargs["embeddings"]
        assert not judge.closed and not embedder.closed
        calls.append((judge, embedder))
        if len(calls) > 1:
            previous_judge, previous_embedder = calls[-2]
            assert judge is not previous_judge
            assert embedder is not previous_embedder
            assert previous_judge.closed and previous_embedder.closed
        # RAGAS closes the per-call async loop after evaluate returns. Marking
        # these fakes closed models that lifetime boundary without networking.
        judge.closed = True
        embedder.closed = True
        return SimpleNamespace(to_pandas=lambda: pd.DataFrame([
            {metric: 1.0 for metric in ragas_eval.METRIC_NAMES}
        ]))

    monkeypatch.setattr(ragas, "evaluate", evaluate)
    scores, error = ragas_eval._ragas_scores(_rows(2))
    assert error == ""
    assert scores == {metric: 1.0 for metric in ragas_eval.METRIC_NAMES}
    assert len(judges) == len(embedders) == len(calls) == 2


def test_incremental_scores_count_only_valid_values(offline_ragas, monkeypatch):
    import pandas as pd

    ragas_eval, ragas = offline_ragas
    rows = _rows()
    values = [
        {"faithfulness": 1.0, "answer_relevancy": float("nan"), "context_precision": 0.0},
        {"faithfulness": float("nan"), "answer_relevancy": 0.5, "context_precision": float("inf")},
        {"faithfulness": 0.5, "answer_relevancy": None, "context_precision": None},
    ]
    calls = []

    def evaluate(**kwargs):
        assert len(kwargs["dataset"].samples) == 1
        calls.append(kwargs["metrics"])
        return SimpleNamespace(to_pandas=lambda: pd.DataFrame([values[len(calls) - 1]]))

    monkeypatch.setattr(ragas, "evaluate", evaluate)
    events = []
    scores, error = ragas_eval._ragas_scores(rows, on_progress=events.append)
    assert error == ""
    assert scores == {"faithfulness": 0.75, "answer_relevancy": 0.5,
                      "context_precision": 0.0, "context_recall": None}
    done = [event for event in events if event["status"] == "completed"]
    assert [event["completed"] for event in done] == [1, 2, 3]
    assert done[0]["running_scores"]["faithfulness"] == 1.0
    assert done[1]["score_counts"]["faithfulness"] == 1
    assert done[1]["scores"]["context_precision"] is None
    assert done[2]["score_counts"] == {"faithfulness": 2, "answer_relevancy": 1,
                                      "context_precision": 1, "context_recall": 0}
    assert all(metric is not other for metric, other in zip(calls[0], calls[1]))


def test_scoring_failure_keeps_progress_and_continues_later_samples(offline_ragas, monkeypatch):
    import pandas as pd

    ragas_eval, ragas = offline_ragas
    rows = _rows(2)

    def evaluate(**kwargs):
        if kwargs["dataset"].samples[0].user_input == "question-1":
            raise RuntimeError("mock judge failed")
        return SimpleNamespace(to_pandas=lambda: pd.DataFrame([{key: 0.5 for key in ragas_eval.METRIC_NAMES}]))

    monkeypatch.setattr(ragas, "evaluate", evaluate)
    events = []
    scores, error = ragas_eval._ragas_scores(rows, on_progress=events.append)
    assert "case-1" in error and "mock judge failed" in error
    assert rows[0]["scores"] == {key: None for key in ragas_eval.METRIC_NAMES}
    assert rows[1]["scores"] == {key: 0.5 for key in ragas_eval.METRIC_NAMES}
    assert scores == rows[1]["scores"]
    assert events[-1]["completed"] == 2
    assert events[-1]["score_counts"] == {key: 1 for key in ragas_eval.METRIC_NAMES}


def test_run_reports_score_denominators_and_progress_variant(monkeypatch):
    from app.eval import dataset, ragas_eval

    cases = [dataset.EvalCase(case_id=f"case-{index}", question=f"question-{index}",
                              ground_truth="reference", doc_id="d", doc_name="doc",
                              line_start=1, line_end=2) for index in range(1, 4)]
    monkeypatch.setattr(dataset, "load_dataset", lambda: dataset.EvalDataset(name="test", created_at="now", cases=cases))
    monkeypatch.setattr(ragas_eval, "settings", replace(ragas_eval.settings,
                                                       eval=replace(ragas_eval.settings.eval, request_interval=0)))
    rows = {row["case_id"]: row for row in _rows()}
    monkeypatch.setattr(ragas_eval, "_run_one", lambda case, variant: rows[case.case_id])

    def score(scored_rows, notices, fallback_cases, on_progress=None):
        for index, row in enumerate(scored_rows):
            row["scores"] = {key: None for key in ragas_eval.METRIC_NAMES}
            row["scores"]["faithfulness"] = 1.0 if index == 0 else None
        averages, counts = ragas_eval._score_summary(scored_rows)
        on_progress({"type": "progress", "phase": "judge", "status": "completed",
                     "completed": len(scored_rows), "total": len(scored_rows),
                     "running_scores": averages, "score_counts": counts})
        return averages, ""

    monkeypatch.setattr(ragas_eval, "_ragas_scores", score)
    events = []
    report = ragas_eval.run(variants=[{"name": "test plan"}], on_progress=events.append)
    variant = report.variants[0]
    assert variant.scores["faithfulness"] == 1.0
    assert variant.score_counts["faithfulness"] == 1
    assert report.to_dict()["variants"][0]["score_counts"] == variant.score_counts
    assert all(event["variant"] == "test plan" for event in events)
    completed = [event for event in events if event["phase"] == "answer" and event["status"] == "completed"]
    assert [event["completed"] for event in completed] == [1, 2, 3]


def test_progress_sink_failure_does_not_abort_evaluation(monkeypatch):
    from app.eval import ragas_eval

    def broken_sink(event):
        raise RuntimeError("UI went away")

    ragas_eval._emit_progress(broken_sink, type="progress", phase="judge")
