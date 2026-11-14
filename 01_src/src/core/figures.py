# -*- coding: utf-8 -*-
"""Request-scoped SVG figure registry (artifact-first drawing).

Why this exists
---------------
``draw_svg`` used to return the whole sanitized SVG and instruct the model to
paste it back verbatim into the final answer.  That made every diagram cost two
full generations of the same markup: once as the tool argument, once as answer
text.  Long diagrams therefore ran into the request deadline during synthesis
("第一轮绘图总是说超时"), the user retried, and each retry validated the SVG
again ("每次绘图SVG都需要反复安全校验").

With this registry the tool result is a handful of tokens: it stores the
sanitized markup and returns a ``[[FIGURE:n]]`` placeholder.  The graph expands
the placeholder once, after the answer is complete, so the browser receives
exactly the markup that passed validation and the model never retypes it.

Concurrency
-----------
The graph creates a mutable request-owned registry before dispatching tools.
Copied contexts in tool worker threads reference that same object, so writes
are visible to the owning graph without sharing data with other requests.
The lock protects concurrent tool writes within a request. Starting a new
request replaces the context binding; it never clears another request's data.
"""
from __future__ import annotations

import re
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field

PLACEHOLDER_RE = re.compile(r"\[\[FIGURE:(\d+)\]\]")


@dataclass
class _Registry:
    figures: dict[int, dict[str, str]] = field(default_factory=dict)
    attempts: int = 0
    lock: threading.Lock = field(default_factory=threading.Lock)


_CURRENT: ContextVar[_Registry | None] = ContextVar("request_figures", default=None)


def _registry() -> _Registry:
    registry = _CURRENT.get()
    if registry is None:
        # Direct tool callers have an isolated scope too. The graph must call
        # begin_request before dispatch so its worker contexts share this value.
        registry = _Registry()
        _CURRENT.set(registry)
    return registry

_TOOL_TEXT = (
    "SVG 已通过安全校验，登记为图示 {idx}（元素与属性均已在服务端过滤）。"
    "请在最终回答中用一行占位符 `[[FIGURE:{idx}]]` 表示该图，并附一句图示说明；"
    "不要重复输出 SVG 源码，也不要再次调用本工具生成同一张图。"
)


def begin_request() -> None:
    """Bind a fresh registry before creating this request's tool tasks."""
    _CURRENT.set(_Registry())


def bump_attempts() -> int:
    """Count draw_svg submissions in this request so retries cannot eat the deadline."""
    registry = _registry()
    with registry.lock:
        registry.attempts += 1
        return registry.attempts


def register(svg: str, alt_text: str = "") -> tuple[int, str]:
    """Store a validated SVG and return ``(index, text_for_model)``."""
    registry = _registry()
    with registry.lock:
        index = len(registry.figures) + 1
        registry.figures[index] = {"svg": svg, "alt": alt_text or "图示"}
    return index, _TOOL_TEXT.format(idx=index)


def count() -> int:
    registry = _registry()
    with registry.lock:
        return len(registry.figures)


def snapshot() -> dict[int, dict[str, str]]:
    registry = _registry()
    with registry.lock:
        return {k: dict(v) for k, v in registry.figures.items()}


def expand(text: str) -> str:
    """Replace every placeholder with the fenced SVG block it stands for."""
    if not text or "[[" not in text:
        return text
    store = snapshot()
    if not store:
        return text

    def _sub(match: re.Match[str]) -> str:
        figure = store.get(int(match.group(1)))
        if not figure:
            return match.group(0)
        return f"```svg\n{figure['svg']}\n```"

    return PLACEHOLDER_RE.sub(_sub, text)


def clear() -> None:
    begin_request()
