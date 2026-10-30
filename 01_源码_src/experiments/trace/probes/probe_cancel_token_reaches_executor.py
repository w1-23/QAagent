"""S1 前置验证：取消令牌能否从请求协程一路到达工具执行线程。

S1-01 的整个设计建立在两个假设上：
  1) registry._run_single_tool 用 `ctx = contextvars.copy_context()` + `ctx.run(fn, ...)`
     进入 asyncio 的默认线程池，因此请求级 ContextVar 在该线程里可见；
  2) threading.Event 跨线程置位后，工作线程里的 .is_set() 立刻为 True。

如果 (1) 不成立，S1 就必须改成"显式传令牌给 17 个工具函数"，改动面完全不同。
所以先用真实代码路径证明，再写 S1。

运行：
  E:\\anaconda\\envs\\rag-agent\\python.exe experiments\\trace\\probes\\probe_cancel_token_reaches_executor.py
"""
from __future__ import annotations

import asyncio
import contextvars
import json
import sys
import threading
import time
from pathlib import Path

_src = Path(__file__).resolve().parents[3]  # 01_源码_src
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

evidence: dict[str, object] = {}


# ---------------------------------------------------------------- 1) 复制上下文
# 复刻 registry.py 的真实写法，不 import registry（避免拉起整条工具链）。
_current: contextvars.ContextVar[str | None] = contextvars.ContextVar("probe_token", default=None)
_ev = threading.Event()


def _worker_sync() -> dict:
    """在线程池里同步执行，模拟工具函数体。"""
    seen = _current.get()
    first = _ev.is_set()
    # 主线程 50ms 后置位，这里轮询，验证跨线程可见
    deadline = time.time() + 2.0
    flipped_at = None
    while time.time() < deadline:
        if _ev.is_set():
            flipped_at = "observed"
            break
        time.sleep(0.01)
    return {"ctxvar_visible_in_thread": seen, "cancelled_at_entry": first, "cancel_observed": flipped_at}


async def main() -> int:
    loop = asyncio.get_running_loop()
    _current.set("req-abc")  # 相当于 set_current_token(token)
    copied = contextvars.copy_context()  # 相当于 registry.py:299

    def run_in_worker():
        return _worker_sync()

    fut = loop.run_in_executor(None, copied.run, run_in_worker)  # 相当于 registry.py:310
    await asyncio.sleep(0.05)
    _ev.set()  # 主线程置位，相当于 token.cancel()
    result = await asyncio.wait_for(fut, timeout=5)

    evidence["copy_context_path"] = result
    evidence["verdict_ctxvar_reaches_thread"] = result["ctxvar_visible_in_thread"] == "req-abc"
    evidence["verdict_cross_thread_event"] = result["cancel_observed"] == "observed"

    # ------------------------------------------------------------ 2) 不复制上下文会怎样
    _current.set("req-abc")
    direct = await asyncio.wait_for(loop.run_in_executor(None, _worker_sync), timeout=5)
    evidence["plain_run_in_executor"] = {"ctxvar_visible_in_thread": direct["ctxvar_visible_in_thread"]}
    evidence["verdict_plain_executor_loses_ctxvar"] = direct["ctxvar_visible_in_thread"] is None

    # ------------------------------------------------------------ 3) 嵌套：复制后新起子线程继承
    def spawn_thread_inside(body):
        out = {}

        def t():
            out["ctxvar"] = _current.get()

        th = threading.Thread(target=t)
        th.start()
        th.join()
        return out

    nested = await asyncio.wait_for(
        loop.run_in_executor(None, contextvars.copy_context().run, spawn_thread_inside, _worker_sync),
        timeout=5,
    )
    evidence["nested_thread_inherits"] = nested["ctxvar"]

    out = _src / "experiments" / "trace" / "results" / "probe_cancel_token_reaches_executor.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=2))
    print(f"\nwritten: {out}")

    # 三个"必须成立"的前提：令牌能进工具线程、置位跨线程可见、且必须显式 copy_context。
    required = {
        "ctxvar_reaches_tool_thread": evidence["verdict_ctxvar_reaches_thread"],
        "cross_thread_event_visible": evidence["verdict_cross_thread_event"],
        "plain_executor_would_lose_it": evidence["verdict_plain_executor_loses_ctxvar"],
    }
    # caveat：工具线程内再起的新线程不继承（threading.Thread 不继承调用方 context）。
    # 已核对 src/research/*.py 与 src/tools/*.py 无自建线程池/子线程（仅 registry.py:500
    # 为 sync 包装用的独立 event_loop，不涉及产物写入），故不影响 S1-01/05 的三道闸门。
    caveats = {"nested_thread_inherits": nested["ctxvar"] is not None}
    evidence["required"] = required
    evidence["caveats"] = caveats
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")

    ok = all(required.values())
    print(json.dumps({"required": required, "caveats": caveats}, ensure_ascii=False, indent=2))
    print(f"\nS1-01 design assumption: {'HOLDS' if ok else 'BROKEN'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
