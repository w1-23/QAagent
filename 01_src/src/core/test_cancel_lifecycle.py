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

import pytest

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


@pytest.fixture(autouse=True)
def isolated_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(jobs_mod, "_DB_PATH", tmp_path / "jobs.sqlite3")


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
    root = pathlib.Path(__file__).resolve().parents[2]   # 01_src
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
    assert 'cancel_token.reason if cancel_token.cancelled()' in src
    assert '"user" if prev_status == "cancel_requested" else "internal"' in src


def test_completion_after_stop_request_is_recorded_as_completed():
    job_id = _new_job()
    jobs_mod.request_cancel(job_id, "user")
    assert jobs_mod.mark_completed_if_open(job_id, note="停止请求晚于完成") is True
    job = jobs_mod.get_job(job_id) or {}
    assert job.get("status") == "completed"
    assert job.get("stop_note") == "停止请求晚于完成"


def test_terminal_snapshot_round_trips_for_detached_recovery():
    job_id = _new_job()
    snapshot = {
        "display_status": "partial",
        "execution_status": "partial",
        "history_saved": True,
        "notices": ["部分结果"],
        "verified_artifacts": 1,
    }
    assert jobs_mod.save_terminal_snapshot(job_id, snapshot) is True
    job = jobs_mod.get_job(job_id) or {}
    assert jobs_mod.decode_terminal_snapshot(job) == snapshot
    assert jobs_mod.decode_terminal_snapshot(
        {"terminal_snapshot": "not-json"}) == {}
    assert jobs_mod.decode_terminal_snapshot(None) == {}


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


