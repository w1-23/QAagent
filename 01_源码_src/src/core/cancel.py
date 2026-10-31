"""请求级协作式取消令牌（S1）。

设计要点
--------
* **零签名改动**：令牌放在 `ContextVar` 里。`src/tools/registry.py` 执行工具时已经用
  `ctx = contextvars.copy_context()` + `ctx.run(...)` 进入 executor 线程，因此请求级
  令牌天然随复制上下文到达工具线程（已用
  `experiments/trace/probes/probe_cancel_token_reaches_executor.py` 实测证明）。
* **跨线程可见**：底层是 `threading.Event`，任一线程 `cancel()` 后，工具线程里的
  `cancelled()` / `raise_if_cancelled()` 立刻反映。
* **协作式，不硬杀**：令牌只让工具在安全检查点主动退出，不承诺"点击停止后线程立即消失"。
  三道检查点为：写产物前、产物提交前、工具结果采用前。
* **无令牌不报错**：在没有绑定请求令牌的场景（脚本、单测、后台任务）里
  `raise_if_cancelled()` 是空操作，避免把"没有取消系统"误判成"已取消"。

已知限制：工具线程内部再新建的 `threading.Thread` **不会**继承本 ContextVar
（Python 语义）。因此工具实现约定：不要在子线程里写产物。
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import threading
from typing import Iterator, Optional

logger = logging.getLogger(__name__)

__all__ = [
    "ToolCancelled",
    "CancelToken",
    "current_token",
    "set_current_token",
    "reset_current_token",
    "raise_if_cancelled",
    "is_cancelled",
    "bound_token",
]


class ToolCancelled(Exception):
    """工具在安全检查点发现请求已取消。

    与 `[TOOL_CANCELLED]` 前缀对应：这**不是失败**，不应计入失败率、不应写
    `[TOOL_ERROR]`、不应触发"稍后重试"提示。
    """

    def __init__(self, where: str = "", reason: str = "user") -> None:
        self.where = where
        self.reason = reason
        super().__init__(f"cancelled at {where or 'unknown'} (reason={reason})")


class CancelToken:
    """线程安全的取消标志。一个请求一个实例。"""

    __slots__ = ("_event", "_reason", "_lock")

    def __init__(self) -> None:
        self._event = threading.Event()
        self._reason = "user"
        self._lock = threading.Lock()

    def cancel(self, reason: str = "user") -> None:
        with self._lock:
            if not self._event.is_set():
                self._reason = reason or "user"
            self._event.set()

    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str:
        return self._reason

    def raise_if_cancelled(self, where: str = "") -> None:
        if self._event.is_set():
            raise ToolCancelled(where=where, reason=self._reason)

    def __repr__(self) -> str:  # pragma: no cover - 便于日志
        return f"<CancelToken cancelled={self.cancelled()} reason={self.reason}>"


_current: contextvars.ContextVar[Optional[CancelToken]] = contextvars.ContextVar(
    "citrus_cancel_token", default=None
)


def current_token() -> Optional[CancelToken]:
    """当前上下文的令牌；没有则返回 None。"""
    return _current.get()


def set_current_token(token: Optional[CancelToken]) -> contextvars.Token:
    """绑定令牌，返回可用于还原的 token（必须与 `reset_current_token` 成对使用）。"""
    return _current.set(token)


def reset_current_token(token: contextvars.Token) -> None:
    """还原绑定。传错 token 时静默（例如协程被取消导致 finally 顺序异常）。"""
    try:
        _current.reset(token)
    except (ValueError, RuntimeError):  # pragma: no cover - 防御
        logger.debug("[Cancel] reset_current_token ignored a stale token reset")


def is_cancelled() -> bool:
    token = _current.get()
    return bool(token and token.cancelled())


def raise_if_cancelled(where: str = "") -> None:
    """检查点：已取消则抛 `ToolCancelled`；未绑定令牌时静默返回。"""
    token = _current.get()
    if token is not None and token.cancelled():
        raise ToolCancelled(where=where, reason=token.reason)


@contextlib.contextmanager
def bound_token(token: Optional[CancelToken]) -> Iterator[Optional[CancelToken]]:
    """临时绑定令牌的上下文管理器（测试与脚本用）。"""
    handle = _current.set(token)
    try:
        yield token
    finally:
        reset_current_token(handle)
