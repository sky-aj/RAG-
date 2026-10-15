"""Provider separation and stream terminal behavior without paid requests."""
from dataclasses import replace
import json

import httpx
import pytest


def _settings(monkeypatch, **overrides):
    from app.config import settings
    from app.llm import client

    config = replace(settings.llm, **{
        "api_key": "main-key", "base_url": "https://www.sub-coco.com/v1", "provider": "sub-coco",
        "chat_model": "deepseek-v4-flash", "max_retries": 1, "min_interval": 0,
        "fallback_api_key": "old-key", "fallback_enabled": True,
        "fallback_base_url": "https://open.bigmodel.cn/api/paas/v4", "fallback_provider": "zhipu",
        "fallback_model": "glm-4-flash", **overrides,
    })
    monkeypatch.setattr(client, "settings", replace(settings, llm=config))
    return client


def _transport(monkeypatch, client, handler):
    real_client = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(client.httpx, "Client", lambda **kwargs: real_client(transport=transport, **kwargs))


def _frame(delta=None, finish_reason=None, usage=None):
    data = {"choices": []}
    if delta is not None or finish_reason is not None:
        data["choices"] = [{"delta": delta or {}, "finish_reason": finish_reason}]
    if usage is not None:
        data["usage"] = usage
    return "data: " + json.dumps(data) + "\n\n"


def _sse(*frames):
    return httpx.Response(200, headers={"Content-Type": "text/event-stream"}, content="".join(frames))


