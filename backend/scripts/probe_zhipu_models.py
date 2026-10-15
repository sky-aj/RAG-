"""Small, targeted probe for the configured primary generation model.

The historical filename remains for existing invocations. This script never
prints a key or sends the model capability limit as a request budget.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings
from app.llm import client


def main() -> int:
    if not settings.llm.configured:
        print("LLM_API_KEY 未配置")
        return 1
    try:
        result = client.chat([{"role": "user", "content": "只回复：可用"}], max_tokens=64)
        print({"configured_model": settings.llm.chat_model,
               "actual_model": result["actual_model"],
               "actual_provider": result["actual_provider"],
               "has_content": bool(result["content"]),
               "finish_reason": result["finish_reason"],
               "fallback_used": bool(result["notices"])})
        return 0 if result["actual_provider"] == settings.llm.provider else 2
    except client.LLMError as error:
        print(f"探测失败：{error}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
