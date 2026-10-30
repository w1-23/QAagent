# -*- coding: utf-8 -*-
"""Process-level SVG figure registry (artifact-first drawing).

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
Tool bodies run in worker threads (``Tool.ainvoke`` uses an executor), so a
``ContextVar`` set inside the tool would not be visible to the graph.  The
registry is therefore a process-level dict guarded by a lock, reset at the
start of every request by :func:`begin_request`.  The server runs one uvicorn
worker and each request drains its own registry right after its tool batch, so
interleaving requires two *simultaneous* drawing requests; that case degrades
to "a figure may be missing", never to leaking markup across answers.
"""
from __future__ import annotations

import re
import threading

PLACEHOLDER_RE = re.compile(r"\[\[FIGURE:(\d+)\]\]")

_LOCK = threading.Lock()
_FIGURES: dict[int, dict[str, str]] = {}
_ATTEMPTS = 0

_TOOL_TEXT = (
    "SVG 已通过安全校验，登记为图示 {idx}（元素与属性均已在服务端过滤）。"
    "请在最终回答中用一行占位符 `[[FIGURE:{idx}]]` 表示该图，并附一句图示说明；"
    "不要重复输出 SVG 源码，也不要再次调用本工具生成同一张图。"
)


def begin_request() -> None:
    """Reset the registry for a new request."""
    global _ATTEMPTS
    with _LOCK:
        _FIGURES.clear()
        _ATTEMPTS = 0


def bump_attempts() -> int:
    """Count draw_svg submissions in this request so retries cannot eat the deadline."""
    global _ATTEMPTS
    with _LOCK:
        _ATTEMPTS += 1
        return _ATTEMPTS


def register(svg: str, alt_text: str = "") -> tuple[int, str]:
    """Store a validated SVG and return ``(index, text_for_model)``."""
    with _LOCK:
        index = len(_FIGURES) + 1
        _FIGURES[index] = {"svg": svg, "alt": alt_text or "图示"}
    return index, _TOOL_TEXT.format(idx=index)


def count() -> int:
    with _LOCK:
        return len(_FIGURES)


def snapshot() -> dict[int, dict[str, str]]:
    with _LOCK:
        return {k: dict(v) for k, v in _FIGURES.items()}


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
