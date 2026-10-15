"""Model configuration rejects invalid values without printing credentials."""
from dataclasses import replace

import pytest


@pytest.mark.parametrize("key", ["LLM_TIMEOUT", "LLM_MIN_INTERVAL", "LLM_TEMPERATURE",
                                  "LLM_FALLBACK_TEMPERATURE", "EVAL_JUDGE_INTERVAL", "EVAL_REQUEST_INTERVAL"])
@pytest.mark.parametrize("value", ["not-a-number", "nan", "inf", "-inf"])
def test_invalid_numeric_model_environment_is_rejected(monkeypatch, key, value):
    from app.config import EvalConfig, LLMConfig

    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=key):
        (EvalConfig if key.startswith("EVAL_") else LLMConfig)()


@pytest.mark.parametrize("field,value", [("temperature", -1), ("temperature", float("nan")),
                                         ("timeout", 0), ("timeout", float("inf")),
                                         ("min_interval", -1), ("fallback_temperature", 0)])
def test_invalid_model_values_are_rejected(field, value):
    from app.config import settings

    with pytest.raises(ValueError):
        replace(settings.llm, **{field: value})


def test_primary_zero_temperature_and_zero_intervals_are_valid():
    from app.config import settings

    assert replace(settings.llm, temperature=0, min_interval=0).temperature == 0
    assert replace(settings.eval, request_interval=0, judge_interval=0).judge_interval == 0


def test_invalid_budget_and_missing_primary_key_do_not_use_legacy_main(monkeypatch):
    from app.config import LLMConfig

    monkeypatch.setenv("LLM_MAX_TOKENS", "invalid")
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        LLMConfig()
    monkeypatch.setenv("LLM_MAX_TOKENS", "8192")
    monkeypatch.setenv("LLM_API_KEY", "")
    monkeypatch.setenv("LLM_FALLBACK_API_KEY", "")
    monkeypatch.setenv("ZHIPU_API_KEY", "legacy-secret")
    config = LLMConfig()
    assert not config.configured
    assert config.fallback_api_key == ""  # Explicitly blank fallback remains disabled.
    assert "legacy-secret" not in repr(config)


def test_missing_fallback_key_and_excessive_budget_are_rejected():
    from app.config import settings

    assert not replace(settings.llm, fallback_api_key="").fallback_configured
    with pytest.raises(ValueError, match="LLM_MAX_TOKENS"):
        replace(settings.llm, max_tokens=settings.llm.max_output_tokens + 1)
