"""Generation client for the configured OpenAI-compatible provider and GLM fallback."""
from __future__ import annotations

import json
import logging
from contextlib import closing
import threading
import time
from typing import Iterator

import httpx

from ..config import settings

logger = logging.getLogger(__name__)
_RETRY_STATUS = {429, 500, 502, 503, 504}
_lock = threading.Lock()
_next_slot = 0.0


class LLMError(RuntimeError):
    def __init__(self, reason: str, *, attempts: list[dict] | None = None,
                 actual_model: str | None = None, actual_provider: str | None = None):
        super().__init__(reason)
        self.attempts = attempts or []
        self.actual_model = actual_model
        self.actual_provider = actual_provider


def _throttle(interval: float | None = None) -> None:
    global _next_slot
    gap = settings.llm.min_interval if interval is None else interval
    if gap <= 0:
        return
    with _lock:
        now = time.monotonic()
        wait = max(0, _next_slot - now)
        _next_slot = max(now, _next_slot) + gap
    if wait:
        time.sleep(wait)


def _connection(fallback: bool = False) -> dict:
    config = settings.llm
    if fallback:
        if not config.fallback_configured:
            raise LLMError("GLM 备用连接未配置")
        return {"provider": config.fallback_provider, "base_url": config.fallback_base_url,
                "api_key": config.fallback_api_key, "model": config.fallback_model,
                "max_tokens": config.fallback_max_tokens, "temperature": config.fallback_temperature}
    if not config.configured:
        raise LLMError("LLM_API_KEY 未配置，请检查 backend/.env")
    return {"provider": config.provider, "base_url": config.base_url, "api_key": config.api_key,
            "model": config.chat_model, "max_tokens": config.max_tokens, "temperature": config.temperature}


def _body(messages: list[dict], stream: bool, connection: dict, overrides: dict) -> dict:
    allowed = {"model", "max_tokens", "temperature"}
    unknown = set(overrides) - allowed
    if unknown:
        raise LLMError(f"不支持的请求参数：{', '.join(sorted(unknown))}")
    body = {"messages": messages, "stream": stream}
    for key in allowed:
        body[key] = overrides.get(key, connection[key])
    if not isinstance(body["max_tokens"], int) or body["max_tokens"] <= 0:
        raise LLMError("max_tokens 必须为正整数")
    if not connection.get("fallback") and body["max_tokens"] > settings.llm.max_output_tokens:
        raise LLMError("max_tokens 超过模型能力上限")
    return body


def _request_details(connection: dict) -> tuple[str, dict]:
    return (connection["base_url"].rstrip("/") + "/chat/completions",
            {"Authorization": "Bearer " + connection["api_key"], "Content-Type": "application/json"})


def _retry_after(headers) -> float:
    try:
        return min(60.0, max(0.0, float(headers.get("Retry-After", 0))))
    except (ValueError, TypeError):
        return 0.0


def _delay(index: int, retry_after: float) -> None:
    time.sleep(max(retry_after, min(2.0 * 2 ** index, 20.0)))


def _safe_http_error(response: httpx.Response) -> str:
    # Provider errors can include request material. Only expose status and a short code.
    try:
        data = response.json()
        error = data.get("error") or {}
        code = error.get("code") if isinstance(error, dict) else None
    except (ValueError, TypeError, AttributeError, httpx.ResponseNotRead):
        code = None
    return f"HTTP {response.status_code}" + (f" ({str(code)[:40]})" if code else "")


def _notice(reason: str) -> dict:
    model = settings.llm.fallback_model
    return {"type": "notice", "reason": reason, "target_model": model,
            "text": f"主模型暂不可用（{reason}），已切换至 {model}，正在重新生成"}


def _call(messages: list[dict], connection: dict, overrides: dict) -> tuple[dict, list[dict]]:
    attempts = max(1, settings.llm.max_retries)
    url, headers = _request_details(connection)
    body = _body(messages, False, connection, overrides)
    history: list[dict] = []
    for index in range(attempts):
        delay = 0.0
        try:
            _throttle()
            with httpx.Client(timeout=settings.llm.timeout) as http:
                response = http.post(url, headers=headers, json=body)
            if response.status_code != 200:
                reason = _safe_http_error(response)
                recoverable = response.status_code in _RETRY_STATUS
                delay = _retry_after(response.headers)
                history.append({"model": body["model"], "provider": connection["provider"], "error": reason})
                if not recoverable:
                    raise LLMError(reason)
            else:
                data = response.json()
                if not isinstance(data, dict) or data.get("error") or not data.get("choices"):
                    raise LLMError("模型响应格式无效")
                choice = data["choices"][0]
                message = choice.get("message") or {}
                content = (message.get("content") or "").strip()
                history.append({"model": body["model"], "provider": connection["provider"],
                                "finish_reason": choice.get("finish_reason"), "usage": data.get("usage")})
                if not content:
                    raise _RecoverableError("正文为空")
                return ({"content": content, "reasoning": (message.get("reasoning_content") or "").strip(),
                         "finish_reason": choice.get("finish_reason"), "usage": data.get("usage"),
                         "actual_model": body["model"], "actual_provider": connection["provider"]}, history)
        except (httpx.HTTPError, ValueError) as error:
            reason = type(error).__name__
            history.append({"model": body["model"], "provider": connection["provider"], "error": reason})
        except _RecoverableError as error:
            reason = str(error)
        if index + 1 < attempts:
            _delay(index, delay)
    raise _RecoverableError(reason, history)


