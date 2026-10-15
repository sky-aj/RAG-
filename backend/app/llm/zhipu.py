"""Compatibility imports for integrations using the old module name."""
from .client import LLMError, chat, describe, stream_chat, stream_chat_until_content, summarize

ZhipuError = LLMError
