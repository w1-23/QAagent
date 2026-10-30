# -*- coding: utf-8 -*-
"""S1：取消生命周期回归测试。

覆盖四层契约（都是"用户点了停止"这条链上的可观测行为）：

1. **令牌语义**（`src/core/cancel.py`）：跨线程可见、无令牌时空操作、
   检查点抛出的 `ToolCancelled` 不是失败。
2. **任务状态机**（`src/core/jobs.py`）：running → cancel_requested →
   cancelled(user)；已终态不可被降级；detach ≠ cancel；完成晚于停止请求
   时记 `completed` + `stop_note`。
3. **轮次状态**（`src/core/turn_status.py`）：`cancelled` 步骤 → 整轮
   `cancelled`；有真实失败时降级为 `partial`；不得被判成 `failed`。
4. **结果分类**（`src/tools/outcome.py`）：`[TOOL_CANCELLED]` → `cancelled`
   + 中性文案，不进失败前缀集合。

运行：`python -m pytest src/core/test_cancel_lifecycle.py -q`
"""
from __future__ import annotations

import asyncio
import contextvars
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from src.core.cancel import (  # noqa: E402
    CancelToken,
    ToolCancelled,
    bound_token,
    current_token,
    is_cancelled,
    raise_if_cancelled,
)
from src.core import jobs as jobs_mod  # noqa: E402
from src.core.turn_status import derive_turn_status  # noqa: E402
from src.tools.outcome import normalize_tool_outcome, safe_tool_step  # noqa: E402


# ── 1) 令牌语义 ────────────────────────────────────────────────────────
def test_token_is_visible_across_threads():
    """工具跑在 executor 线程里，停止必须跨线程生效。"""
    token = CancelToken()
    seen = {}

    def worker():
        seen["before"] = is_cancelled()
        token.cancel("user")
        seen["after"] = is_cancelled()
        seen["reason"] = token.reason

    ctx = contextvars.copy_context()
    ctx.run(lambda: __import__("src.core.cancel", fromlist=["set_current_token"])
            .set_current_token(token))
    t = threading.Thread(target=lambda: ctx.run(worker))
    t.start()
    t.join(timeout=5)
    assert seen["before"] is False
    assert seen["after"] is True
    assert seen["reason"] == "user"


def test_no_token_is_a_no_op():
    """脚本/单测里没有绑定令牌时，检查点必须静默——不能把"没有取消系统"当"已取消"。"""
    assert current_token() is None
    assert is_cancelled() is False
    raise_if_cancelled("no_token")  # 不抛


def test_checkpoint_raises_tool_cancelled_with_reason():
    token = CancelToken()
    token.cancel("detached_timeout")
    with bound_token(token):
        try:
            raise_if_cancelled("artifact:before_commit")
        except ToolCancelled as exc:
            assert exc.where == "artifact:before_commit"
            assert exc.reason == "detached_timeout"
        else:  # pragma: no cover
            raise AssertionError("cancelled token must raise at checkpoint")


# ── 2) 任务状态机 ──────────────────────────────────────────────────────
def _new_job() -> str:
    return jobs_mod.create_job(session_id="sess-s1-test", request_id="req-s1", job_type="chat")


def test_state_machine_running_to_cancelled():
    job_id = _new_job()
    assert (jobs_mod.get_job(job_id) or {}).get("status") == "running"
    assert jobs_mod.request_cancel(job_id, "user") is True
    assert (jobs_mod.get_job(job_id) or {}).get("status") == "cancel_requested"
    # 只记录请求不算终止：必须由收尾方迁移到终态
    assert jobs_mod.mark_stopped(job_id, "user") is True
    job = jobs_mod.get_job(job_id) or {}
    assert job.get("status") == "cancelled"
    assert job.get("stop_reason") == "user"
    assert job.get("finished_at")


def test_terminal_state_is_not_downgradable():
    job_id = _new_job()
    jobs_mod.mark_stopped(job_id, "user")
    # 迟到的"完成"不得把 cancelled 改回 completed
    assert jobs_mod.mark_completed_if_open(job_id) is False
    assert (jobs_mod.get_job(job_id) or {}).get("status") == "cancelled"
    # 无条件 update 也被守卫挡住（除非显式 force）
    assert jobs_mod.update_job(job_id, status="completed") is False
    assert (jobs_mod.get_job(job_id) or {}).get("status") == "cancelled"