@pytest.fixture
def chat_runtime(monkeypatch, tmp_path):
    """Exercise the actual endpoint/stream without provider or live storage."""
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from src.api import main as api
    from src import auth, admin_audit
    from src.session import files
    from src.graph import graph as graph_module
    from src.core.chat_capacity import ChatCapacity
    from src.core import business_logger, diag, progress_bus

    state = SimpleNamespace(api=api, graph_calls=0, parsed=Mock(return_value=("", [], [])),
                            refund=Mock(), history=Mock(return_value=True), audit=Mock())
    from src.core import account_leases
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
            (123, "offline", b"salt", b"hash", "2026-01-01"))
    user = {"user_id": 123, "username": "offline", "role": "user"}
    monkeypatch.setattr(api, "current_user", lambda request: user)
    monkeypatch.setattr(api, "require_owned_session", lambda *a: None)
    monkeypatch.setattr(auth, "require_admin", lambda user: False)
    monkeypatch.setattr(auth, "check_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr(auth, "reserve_chat_request", lambda user: (True, 10))
    monkeypatch.setattr(auth, "release_chat_request", state.refund)
    monkeypatch.setattr(api.session_manager, "session_exists", lambda sid: False)
    monkeypatch.setattr(api.session_manager, "get_or_create_session", AsyncMock(side_effect=lambda sid, **kwargs: sid))
    monkeypatch.setattr(api.session_manager, "_save_messages_sync", state.history)
    monkeypatch.setattr(api, "_register_session_files", lambda *a: [])
    monkeypatch.setattr(files, "link_turn_files", lambda *a: None)
    monkeypatch.setattr(admin_audit, "start_request", lambda **k: None)
    monkeypatch.setattr(admin_audit, "finish_request", state.audit)
    monkeypatch.setattr(business_logger, "blog", lambda *a, **k: None)
    monkeypatch.setattr(diag, "diag", lambda *a, **k: None)
    monkeypatch.setattr(progress_bus, "log_sse_frame", lambda *a: None)
    monkeypatch.setattr(api, "_prepare_chat_attachments", state.parsed)
    gate = ChatCapacity()
    gate.max_active = 1
    state.gate = gate
    state.release = AsyncMock(wraps=gate.release)
    monkeypatch.setattr(gate, "release", state.release)
    monkeypatch.setattr(api, "chat_capacity", gate)

    class Graph:
        async def astream(self, *args, **kwargs):
            state.graph_calls += 1
            if getattr(state, "graph_entered", None):
                state.graph_entered.set()
            await asyncio.Event().wait()
            yield {}

    monkeypatch.setattr(graph_module, "build_graph", lambda mode: Graph())
    return state


async def _open_offline_chat(runtime):
    response = await runtime.api.chat_v2(runtime.api.ChatRequest(query="offline lifecycle"), object())
    job_id = next(iter(runtime.api._running_graph_tasks))
    return response, job_id


async def _drain_terminal(response):
    import json

    async def drain():
        return [event async for event in response.body_iterator]

    events = await asyncio.wait_for(drain(), timeout=3)
    done = [json.loads(event["data"]) for event in events if event.get("event") == "done"]
    assert len(done) == 1, events
    assert done[0]["turn_status"] == "cancelled"
    return done[0]


def _assert_chat_clean(runtime, job_id, *, acquired, graph_started=False):
    api = runtime.api
    assert job_id not in api._running_graph_tasks
    assert job_id not in api._job_tokens
    assert job_id not in api._pending_graph_starts
    assert runtime.release.await_count == int(acquired)
    assert runtime.refund.call_count == int(not graph_started)
    assert runtime.history.call_count == 1
    assert (jobs_mod.get_job(job_id) or {})["status"] == "cancelled"
    assert jobs_mod.decode_terminal_snapshot(jobs_mod.get_job(job_id))["display_status"] == "cancelled"


def test_cancel_before_worker_first_step_emits_terminal_and_refunds_once(chat_runtime):
    rt = chat_runtime

    async def scenario():
        response, job_id = await _open_offline_chat(rt)
        assert job_id in rt.api._pending_graph_starts
        assert rt.api._cancel_one_job(job_id)["accepted"]
        assert not rt.api._cancel_one_job(job_id)["accepted"]
        terminal = await _drain_terminal(response)
        assert terminal["stop_reason"] == "user"
        assert rt.graph_calls == 0 and rt.parsed.call_count == 0
        _assert_chat_clean(rt, job_id, acquired=False)

    asyncio.run(scenario())


def test_cancel_queued_request_removes_waiter_and_finishes_sse(chat_runtime):
    rt = chat_runtime

    async def scenario():
        await rt.gate.acquire()
        response, job_id = await _open_offline_chat(rt)
        while True:
            event = await asyncio.wait_for(anext(response.body_iterator), timeout=3)
            if event.get("event") == "status" and "queued" in event["data"]:
                break
        rt.api._cancel_one_job(job_id)
        rt.api._cancel_one_job(job_id)
        await _drain_terminal(response)
        snapshot = await rt.gate.snapshot()
        assert snapshot["active"] == 1 and snapshot["queued"] == 0
        assert rt.graph_calls == 0 and rt.parsed.call_count == 0
        _assert_chat_clean(rt, job_id, acquired=False)
        await rt.gate.release()

    asyncio.run(scenario())


def test_cancel_exactly_when_slot_is_granted_releases_once(chat_runtime, monkeypatch):
    rt = chat_runtime
    original_acquire = rt.gate.acquire

    async def acquire(*args, **kwargs):
        await original_acquire(*args, **kwargs)
        # Cancel the owner before shield resumes with the acquired slot.
        job_id = next(iter(rt.api._running_graph_tasks))
        rt.api._cancel_one_job(job_id)

    monkeypatch.setattr(rt.gate, "acquire", acquire)

    async def scenario():
        response, job_id = await _open_offline_chat(rt)
        await _drain_terminal(response)
        assert (await rt.gate.snapshot())["active"] == 0
        assert rt.graph_calls == 0 and rt.parsed.call_count == 0
        _assert_chat_clean(rt, job_id, acquired=True)

    asyncio.run(scenario())


def test_cancel_during_attachment_parse_discards_late_result(chat_runtime, monkeypatch):
    rt = chat_runtime
    unblock = threading.Event()
    finished = threading.Event()

    async def scenario():
        entered = asyncio.Event()
        loop = asyncio.get_running_loop()

        def parse(attachments):
            loop.call_soon_threadsafe(entered.set)
            try:
                assert unblock.wait(3)
                return "late content", [], []
            finally:
                finished.set()

        monkeypatch.setattr(rt.api, "_prepare_chat_attachments", parse)
        response, job_id = await _open_offline_chat(rt)
        await asyncio.wait_for(entered.wait(), timeout=3)
        rt.api._cancel_one_job(job_id)
        try:
            await _drain_terminal(response)
            assert not finished.is_set()
            assert rt.graph_calls == 0
            _assert_chat_clean(rt, job_id, acquired=True)
        finally:
            unblock.set()
            assert await asyncio.to_thread(finished.wait, 3)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["after_parse", "before_graph"])
