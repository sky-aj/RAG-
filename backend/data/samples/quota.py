"""配额检查与滑动窗口实现。

这个文件用来演示「按函数 / 类切分」：每个函数都有 docstring，
切分层应当以函数签名 + docstring + 函数体为一个 chunk。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass


DEFAULT_LIMIT = 600
BURST_FACTOR = 1.5


@dataclass
class QuotaResult:
    """一次配额检查的结果。"""

    allowed: bool
    used: int
    limit: int
    retry_after: float = 0.0


class SlidingWindowCounter:
    """按滑动窗口统计请求量。

    用两个相邻窗口的加权求和近似连续释放，避免为每个请求存时间戳。
    """

    def __init__(self, limit: int = DEFAULT_LIMIT, window_seconds: int = 60) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self._current = 0
        self._previous = 0
        self._window_start = time.time()

    def _rotate(self) -> None:
        """窗口到点后滚动一次。"""
        now = time.time()
        elapsed = now - self._window_start
        if elapsed >= self.window_seconds:
            self._previous = self._current
            self._current = 0
            self._window_start = now

    def check(self, burst: bool = False) -> QuotaResult:
        """检查并消耗一次配额。"""
        self._rotate()
        effective_limit = math.floor(self.limit * (BURST_FACTOR if burst else 1))
        if self._current >= effective_limit:
            return QuotaResult(
                allowed=False,
                used=self._current,
                limit=effective_limit,
                retry_after=self.window_seconds,
            )
        self._current += 1
        return QuotaResult(allowed=True, used=self._current, limit=effective_limit)