def test_chat_keeps_explicit_model_and_separate_fallback_connection(monkeypatch):
    client = _settings(monkeypatch)
    requests = []

    def handler(request):
        import json
        body = json.loads(request.content)
        requests.append((str(request.url), request.headers["Authorization"], body))
        if len(requests) == 1:
            return httpx.Response(503, json={"error": {"code": "busy"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(client.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    result = client.chat([{"role": "user", "content": "q"}], model="deepseek-v4-flash", max_tokens=64)
    assert requests[0][0].endswith("sub-coco.com/v1/chat/completions")
    assert requests[0][1] == "Bearer main-key"
    assert requests[0][2]["model"] == "deepseek-v4-flash"
    assert requests[1][0].endswith("open.bigmodel.cn/api/paas/v4/chat/completions")
    assert requests[1][1] == "Bearer old-key"
    assert requests[1][2]["model"] == "glm-4-flash"
    assert requests[1][2]["max_tokens"] == client.settings.llm.fallback_max_tokens
    assert result["actual_model"] == "glm-4-flash"
    assert result["usage"] is None
    assert result["notices"]


def test_auth_error_never_switches_to_glm(monkeypatch):
    client = _settings(monkeypatch)
    calls = []
    transport = httpx.MockTransport(lambda request: (calls.append(request), httpx.Response(401))[1])
    real_client = httpx.Client
    monkeypatch.setattr(client.httpx, "Client", lambda **kwargs: real_client(transport=transport))
    with pytest.raises(client.LLMError, match="HTTP 401"):
        client.chat([{"role": "user", "content": "q"}])
    assert len(calls) == 1


def test_empty_main_stream_has_one_done_after_notice(monkeypatch):
    client = _settings(monkeypatch)

    def stream(messages, **overrides):
        yield {"type": "reasoning", "text": "thinking"}
        yield {"type": "done", "finish_reason": "length", "usage": None}

    def fallback(messages, connection, overrides):
        yield {"type": "content", "text": "answer"}
        yield {"type": "done", "actual_model": "glm-4-flash", "usage": {"completion_tokens": 1}}

    monkeypatch.setattr(client, "stream_chat", stream)
    monkeypatch.setattr(client, "_stream_once", fallback)
    events = list(client.stream_chat_until_content([{"role": "user", "content": "q"}]))
    assert [event["type"] for event in events] == ["reasoning", "notice", "content", "done"]
    assert events[-1]["actual_model"] == "glm-4-flash"


def test_main_stream_keeps_explicit_model_and_usage_after_finish(monkeypatch):
    client = _settings(monkeypatch)
    requests = []
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}

    def handler(request):
        requests.append(json.loads(request.content))
        return _sse(": keep-alive\n\n", _frame({"content": "ok"}),
                    _frame(finish_reason="stop"), _frame(usage=usage), "data: [DONE]\n\n")

    _transport(monkeypatch, client, handler)
    events = list(client.stream_chat_until_content([], model="explicit-model", max_tokens=64))
    assert len(requests) == 1 and requests[0]["model"] == "explicit-model"
    assert [e["type"] for e in events] == ["content", "done"]
    assert events[-1]["usage"] == usage
    assert events[-1]["attempts"][0]["usage"] == usage
    assert events[-1]["actual_model"] == "explicit-model"


def test_stream_fallback_retains_retries_and_main_usage(monkeypatch):
    client = _settings(monkeypatch, max_retries=2)
    requests = []
    waits = []
    monkeypatch.setattr(client.time, "sleep", waits.append)
    main_usage = {"total_tokens": 80}
    fallback_usage = {"total_tokens": 5}

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503, headers={"Retry-After": "7"})
        if len(requests) == 2:
            return _sse(_frame({"reasoning_content": "thinking"}), _frame(finish_reason="length"),
                        _frame(usage=main_usage), "data: [DONE]\n\n")
        return _sse(_frame({"content": "fallback"}), _frame(finish_reason="stop"),
                    _frame(usage=fallback_usage), "data: [DONE]\n\n")

    _transport(monkeypatch, client, handler)
    events = list(client.stream_chat_until_content([]))
    assert [e["type"] for e in events] == ["reasoning", "notice", "content", "done"]
    assert waits == [7]
    assert requests[2].headers["Authorization"] == "Bearer old-key"
    result = events[-1]
    assert result["usage"] == fallback_usage  # Final attempt only, never the total cost.
    assert len(result["attempts"]) == 3
    assert result["attempts"][0]["error"] == "HTTP 503"
    assert result["attempts"][1]["usage"] == main_usage
    assert result["attempts"][2]["usage"] == fallback_usage
    assert [a["provider"] for a in result["attempts"]] == ["sub-coco", "sub-coco", "zhipu"]


def test_exhausted_stream_retries_remain_in_fallback_result(monkeypatch):
    client = _settings(monkeypatch, max_retries=2)
    requests = []
    monkeypatch.setattr(client.time, "sleep", lambda _: None)

    def handler(request):
        requests.append(request)
        if len(requests) < 3:
            raise httpx.ConnectError("mock failure", request=request)
        return _sse(_frame({"content": "ok"}), "data: [DONE]\n\n")

    _transport(monkeypatch, client, handler)
    events = list(client.stream_chat_until_content([]))
    assert [e["type"] for e in events] == ["notice", "content", "done"]
    assert len(events[-1]["attempts"]) == 3
    assert all(a["error"] == "ConnectError" for a in events[-1]["attempts"][:2])
    assert events[-1]["usage"] is None


def test_partial_stream_disconnect_never_replays_or_falls_back(monkeypatch):
    client = _settings(monkeypatch, max_retries=3)
    requests = []

    def handler(request):
        requests.append(request)
        return _sse(_frame({"content": "partial"}), _frame(usage={"total_tokens": 8}))

    _transport(monkeypatch, client, handler)
    events = []
    with pytest.raises(client.LLMError, match="意外中断") as caught:
        events.extend(client.stream_chat_until_content([]))
    assert [e["type"] for e in events] == ["content"]
    assert len(requests) == 1
    assert caught.value.attempts[0]["usage"] == {"total_tokens": 8}
    assert caught.value.actual_model == "deepseek-v4-flash"


@pytest.mark.parametrize("fallback_empty", [False, True])
def test_failed_fallback_keeps_notice_and_attempt_records(monkeypatch, fallback_empty):
    client = _settings(monkeypatch)
    requests = []

    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return _sse(_frame(finish_reason="length"), _frame(usage={"total_tokens": 80}), "data: [DONE]\n\n")
        if fallback_empty:
            return _sse(_frame(finish_reason="stop"), "data: [DONE]\n\n")
        return _sse(_frame({"content": "partial fallback"}))

    _transport(monkeypatch, client, handler)
    events = []
    with pytest.raises(client.LLMError, match="GLM 备用") as caught:
        events.extend(client.stream_chat_until_content([]))
    assert events[0]["type"] == "notice"
    assert not any(e["type"] == "done" for e in events)
    assert len(requests) == 2
    assert len(caught.value.attempts) == 2
    assert caught.value.attempts[0]["usage"] == {"total_tokens": 80}
    assert caught.value.actual_model == "glm-4-flash"


@pytest.mark.parametrize("status", [400, 401, 403])
def test_stream_config_errors_never_switch(monkeypatch, status):
    client = _settings(monkeypatch)
    requests = []
    _transport(monkeypatch, client, lambda request: (requests.append(request), httpx.Response(status))[1])
    with pytest.raises(client.LLMError, match=f"HTTP {status}"):
        list(client.stream_chat_until_content([]))
    assert len(requests) == 1


def test_closing_stream_closes_upstream_response(monkeypatch):
    client = _settings(monkeypatch)

    class TrackedStream(httpx.SyncByteStream):
        closed = False

        def __iter__(self):
            yield _frame({"content": "partial"}).encode()
            yield b"data: [DONE]\n\n"

        def close(self):
            self.closed = True

    body = TrackedStream()
    _transport(monkeypatch, client, lambda _: httpx.Response(200, stream=body))
    stream = client.stream_chat_until_content([])
    assert next(stream)["type"] == "content"
    stream.close()
    assert body.closed


def test_unread_streaming_auth_response_remains_safe_config_error(monkeypatch):
    client = _settings(monkeypatch)
    requests = []

    class ErrorBody(httpx.SyncByteStream):
        def __iter__(self):
            yield b'{"error":{"message":"main-key old-key"}}'

    def handler(request):
        requests.append(request)
        return httpx.Response(401, stream=ErrorBody())

    _transport(monkeypatch, client, handler)
    with pytest.raises(client.LLMError, match="HTTP 401") as caught:
        list(client.stream_chat_until_content([]))
    assert len(requests) == 1
    assert "main-key" not in str(caught.value) and "old-key" not in str(caught.value)