def test_token_only_cancel_at_launch_checkpoints_never_runs_graph(chat_runtime, monkeypatch, stage):
    rt = chat_runtime
    original_update = jobs_mod.update_job

    def stop_token():
        token = next(iter(rt.api._job_tokens.values()))
        token.cancel("user")

    if stage == "after_parse":
        def parse(attachments):
            stop_token()
            return "", [], []
        monkeypatch.setattr(rt.api, "_prepare_chat_attachments", parse)
    else:
        def update(job_id, **fields):
            if fields.get("current_step") == "starting":
                stop_token()
            return original_update(job_id, **fields)
        monkeypatch.setattr(jobs_mod, "update_job", update)

    async def scenario():
        response, job_id = await _open_offline_chat(rt)
        await _drain_terminal(response)
        assert rt.graph_calls == 0
        _assert_chat_clean(rt, job_id, acquired=True)

    asyncio.run(scenario())


def test_cancel_running_graph_keeps_quota_and_releases_slot(chat_runtime):
    rt = chat_runtime

    async def scenario():
        rt.graph_entered = asyncio.Event()
        response, job_id = await _open_offline_chat(rt)
        await asyncio.wait_for(rt.graph_entered.wait(), timeout=3)
        rt.api._cancel_one_job(job_id)
        await _drain_terminal(response)
        assert rt.graph_calls == 1
        _assert_chat_clean(rt, job_id, acquired=True, graph_started=True)

    asyncio.run(scenario())


def test_stop_history_failure_still_emits_terminal(chat_runtime):
    rt = chat_runtime
    rt.history.side_effect = RuntimeError("offline persistence failure")

    async def scenario():
        response, job_id = await _open_offline_chat(rt)
        rt.api._cancel_one_job(job_id)
        terminal = await _drain_terminal(response)
        assert terminal["history_saved"] is False
        _assert_chat_clean(rt, job_id, acquired=False)

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", ["queue_full", "queue_timeout", "parse_error"])
def test_pregraph_failure_closes_stream_and_refunds_once(chat_runtime, monkeypatch, failure):
    rt = chat_runtime
    if failure != "parse_error":
        async def acquire(**kwargs):
            if failure == "queue_full":
                raise OverflowError("offline queue full")
            raise TimeoutError("offline queue timeout")
        monkeypatch.setattr(rt.gate, "acquire", acquire)
    else:
        rt.parsed.side_effect = RuntimeError("offline parse failure")

    async def scenario():
        response, job_id = await _open_offline_chat(rt)

        async def drain():
            return [event async for event in response.body_iterator]

        events = await asyncio.wait_for(drain(), timeout=3)
        assert sum(e.get("event") == "error" for e in events) == 1
        assert not any(e.get("event") == "done" for e in events)
        assert (jobs_mod.get_job(job_id) or {})["status"] == "failed"
        assert rt.refund.call_count == 1
        assert rt.release.await_count == int(failure == "parse_error")
        assert job_id not in rt.api._running_graph_tasks
        assert job_id not in rt.api._job_tokens
        assert rt.graph_calls == 0

    asyncio.run(scenario())


