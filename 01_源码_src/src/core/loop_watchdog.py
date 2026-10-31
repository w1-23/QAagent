"""Event-loop stall watchdog — **diagnostics only**.

Why this exists
---------------
Production evidence (``DEPLOY_20261030_54bf15c.md``) showed a turn whose client
was killed staying ``running`` for minutes: heartbeat frames and disconnect
detection both queue behind whatever is occupying the event loop. Measuring
"the loop is slow" is not enough — the useful question is *which frame* is
holding it.

This watchdog answers that question without touching request state:

* a tiny asyncio task bumps a counter every ``tick_s``;
* a daemon thread watches the counter; when it stops advancing for longer than
  ``stall_ms``, the thread snapshots the **loop thread's live stack** via
  ``sys._current_frames()`` and records it.

Because the sampling thread is independent, it can still run while the loop is
blocked — which is exactly what an in-loop monitor cannot do.

Nothing here raises into the loop, mutates state, or gates a request: if the
watchdog itself fails, it disables itself and stays silent.

Env
---
``CITRUSQA_LOOP_WATCHDOG=0``      disable entirely
``CITRUSQA_LOOP_STALL_MS=1000``   stall threshold in milliseconds
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from typing import Any

logger = logging.getLogger(__name__)

# Frames that only say "the loop is waiting"; dropping them exposes the frame
# that is actually doing the blocking work.
_NOISE = (
    "asyncio/base_events.py",
    "asyncio/events.py",
    "asyncio/runners.py",
    "asyncio/tasks.py",
    "asyncio/selector_events.py",
    "asyncio/windows_events.py",
    "uvicorn/protocols",
    "uvicorn/server.py",
    "anyio/_backends",
    "anyio/_core",
    "anyio/abc",
    "starlette/responses.py",
    "starlette/routing.py",
    "sse_starlette",
)

_MAX_STALLS = 20
_MAX_FRAMES = 8

_LOCK = threading.Lock()
_STATE: dict[str, Any] = {
    "installed": False,
    "stall_ms": 1000.0,
    "tick_s": 0.1,
    "tick": 0,
    "tick_at": 0.0,
    "loop_thread": None,
    "thread": None,
    "stop": threading.Event(),
    "stalls": deque(maxlen=_MAX_STALLS),
    "stall_count": 0,
    "max_stall_ms": 0.0,
    "sampling": 0,
}


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default


def _useful_frames(frame: Any) -> list[str]:
    """Deepest-first user frames of ``frame``, as ``file:line in func``."""
    try:
        extracted = traceback.extract_stack(frame)
    except Exception:
        return []
    out: list[str] = []
    for item in reversed(extracted):
        filename = str(item.filename).replace("\\", "/")
        if any(token in filename for token in _NOISE):
            continue
        # Skip the watchdog's own plumbing by exact file name — a substring
        # match would also drop tests/probes that merely mention it.
        base = filename.rsplit("/", 1)[-1]
        if base in {"loop_watchdog.py", "threading.py"}:
            continue
        out.append(f"{filename}:{item.lineno} in {item.name}")
        if len(out) >= _MAX_FRAMES:
            break
    return out


def _watchdog_loop(stall_ms: float, tick_s: float) -> None:
    stop = _STATE["stop"]
    reported = False
    while not stop.wait(tick_s):
        try:
            with _LOCK:
                tick_at = _STATE["tick_at"]
                loop_thread = _STATE["loop_thread"]
            age_ms = (time.perf_counter() - tick_at) * 1000.0
            if age_ms < stall_ms:
                # Recovered: arm the next episode.
                reported = False
                continue
            if reported:
                continue          # one report per stall episode
            reported = True
            frame = sys._current_frames().get(loop_thread) if loop_thread else None
            frames = _useful_frames(frame) if frame is not None else []
            with _LOCK:
                _STATE["stall_count"] += 1
                _STATE["max_stall_ms"] = max(_STATE["max_stall_ms"], round(age_ms, 1))
                _STATE["stalls"].append({
                    "at": round(time.time(), 3),
                    "stall_ms": round(age_ms, 1),
                    "frames": frames,
                })
            logger.warning(
                "[loop-watchdog] event loop blocked for %.0f ms; deepest frame: %s",
                age_ms, frames[0] if frames else "<unknown>")
            for line in frames[1:]:
                logger.warning("[loop-watchdog]   under: %s", line)
        except Exception:         # pragma: no cover - diagnostics never escalate
            try:
                stop.set()
            except Exception:
                pass
            logger.debug("[loop-watchdog] disabled after internal error", exc_info=True)
            return


async def _ticker(tick_s: float) -> None:
    try:
        while True:
            await asyncio.sleep(tick_s)
            with _LOCK:
                _STATE["tick"] += 1
                _STATE["tick_at"] = time.perf_counter()
    except asyncio.CancelledError:      # pragma: no cover - shutdown path
        raise


def install(*, stall_ms: float | None = None, tick_s: float | None = None) -> bool:
    """Start the watchdog. Idempotent; returns ``True`` when running."""
    if os.getenv("CITRUSQA_LOOP_WATCHDOG", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    if _STATE["installed"]:
        return True
    stall = float(stall_ms if stall_ms is not None else _env_float("CITRUSQA_LOOP_STALL_MS", 1000.0))
    tick = float(tick_s if tick_s is not None else _env_float("CITRUSQA_LOOP_TICK_MS", 100.0) / 1000.0)
    tick = max(0.01, tick)
    stall = max(tick * 3, stall)

    loop = asyncio.get_running_loop()
    with _LOCK:
        _STATE.update({
            "installed": True,
            "stall_ms": stall,
            "tick_s": tick,
            "tick": 0,
            "tick_at": time.perf_counter(),
            "loop_thread": threading.get_ident(),
            "stalls": deque(maxlen=_MAX_STALLS),
            "stall_count": 0,
            "max_stall_ms": 0.0,
            "sampling": 0,
        })
        _STATE["stop"] = threading.Event()

    # The ticker must start on the loop thread: install() is called from lifespan.
    _STATE["loop_task"] = loop.create_task(_ticker(tick))
    thread = threading.Thread(
        target=_watchdog_loop, args=(stall, tick),
        name="citrus-loop-watchdog", daemon=True)
    _STATE["thread"] = thread
    thread.start()
    logger.info("[loop-watchdog] installed (stall_ms=%.0f tick_ms=%.0f)", stall, tick * 1000)
    return True


def uninstall() -> None:
    """Stop the sampling thread and the ticker task (best effort)."""
    with _LOCK:
        _STATE["installed"] = False
        stop = _STATE["stop"]
        task = _STATE.get("loop_task")
        thread = _STATE.get("thread")
        _STATE["loop_task"] = None
        _STATE["thread"] = None
    try:
        stop.set()
    except Exception:
        pass
    if task is not None:
        try:
            task.cancel()
        except Exception:
            pass
    if thread is not None and thread.is_alive():
        thread.join(timeout=1.0)


def reset() -> None:
    with _LOCK:
        _STATE["stalls"] = deque(maxlen=_MAX_STALLS)
        _STATE["stall_count"] = 0
        _STATE["max_stall_ms"] = 0.0


def snapshot() -> dict:
    """Current watchdog state plus the recorded stalls (newest last)."""
    with _LOCK:
        stalls = list(_STATE["stalls"])
        return {
            "installed": bool(_STATE["installed"]),
            "stall_ms": _STATE["stall_ms"],
            "tick_s": _STATE["tick_s"],
            "tick": _STATE["tick"],
            "stall_count": _STATE["stall_count"],
            "max_stall_ms": _STATE["max_stall_ms"],
            "stalls": stalls,
        }