def test_detach_is_not_cancel():
    job_id = _new_job()
    assert jobs_mod.mark_detached(job_id) is True
    job = jobs_mod.get_job(job_id) or {}
    assert job.get("status") == "detached"      # 断线 ≠ 取消
    assert job.get("detached_at")
    assert job.get("finished_at") in (None, "")
    # 宽限期到 → 停止原因必须是 detached_timeout，不能写成 user
    assert jobs_mod.mark_stopped(job_id, "detached_timeout") is True
    job = jobs_mod.get_job(job_id) or {}
    assert job.get("status") == "cancelled"
    assert job.get("stop_reason") == "detached_timeout"


def test_sse_disconnect_is_wired_to_the_client_close_hook():
    """断线必须走 sse-starlette 的 client_close_handler，而不是靠 CancelledError。

    真实事故（发布准入矩阵「SSE 断线」实测）：sse-starlette 3.4 收到
    http.disconnect 后是 `cancel_scope.cancel()` 掉驱动任务，生成器被
    aclose() 抛的是 **GeneratorExit**，只 catch CancelledError 的写法根本
    不执行，于是 finally 里 `graph_task.cancel()` 把任务记成
    `cancelled / stop_reason=internal`——用户会看到"任务莫名被取消"。

    这是接线契约，用源码断言钉住；行为侧由
    `experiments/trace/probes/probe_disconnect_e2e.py` 在真实 HTTP 上验证。
    """
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]   # 01_源码_src
    src = (root / "src/api/main.py").read_text(encoding="utf-8")
    # ① 断线钩子确实交给 EventSourceResponse
    assert "client_close_handler_callable=_on_client_close" in src
    # ② 生成器里两种断线异常都覆盖
    assert "except (asyncio.CancelledError, GeneratorExit):" in src
    # ③ finally 只看 conn_state，不再看旧的局部 keep_alive
    assert 'if not conn_state["keep_alive"]:' in src
    assert "if not keep_alive:" not in src
    # ④ 断线只标 detached，绝不写 user 取消
    assert "jobs_mod.mark_detached(job_id)" in src
    assert 'stop_reason = "user" if prev_status == "cancel_requested" else "internal"' in src


def test_completion_after_stop_request_is_recorded_as_completed():
    job_id = _new_job()
    jobs_mod.request_cancel(job_id, "user")
    assert jobs_mod.mark_completed_if_open(job_id, note="停止请求晚于完成") is True
    job = jobs_mod.get_job(job_id) or {}
    assert job.get("status") == "completed"
    assert job.get("stop_note") == "停止请求晚于完成"


def test_cancel_endpoint_contract_for_terminal_and_unknown_jobs():
    """`_cancel_one_job` 必须区分 accepted / already_finished / not_found。"""
    from src.api.main import _cancel_one_job

    job_id = _new_job()
    first = _cancel_one_job(job_id)
    assert first["accepted"] is True
    assert first["previous_status"] == "running"
    assert first["stop_reason"] == "user"
    # 重复点击幂等：不再报"又停止了一次"
    jobs_mod.mark_stopped(job_id, "user")
    second = _cancel_one_job(job_id)
    assert second["accepted"] is False
    assert second["status"] == "cancelled"
    assert second["note"] == "already_finished"
    unknown = _cancel_one_job("does-not-exist")
    assert unknown["accepted"] is False
    assert unknown["note"] == "not_found"


# ── 3) 轮次状态 ────────────────────────────────────────────────────────
def test_cancelled_steps_make_the_turn_cancelled():
    status = derive_turn_status("", [{"name": "本地检索", "status": "cancelled"}])
    assert status == "cancelled"


def test_real_failure_plus_stop_is_partial_not_failed():
    status = derive_turn_status(
        "", [{"name": "本地检索", "status": "cancelled"},
             {"name": "网页搜索", "status": "failed"}])
    assert status == "partial"


def test_completed_steps_plus_stop_is_still_cancelled():
    status = derive_turn_status(
        "", [{"name": "本地检索", "status": "complete"},
             {"name": "数据审查", "status": "cancelled"}])
    assert status == "cancelled"


def test_stopped_notice_is_not_incomplete():
    from src.core.turn_notices import is_incomplete, stopped_notice
    text = stopped_notice()
    assert "停止" in text
    # 停止是用户决定，不能借用"本轮未完成"前缀（否则统计里算系统失败）
    assert is_incomplete(text) is False