def test_detached_graph_survives_disconnect_then_can_be_cancelled(chat_runtime, monkeypatch):
    rt = chat_runtime
    monkeypatch.setattr(rt.api.settings, "AGENT_DETACH_GRACE_SEC", 0)

    async def scenario():
        rt.graph_entered = asyncio.Event()
        response, job_id = await _open_offline_chat(rt)
        await anext(response.body_iterator)
        await asyncio.wait_for(rt.graph_entered.wait(), timeout=3)
        task = rt.api._running_graph_tasks[job_id]
        await response.body_iterator.aclose()
        assert not task.done()
        assert (jobs_mod.get_job(job_id) or {})["status"] == "detached"
        rt.api._cancel_one_job(job_id)
        await asyncio.wait_for(task, timeout=3)
        _assert_chat_clean(rt, job_id, acquired=True, graph_started=True)

    asyncio.run(scenario())


def test_detach_timeout_reason_survives_request_finalizer(chat_runtime):
    rt = chat_runtime

    async def scenario():
        rt.graph_entered = asyncio.Event()
        response, job_id = await _open_offline_chat(rt)
        await asyncio.wait_for(rt.graph_entered.wait(), timeout=3)
        rt.api._job_tokens[job_id].cancel("detached_timeout")
        jobs_mod.mark_stopped(job_id, "detached_timeout")
        rt.api._running_graph_tasks[job_id].cancel()
        terminal = await _drain_terminal(response)
        assert terminal["stop_reason"] == "detached_timeout"
        _assert_chat_clean(rt, job_id, acquired=True, graph_started=True)

    asyncio.run(scenario())


def test_successful_graph_still_emits_one_terminal_and_releases_once(chat_runtime, monkeypatch):
    import json
    from src.graph import graph as graph_module
    rt = chat_runtime

    class Graph:
        async def astream(self, *args, **kwargs):
            yield {"supervisor": {"answer": "offline answer", "tool_status": []}}
            yield {"session_save": {"_trace": {"history_saved": True, "generation_ms": 12}}}

    monkeypatch.setattr(graph_module, "build_graph", lambda mode: Graph())

    async def scenario():
        response, job_id = await _open_offline_chat(rt)

        async def drain():
            return [event async for event in response.body_iterator]

        events = await asyncio.wait_for(drain(), timeout=3)
        done = [json.loads(e["data"]) for e in events if e.get("event") == "done"]
        assert len(done) == 1 and done[0]["answer"] == "offline answer"
        assert done[0]["turn_status"] == "completed"
        assert (jobs_mod.get_job(job_id) or {})["status"] == "completed"
        assert rt.release.await_count == 1 and rt.refund.call_count == 0
        assert job_id not in rt.api._running_graph_tasks and job_id not in rt.api._job_tokens

    asyncio.run(scenario())


def test_detach_grace_does_not_interrupt_an_existing_user_stop(monkeypatch):
    from src.api import main as api
    job_id = _new_job()
    jobs_mod.request_cancel(job_id, "user")
    token = CancelToken()
    token.cancel("user")
    monkeypatch.setitem(api._job_tokens, job_id, token)
    monkeypatch.setattr(api.settings, "AGENT_DETACH_GRACE_SEC", 0.01)

    async def expire(shielded, timeout):
        shielded.cancel()
        raise asyncio.TimeoutError

    monkeypatch.setattr(api.asyncio, "wait_for", expire)

    async def scenario():
        task = asyncio.create_task(asyncio.Event().wait())
        api._schedule_detach_grace(job_id, task)
        await asyncio.sleep(0)
        assert not task.done() and task.cancelling() == 0
        assert (jobs_mod.get_job(job_id) or {})["status"] == "cancel_requested"
        assert token.reason == "user"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