class _RecoverableError(LLMError):
    def __init__(self, reason: str, attempts: list[dict] | None = None, retry_after: float = 0.0,
                 *, actual_model: str | None = None, actual_provider: str | None = None):
        super().__init__(reason, attempts=attempts, actual_model=actual_model, actual_provider=actual_provider)
        self.retry_after = retry_after


def chat(messages: list[dict], *, on_notice=None, **overrides) -> dict:
    main = _connection()
    try:
        result, attempts = _call(messages, main, overrides)
        return {**result, "requested_model": overrides.get("model", main["model"]),
                "notices": [], "attempts": attempts}
    except _RecoverableError as error:
        if not settings.llm.fallback_configured:
            raise LLMError(f"主模型失败：{error}；GLM 备用连接未配置") from error
        notice = _notice(str(error))
        if on_notice:
            on_notice(notice)
        fallback = {**_connection(True), "fallback": True}
        try:
            result, attempts = _call(messages, fallback, {})
        except LLMError as fallback_error:
            raise LLMError(f"主模型失败：{error}；已通知切换，但 GLM 也失败：{fallback_error}") from fallback_error
        return {**result, "requested_model": overrides.get("model", main["model"]),
                "notices": [notice], "attempts": error.attempts + attempts}


def _stream_attempt(connection: dict, overrides: dict, *, usage=None, finish_reason=None,
                    error: str | None = None) -> dict:
    attempt = {"model": overrides.get("model", connection["model"]), "provider": connection["provider"],
               "usage": usage, "finish_reason": finish_reason}
    if error is not None:
        attempt["error"] = error
    return attempt


def _stream_once(messages: list[dict], connection: dict, overrides: dict) -> Iterator[dict]:
    url, headers = _request_details(connection)
    body = _body(messages, True, connection, overrides)
    finish_reason = None
    usage = None
    ended = False
    identity = {"actual_model": body["model"], "actual_provider": connection["provider"]}
    try:
        with httpx.Client(timeout=settings.llm.timeout) as http:
            with http.stream("POST", url, headers=headers, json=body) as response:
                if response.status_code != 200:
                    if response.status_code in _RETRY_STATUS:
                        raise _RecoverableError(_safe_http_error(response), retry_after=_retry_after(response.headers))
                    raise LLMError(_safe_http_error(response))
                for line in response.iter_lines():
                    line = line.strip()
                    if not line or line.startswith(":"):
                        continue
                    if not line.startswith("data:"):
                        continue
                    value = line[5:].strip()
                    if value == "[DONE]":
                        ended = True
                        break
                    try:
                        data = json.loads(value)
                    except ValueError as error:
                        raise LLMError("流式响应 JSON 无效") from error
                    if not isinstance(data, dict):
                        raise LLMError("流式响应格式无效")
                    if data.get("error"):
                        raise LLMError("流式响应包含平台错误")
                    if data.get("usage") is not None:
                        usage = data["usage"]
                    for choice in data.get("choices") or []:
                        delta = choice.get("delta") or {}
                        if delta.get("reasoning_content"):
                            yield {"type": "reasoning", "text": delta["reasoning_content"], **identity}
                        if delta.get("content"):
                            yield {"type": "content", "text": delta["content"], **identity}
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]
                if not ended and finish_reason is None:
                    raise _RecoverableError("流式连接意外中断")
    except httpx.HTTPError as error:
        raise _RecoverableError("流式连接中断", [_stream_attempt(connection, overrides, usage=usage,
                                finish_reason=finish_reason, error=type(error).__name__)], **identity) from error
    except LLMError as error:
        error.attempts = [_stream_attempt(connection, overrides, usage=usage,
                                         finish_reason=finish_reason, error=str(error))]
        error.actual_model = body["model"]
        error.actual_provider = connection["provider"]
        raise
    yield {"type": "done", "finish_reason": finish_reason, "usage": usage,
           "attempts": [_stream_attempt(connection, overrides, usage=usage, finish_reason=finish_reason)],
           **identity}


