"""The RAGAS Judge connects to the independent GLM endpoint on recoverable errors."""
from dataclasses import replace

import pytest


def test_judge_sync_and_async_fallback(monkeypatch):
    import asyncio
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI

    from app.config import settings
    from app.eval import ragas_eval

    patched = replace(settings, llm=replace(settings.llm, api_key="main-key",
                                            fallback_api_key="old-key", temperature=0.8,
                                            fallback_temperature=0.2, fallback_enabled=True))
    monkeypatch.setattr(ragas_eval, "settings", patched)
    calls = []

    def fake_generate(self, messages, *args, **kwargs):
        calls.append((str(self.openai_api_base), self.openai_api_key.get_secret_value(),
                      kwargs.get("temperature"), kwargs.get("max_tokens")))
        if "sub-coco.com" in str(self.openai_api_base):
            raise ConnectionError("temporary")
        return "ok"

    async def fake_agenerate(self, messages, *args, **kwargs):
        return fake_generate(self, messages, *args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "_generate", fake_generate)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", fake_agenerate)
    notices = []
    fallback_cases = set()
    Judge = ragas_eval._make_throttled_chat_openai(0, notices, fallback_cases, {"case-001": "q"})
    judge = Judge(model=patched.eval.judge_model, api_key=patched.llm.api_key,
                  base_url=patched.llm.base_url, temperature=0.8, max_retries=0)
    message = [HumanMessage(content="q")]
    assert judge._generate(message, temperature=0.9, max_tokens=30000) == "ok"
    assert asyncio.run(judge._agenerate(message, temperature=0.9, max_tokens=30000)) == "ok"
    assert sum("部分判分使用了" in notice for notice in notices) == 2
    assert fallback_cases == {"case-001"}
    assert calls[0][1] == "main-key" and calls[1][1] == "old-key"
    assert calls[2][1] == "main-key" and calls[3][1] == "old-key"
    assert calls[0][2] == calls[2][2] == 0.8
    assert calls[1][2] == calls[3][2] == 0.2
    assert calls[1][3] == calls[3][3] == patched.llm.fallback_max_tokens
    assert judge.temperature == 0.8


@pytest.mark.parametrize("async_call", [False, True])
def test_judge_auth_error_does_not_fallback(monkeypatch, async_call):
    import asyncio
    import httpx
    from openai import AuthenticationError
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    from app.eval import ragas_eval

    calls = []
    notices = []

    def fail(self, *args, **kwargs):
        calls.append(self)
        response = httpx.Response(401, request=httpx.Request("POST", "https://example.test/v1/chat/completions"))
        raise AuthenticationError("mock auth error", response=response, body=None)

    async def afail(self, *args, **kwargs):
        return fail(self, *args, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "_generate", fail)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", afail)
    Judge = ragas_eval._make_throttled_chat_openai(0, notices)
    judge = Judge(model="deepseek-v4-flash", api_key="dummy", base_url="https://example.test/v1", max_retries=0)
    with pytest.raises(AuthenticationError):
        if async_call:
            asyncio.run(judge._agenerate([HumanMessage(content="q")]))
        else:
            judge._generate([HumanMessage(content="q")])
    assert len(calls) == 1 and notices == []


def test_failed_judge_fallback_retains_attempt_notice_without_success(monkeypatch):
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    from app.config import settings
    from app.eval import ragas_eval

    patched = replace(settings, llm=replace(settings.llm, api_key="main-key", fallback_api_key="old-key",
                                            fallback_enabled=True))
    monkeypatch.setattr(ragas_eval, "settings", patched)
    calls = []

    def fail(self, *args, **kwargs):
        calls.append(self)
        raise ConnectionError("temporary")

    monkeypatch.setattr(ChatOpenAI, "_generate", fail)
    notices = []
    cases = set()
    Judge = ragas_eval._make_throttled_chat_openai(0, notices, cases, {"case-001": "q"})
    judge = Judge(model=patched.eval.judge_model, api_key="main-key", base_url=patched.llm.base_url, max_retries=0)
    with pytest.raises(ConnectionError):
        judge._generate([HumanMessage(content="q")])
    assert len(calls) == 2
    assert len(notices) == 1 and "已尝试切换" in notices[0]
    assert not cases


@pytest.mark.parametrize("async_call", [False, True])
@pytest.mark.parametrize("finishes", [("length", "stop"), ("length", "length"), ("stop",)])
def test_judge_truncation_has_one_bounded_retry_and_separate_usage(monkeypatch, async_call, finishes):
    import asyncio
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    from app.config import settings
    from app.eval import ragas_eval

    patched = replace(settings, llm=replace(settings.llm, max_output_tokens=16000))
    monkeypatch.setattr(ragas_eval, "settings", patched)
    calls = []

    def generate(self, messages, **kwargs):
        budget = kwargs.get("max_tokens") or self.max_tokens
        calls.append(budget)
        return SimpleNamespace(generations=[SimpleNamespace(generation_info={"finish_reason": finishes[len(calls) - 1]})],
                               llm_output={"token_usage": {"completion_tokens": budget}})

    async def agenerate(self, messages, **kwargs):
        return generate(self, messages, **kwargs)

    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    monkeypatch.setattr(ChatOpenAI, "_agenerate", agenerate)
    notices, attempts = [], []
    Judge = ragas_eval._make_throttled_chat_openai(0, notices, questions={"case-001": "q"}, judge_attempts=attempts)
    judge = Judge(model="deepseek-v4-flash", api_key="dummy", base_url="https://example.test/v1",
                  max_tokens=8192, max_retries=0)
    if async_call:
        result = asyncio.run(judge._agenerate([HumanMessage(content="q")]))
    else:
        result = judge._generate([HumanMessage(content="q")])
    expected = [8192, 16000] if finishes[0] == "length" else [8192]
    assert calls == expected
    assert [attempt["usage"]["completion_tokens"] for attempt in attempts] == expected
    assert all(attempt["case_ids"] == ["case-001"] for attempt in attempts)
    assert len(notices) == len(expected) - 1
    assert result.generations[0].generation_info["finish_reason"] == finishes[-1]


def test_judge_truncation_at_model_limit_does_not_retry(monkeypatch):
    from types import SimpleNamespace
    from langchain_core.messages import HumanMessage
    from langchain_openai import ChatOpenAI
    from app.config import settings
    from app.eval import ragas_eval

    monkeypatch.setattr(ragas_eval, "settings", replace(settings, llm=replace(settings.llm, max_output_tokens=8192)))
    calls = []
    def generate(self, *args, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(generations=[SimpleNamespace(generation_info={"finish_reason": "length"})])
    monkeypatch.setattr(ChatOpenAI, "_generate", generate)
    notices = []
    Judge = ragas_eval._make_throttled_chat_openai(0, notices)
    judge = Judge(model="deepseek-v4-flash", api_key="dummy", max_tokens=8192, max_retries=0)
    judge._generate([HumanMessage(content="q")])
    assert len(calls) == 1 and not notices