# ── 4) 结果分类 ────────────────────────────────────────────────────────
def test_tool_cancelled_is_neither_success_nor_failure():
    outcome = normalize_tool_outcome("local_hybrid_search", "[TOOL_CANCELLED] 任务已停止")
    assert outcome.status == "cancelled"
    assert outcome.reason_code == "stop_requested"
    step = safe_tool_step("deepseek_search", "[TOOL_CANCELLED] 任务已停止")
    assert step["status"] == "cancelled"
    assert "已停止" in step["summary"]
    assert "失败" not in step["summary"] and "错误" not in step["summary"]


# ── 5) 断线宽限期不会把执行杀掉（C1） ─────────────────────────────────
def test_detach_grace_stops_with_detached_timeout(monkeypatch):
    from src.api import main as api_main

    job_id = _new_job()
    jobs_mod.mark_detached(job_id)

    async def scenario():
        task = asyncio.ensure_future(asyncio.sleep(30))
        monkeypatch.setattr(api_main.settings, "AGENT_DETACH_GRACE_SEC", 0, raising=False)
        api_main._schedule_detach_grace(job_id, task)   # 0 秒 = 不设上限，直接返回
        await asyncio.sleep(0.05)
        assert not task.done()                          # 宽限期内不杀
        token = CancelToken()
        api_main._job_tokens[job_id] = token
        monkeypatch.setattr(api_main.settings, "AGENT_DETACH_GRACE_SEC", 0.01, raising=False)
        api_main._schedule_detach_grace(job_id, task)
        for _ in range(60):
            await asyncio.sleep(0.05)
            if task.done():
                break
        assert task.done()
        assert token.cancelled()
        job = jobs_mod.get_job(job_id) or {}
        assert job.get("status") == "cancelled"
        assert job.get("stop_reason") == "detached_timeout"
        api_main._job_tokens.pop(job_id, None)

    asyncio.run(scenario())


# ── 6) 工具层：停止后工具线程即使跑完，结果也不被采用（S1 闸门 1） ────
def test_result_of_tool_that_finished_after_stop_is_discarded():
    """慢工具（同步、在线程池里跑）无法被中断——正确语义是：跑完也不要结果。

    这条闸门是"点了停止还在写文件 / 还在登记成果"的最后一道防线。
    """
    from langchain_core.tools import Tool
    from src.tools import registry as tool_registry
    from src.tools.registry import _run_single_tool, register_tool

    def slow_tool(topic: str = "") -> str:
        time.sleep(0.6)
        return "TOOL_OUTPUT_SHOULD_NOT_BE_USED"

    tool = Tool(name="slow_probe", func=slow_tool, description="slow probe")
    # 必须登记，否则 HITL 白名单闸门会先拒（[ERR_HITL_REJECT]），测不到取消闸门。
    # 只做增量登记并在结束时摘掉，避免清空全局注册表影响其它用例。
    register_tool(tool, category="general", is_readonly=True)
    token = CancelToken()
    call = {"id": "call-slow-1", "name": "slow_probe", "args": {"topic": "x"}}

    async def scenario():
        with bound_token(token):
            task = asyncio.ensure_future(_run_single_tool(tool, call))
            await asyncio.sleep(0.2)      # 工具已在线程池里跑起来
            token.cancel("user")          # 这段时间里用户点了停止
            return await task

    try:
        msg = asyncio.run(scenario())
    finally:
        tool_registry._TOOL_SPECS.pop("slow_probe", None)
    assert "TOOL_OUTPUT_SHOULD_NOT_BE_USED" not in str(msg.content)
    assert str(msg.content).startswith("[TOOL_CANCELLED]")
    assert (msg.artifact or {}).get("cancelled") is True


def test_tool_dispatched_before_stop_reports_cancelled_in_outcome():
    """停止后的工具结果必须分类为 cancelled：既不进成功也不进失败。

    否则前端会把它画成红色失败——用户点的是停止，不是系统出错。
    """
    step = safe_tool_step("local_hybrid_search",
                          "[TOOL_CANCELLED] 任务已停止，工具 local_hybrid_search 的结果未采用。")
    assert step["status"] == "cancelled"
    assert step["reason_code"] == "stop_requested"


if __name__ == "__main__":  # pragma: no cover
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