def stream_chat(messages: list[dict], **overrides) -> Iterator[dict]:
    connection = _connection()
    attempts = max(1, settings.llm.max_retries)
    history: list[dict] = []
    for index in range(attempts):
        produced = False
        try:
            _throttle()
            with closing(_stream_once(messages, connection, overrides)) as upstream:
                for event in upstream:
                    if event["type"] == "content":
                        produced = True
                    if event["type"] == "done":
                        event["attempts"] = history + (event.get("attempts") or [
                            _stream_attempt(connection, overrides, usage=event.get("usage"),
                                            finish_reason=event.get("finish_reason"))])
                    yield event
            return
        except _RecoverableError as error:
            history.extend(error.attempts or [_stream_attempt(connection, overrides, error=str(error))])
            error.attempts = list(history)
            if produced or index + 1 == attempts:
                raise
            _delay(index, error.retry_after)
        except httpx.HTTPError as error:
            history.append(_stream_attempt(connection, overrides, error=type(error).__name__))
            if produced or index + 1 == attempts:
                raise _RecoverableError("流式连接中断", list(history),
                                        actual_model=overrides.get("model", connection["model"]),
                                        actual_provider=connection["provider"]) from error
            _delay(index, 0)
        except LLMError as error:
            error.attempts = history + error.attempts
            raise


def stream_chat_until_content(messages: list[dict], **overrides) -> Iterator[dict]:
    produced = False
    terminal = None
    reason = "正文为空"
    main_attempts: list[dict] = []
    try:
        with closing(stream_chat(messages, **overrides)) as upstream:
            for event in upstream:
                if event["type"] == "content":
                    produced = True
                if event["type"] == "done":
                    terminal = event
                    main_attempts = event.get("attempts") or [{
                        "model": event.get("actual_model") or overrides.get("model", settings.llm.chat_model),
                        "provider": event.get("actual_provider") or settings.llm.provider,
                        "finish_reason": event.get("finish_reason"), "usage": event.get("usage")}]
                else:
                    yield event
    except _RecoverableError as error:
        if produced:
            raise
        reason = str(error)
        main_attempts = error.attempts
    except LLMError:
        raise
    if produced:
        if terminal:
            yield terminal
        return
    if terminal and terminal.get("finish_reason") not in (None, "stop", "length"):
        raise LLMError(f"主模型结束原因异常：{terminal.get('finish_reason')}")
    if not settings.llm.fallback_configured:
        raise LLMError(f"主模型{reason}；GLM 备用连接未配置", attempts=main_attempts,
                       actual_model=overrides.get("model", settings.llm.chat_model),
                       actual_provider=settings.llm.provider)
    yield _notice(reason)
    fallback = {**_connection(True), "fallback": True}
    try:
        fallback_produced = False
        with closing(_stream_once(messages, fallback, {})) as upstream:
            for event in upstream:
                if event["type"] == "reasoning":
                    continue
                if event["type"] == "done":
                    fallback_attempts = event.get("attempts") or [
                        _stream_attempt(fallback, {}, usage=event.get("usage"), finish_reason=event.get("finish_reason"))]
                    if not fallback_produced:
                        raise LLMError("GLM 备用模型正文为空", attempts=fallback_attempts)
                    event["attempts"] = main_attempts + fallback_attempts
                if event["type"] == "content":
                    fallback_produced = True
                yield event
    except LLMError as error:
        raise LLMError(f"GLM 备用流式调用失败：{error}", attempts=main_attempts + error.attempts,
                       actual_model=fallback["model"], actual_provider=fallback["provider"]) from error
    except httpx.HTTPError as error:
        raise LLMError("GLM 备用流式调用失败", attempts=main_attempts + [
            _stream_attempt(fallback, {}, error=type(error).__name__)],
            actual_model=fallback["model"], actual_provider=fallback["provider"]) from error


def summarize(text: str, limit: int = 400, on_notice=None) -> str:
    snippet = text.strip()
    if not snippet:
        return ""
    try:
        result = chat([{"role": "system", "content": "用中文给文档片段写一句不超过40字的摘要。"},
                       {"role": "user", "content": snippet[:limit]}], on_notice=on_notice,
                      max_tokens=settings.llm.summary_max_tokens)
        return result["content"].strip().strip("。")[:60]
    except LLMError as error:
        logger.warning("摘要生成失败：%s", error)
        return ""


def describe() -> dict:
    config = settings.llm
    return {"provider": config.provider, "base_url": config.base_url,
            "chat_model": config.chat_model, "max_tokens": config.max_tokens,
            "max_output_tokens": config.max_output_tokens, "temperature": config.temperature,
            "reasoning_model": None, "configured": config.configured,
            "fallback_configured": config.fallback_configured, "fallback_model": config.fallback_model}
