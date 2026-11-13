"""Tool Registry — tool classification, partitioned concurrency, offload"""
import asyncio
import json
import contextvars
import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Any, Optional

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool

from src.config import PROJECT_ROOT, settings

logger = logging.getLogger(__name__)

AUTO_OFFLOAD_THRESHOLD = 15000
# v8.13: 锚定 PROJECT_ROOT（此前 CWD 相对——从非仓库根启动时 offload 写入错误位置）
OFFLOAD_DIR = (PROJECT_ROOT / settings.WORKSPACE_DIR / "tmp").resolve()

_offload_created_files: list[Path] = []

# v8.4.5: ask 模式授权等待——权限拒绝时工具执行挂起等待前端卡片授权，
# 授权到达后在同一执行内继续（不再要求用户整轮重跑）。
_pending_permission_events: dict = {}


def signal_permission_granted(session_id: str, tool_name: str) -> None:
    """授权到达后唤醒对应挂起事件（由 /api/v2/permission/grant 端点调用）。"""
    for key in (f"{session_id}|{tool_name}", f"{session_id}|*"):
        ev = _pending_permission_events.pop(key, None)
        if ev is not None:
            try:
                ev.set()
            except Exception:
                pass


@dataclass
class ToolSpec:
    func: Callable
    category: str = "general"
    is_readonly: bool = True
    concurrency_key: Optional[str] = None
    write_scope: Optional[str] = None
    authorization: str = ""


_TOOL_SPECS: dict[str, ToolSpec] = {}


def clear_tool_registry() -> None:
    """Clear the process-local registry before loading the code-owned manifest."""
    _TOOL_SPECS.clear()


def register_tool(tool: BaseTool, category: str = "general", is_readonly: bool = False,
                  concurrency_key: Optional[str] = None,
                  write_scope: Optional[str] = None,
                  authorization: str = ""):
    if write_scope not in {None, "session_artifact", "account_record"}:
        raise ValueError(f"unsupported write scope for {tool.name}: {write_scope}")
    if authorization not in {"", "explicit_user_request"}:
        raise ValueError(f"unsupported authorization for {tool.name}: {authorization}")
    if is_readonly and write_scope is not None:
        raise ValueError(f"read-only tool {tool.name} cannot declare a write scope")
    if not is_readonly and write_scope is None:
        raise ValueError(f"writable tool {tool.name} must declare an explicit write scope")
    _TOOL_SPECS[tool.name] = ToolSpec(
        func=tool, category=category, is_readonly=is_readonly,
        concurrency_key=concurrency_key, write_scope=write_scope,
        authorization=authorization)


def get_tool_spec(name: str) -> Optional[ToolSpec]:
    return _TOOL_SPECS.get(name)


def concurrency_key_for(name: str) -> str:
    """The declared resource group, or "" when the tool does not contend."""
    spec = get_tool_spec(str(name or ""))
    return str(getattr(spec, "concurrency_key", "") or "")


def get_tools_by_category(category: str) -> list[str]:
    return [name for name, spec in _TOOL_SPECS.items() if spec.category == category]


def _classify_error(e: Exception, tool: BaseTool | None = None) -> str:
    """统一错误回传 (v8.3.1): [ERR_类别] 原因 + 建议策略，让 LLM 对症下药。"""
    from src.core.executors import ExecutorSaturatedError
    if isinstance(e, ExecutorSaturatedError):
        return "[ERR_BUSY] 服务当前繁忙，工具未进入执行队列。请稍后重试或缩小任务范围。"
    if isinstance(e, TypeError) and any(token in str(e) for token in (
            "unexpected keyword argument", "required positional argument",
            "required keyword-only argument", "multiple values for argument",
            "positional argument but", "positional arguments but")):
        # 参数名/元数不匹配是可自纠的错误；函数内部的 TypeError 仍是执行故障。
        from src.tools.argument_check import schema_fields
        name = getattr(tool, "name", "") or ""
        listed = "、".join(sorted(schema_fields(tool))) if tool is not None else ""
        message = f"[ERR_ARG] 工具 {name or '调用'} 参数不匹配：{e}。本次未执行。"
        if listed:
            message += f"合法参数：{listed}。"
        return message + "请改用上述参数名后重试，其余步骤照常继续。"
    if isinstance(e, ImportError):
        pkg = getattr(e, "name", str(e))
        return f"[ERR_MISSING_DEP] 缺少依赖库: {pkg}。建议: pip install {pkg} 后重试"
    if isinstance(e, FileNotFoundError):
        return f"[ERR_FILE_NOT_FOUND] 文件不存在: {e.filename}。建议: 检查路径拼写与文件位置"
    if isinstance(e, PermissionError):
        # v8.4.5: 操作系统权限错误与 HITL 拒绝区分（[ERR_PERMISSION] vs [ERR_HITL_REJECT]）
        return f"[ERR_PERMISSION] 权限不足: {e}。建议: 检查文件权限或改用工作区内路径"
    if isinstance(e, (asyncio.TimeoutError, TimeoutError)):
        return f"[ERR_NETWORK] 请求超时: {e}。建议: 稍后重试或改用本地源"
    msg = str(e)
    if "timeout" in msg.lower() or "connection" in msg.lower():
        return f"[ERR_NETWORK] 网络错误: {e}。建议: 稍后重试或改用本地源"
    if isinstance(e, json.JSONDecodeError):
        return ("[ERR_PARSE] 参数 JSON 不完整（通常在输出长度上限处被截断）。"
                "建议：只保留必要字段、减少单次条数（如每批 ≤20 条）后重试。")
    if isinstance(e, ValueError):
        # 工具主动抛出的参数/数据校验错误是可执行的说明，不应显示成“工具执行异常”。
        return f"[ERR_ARG] {e}。建议：按提示修正该参数后重试，其余步骤照常继续。"
    if "parse" in msg.lower() or "decode" in msg.lower() or "json" in msg.lower():
        return f"[ERR_PARSE] 数据解析错误: {e}。建议: 检查输入参数格式"
    return f"[ERR_PARSE] 工具执行异常: {e}。建议: 检查参数是否合理"


def _offload_large_result(result: str, tool_name: str) -> str:
    """Preserve raw output until the graph records it, then clamps the model copy.

    ``_run_regular_calls`` publishes the original result to ``tool_results``
    before applying ``clamp_for_model``. The existing authenticated
    ``/api/v2/session/{session_id}/tool-result/{tool_call_id}`` endpoint reads
    that evidence record (including its retention/truncation metadata).
    Shortening here would make that endpoint store only an offload notice.

    Keep this call-site shim, but never replace content with a tmp path: those
    files are not account-library objects and have no registered reader tool.
    """
    return result


def get_offload_file_list() -> list[Path]:
    return list(_offload_created_files)


def cleanup_offload_files() -> int:
    count = 0
    for p in _offload_created_files:
        try:
            if p.exists():
                p.unlink()
                count += 1
        except Exception as e:
            logger.warning(f"[Cleanup] 删除 {p} 失败: {e}")
    _offload_created_files.clear()
    if count > 0:
        logger.info(f"[Cleanup] 已清理 {count} 个 offload 临时文件")
    return count


def _is_workspace_output_path(path: str) -> bool:
    """write_local_file 路径是否落在 workspace/output 内（auto_workspace 免询问依据）。

    v8.13: 收敛到 core.path_policy.is_output_path（统一 is_relative_to，原
    startswith 同前缀漏洞 registry 侧残留一并消除）。
    """
    from src.core.path_policy import is_output_path
    return is_output_path(path)


async def _check_tool_sandbox(tool_name: str, args: dict) -> Optional[str]:
    """沙箱/权限检查（v8.3.3 fail-closed 内置 + v8.4.3 结构化权限）。

    Policy:
      - SANDBOX_ENABLED=False → allow (operator-controlled local setting)
      - registered read-only tools → allow
      - explicitly scoped session/account writes → allow only through the
        Agent's request-scoped tool routing
      - unclassified writes → permission_mode:
          auto_workspace: write_local_file 且路径在 workspace/output 内 → 放行
          ask: 查 permission_grants（workspace/once/session 范围）→ 放行或
              拒绝并发 permission_request 事件（前端审批卡片）
          deny: 一律拒绝
      - 检查异常 → 拒绝执行（fail-closed）
    """
    try:
        spec = get_tool_spec(tool_name)
        if spec is None:
            return f"[ERR_HITL_REJECT] 工具 {tool_name} 未在活动工具注册表中登记，已拒绝执行"
        # Execution-time authorization is a product rule, not a sandbox mode.
        # A visible tool whose side effect requires an explicit user request is
        # rejected with a correlated receipt when the request did not contain
        # that request. This cannot be bypassed by disabling the sandbox.
        if spec.authorization == "explicit_user_request":
            from src.core.tracing import request_authorizes
            if not request_authorizes(tool_name):
                return (f"[ERR_HITL_REJECT] 工具 {tool_name} 需要用户在本轮明确请求；"
                        f"当前请求未包含该授权，已拒绝执行。请先询问用户是否需要保存。")
        if not getattr(settings, "SANDBOX_ENABLED", True):
            return None
        if spec.is_readonly:
            return None
        # Write capability is explicit metadata on the same declaration that
        # registers the tool. The Agent only exposes these tools for an
        # explicit user workflow; the sandbox does not infer permission from
        # a category label or maintain a second tool-name allowlist.
        if spec.write_scope in {"session_artifact", "account_record"}:
            return None
        # Any registered write without a trusted bounded scope must go through
        # the permission workflow, regardless of its name or category.

        mode = getattr(settings, "PERMISSION_MODE", "auto_workspace") or "auto_workspace"
        path = str((args or {}).get("path", "") or "")

        if mode == "auto_workspace":
            if tool_name == "write_local_file" and _is_workspace_output_path(path):
                return None
            return (f"[ERR_HITL_REJECT] 沙箱拒绝: 工具 {tool_name} 属危险操作类，"
                    f"仅允许写入 workspace/output（permission_mode=auto_workspace）")

        if mode == "ask":
            try:
                from src.session.manager import session_manager
                from src.core.tracing import get_session_id
                sid = get_session_id()
                granted = await asyncio.to_thread(
                    session_manager.consume_grant, sid, tool_name, path)
                if granted:
                    return None
            except Exception:
                pass
            # 发结构化权限请求事件 → 前端审批卡片
            try:
                from src.core.progress_bus import emit_encoded
                emit_encoded("permission_request", {
                    "tool_name": tool_name,
                    "args": {k: str(v)[:200] for k, v in (args or {}).items()},
                })
            except Exception:
                pass
            # v8.4.5: 挂起等待授权（授权后同一执行内继续；超时按拒绝处理）
            try:
                from src.core.tracing import get_session_id as _get_sid
                sid = _get_sid()
                wait_key = f"{sid}|{tool_name}"
                ev = asyncio.Event()
                _pending_permission_events[wait_key] = ev
                try:
                    await asyncio.wait_for(ev.wait(), timeout=settings.PERMISSION_WAIT_SEC)
                    granted = await asyncio.to_thread(
                        session_manager.consume_grant, sid, tool_name, path)
                    if granted:
                        logger.info(f"[Sandbox] 权限已授权，同执行内继续: tool={tool_name}")
                        return None
                except asyncio.TimeoutError:
                    logger.warning(
                        f"[Sandbox] 权限等待超时({settings.PERMISSION_WAIT_SEC}s): {tool_name}")
                finally:
                    _pending_permission_events.pop(wait_key, None)
            except Exception:
                pass
            return (f"[ERR_HITL_REJECT] 权限未授权: 工具 {tool_name} "
                    f"path={path[:120] or '(无)'}（permission_mode=ask，"
                    f"请在弹出的审批卡片中点击允许）")

        # deny 或未知模式
        return (f"[ERR_HITL_REJECT] 沙箱拒绝: 工具 {tool_name} 属危险操作类，"
                f"未获执行批准（permission_mode={mode}）")
    except Exception as e:
        logger.error(f"[Sandbox] 检查异常(fail-closed): {e}")
        return f"[ERR_HITL_REJECT] 沙箱检查失败，已拒绝执行: {e}"


async def _invoke_tool_with_ctx(ctx: contextvars.Context, tool: BaseTool, args: dict):
    """工具执行时保持请求 contextvar（v8.3.4）。

    run_in_executor 线程不继承 contextvars → 工具内部的 emit_*（tool_progress 等）
    会落到全局兜底队列而丢失（执行卡片缺失的根因）。
    async 工具协程天然继承 contextvar，直接 await；sync 工具用 ctx.run 注入。
    """
    if getattr(tool, "coroutine", None) is not None:
        return await tool.ainvoke(args)
    from src.core.executors import TOOL_EXECUTOR
    return await asyncio.get_running_loop().run_in_executor(
        TOOL_EXECUTOR, lambda: ctx.run(lambda: tool.invoke(args)))


def _tool_exec_timeout(tool_name: str) -> float | None:
    """Bounded outer wait, always longer than the active provider HTTP budget."""
    base = getattr(settings, "TOOL_EXEC_TIMEOUT_SEC", 60)
    try:
        base = float(base)
    except (TypeError, ValueError):
        base = 60.0
    if base <= 0:
        return None
    try:
        overrides = getattr(settings, "TOOL_TIMEOUTS", None) or {}
        if not isinstance(overrides, dict):
            overrides = {}
        required_floor = int(base)
        if tool_name == "deepseek_search":
            http_budget = getattr(settings, "WEB_SEARCH_TIMEOUT", 90) or 90
            required_floor = max(required_floor, int(http_budget) + 10)
        elif tool_name in {"deepseek_fetch", "deepseek_fetch_paper"}:
            http_budget = getattr(settings, "SEARCH_FETCH_TIMEOUT_SEC", 45) or 45
            required_floor = max(required_floor, int(http_budget) + 10)
        configured = overrides.get(tool_name, required_floor)
        t = max(int(configured or required_floor), required_floor)
        return min(max(t, 1), 600)
    except (TypeError, ValueError):
        return int(base)


def _resume_hint(tool_name: str, args: dict) -> str:
    """给出可续跑的具体位置，而不是笼统的"可缩小范围后重试"。

    这些工具本来就支持窗口参数与服务端记忆，超时后接着上次的位置继续即可，
    不需要整轮重跑，也不会重复已完成的工作。
    """
    args = args if isinstance(args, dict) else {}

    def _int(name: str) -> int:
        try:
            return max(0, int(args.get(name) or 0))
        except (TypeError, ValueError):
            return 0

    if tool_name == "verify_uploaded_references":
        start = _int("start_index")
        limit = _int("max_rows")
        window = limit or 10
        return (f"续跑方式：再次调用 verify_uploaded_references，"
                f"传 start_index={start}、max_rows={window}，从同一窗口重试。"
                f"超时不代表该窗口已完成，不能跳过；已核验条目在 6 小时内有服务端记忆。"
                f"请沿用同一 file_index/references_json；取得成功回执后再按返回的位置继续。")
    if tool_name == "read_account_file":
        start = _int("start_char")
        size = _int("max_chars") or 9000
        return (f"续跑方式：再次调用 read_account_file，传 start_char={start}、"
                f"max_chars={size}，重试当前尚未返回的页；"
                f"取得内容后再使用回执给出的下一位置，不要跳过未读内容。")
    if tool_name == "inspect_uploaded_table":
        return ("续跑方式：用 sheet_name 指定单张工作表后重试，"
                "不要一次读取整个工作簿。")
    return "可缩小本次范围（减少条数、指定单张工作表或分段读取）后重试。"


def _timeout_message(tool_name: str, exec_timeout, args: dict) -> str:
    return (f"[ERR_TIMEOUT] 工具 {tool_name} 等待超过 {exec_timeout}s；"
            f"请求已停止等待，后台执行仍受限资源池管理。"
            + _resume_hint(tool_name, args))


def _cancelled_result(tool_name: str, tool_call_id: str, where: str) -> ToolMessage:
    """S1：请求已取消时统一的工具结果。

    **不是失败**：不计入失败率、不映射 `[TOOL_ERROR]`、不触发重试提示。
    artifact 标记 `cancelled=True` 供上游丢弃，避免迟到结果被登记为成果。
    """
    return ToolMessage(
        content=f"[TOOL_CANCELLED] 任务已停止，工具 {tool_name} 的结果未采用。",
        tool_call_id=tool_call_id,
        name=tool_name,
        artifact={"cancelled": True, "cancel_stage": where},
    )


def _note_late_result(tool_name: str, where: str) -> None:
    """记录"工具在取消后才返回"——用于事后核查是否仍有副作用。"""
    try:
        from src.core.diag import diag
        diag("late_tool_result", tool=tool_name, stage=where)
    except Exception:  # pragma: no cover - 诊断永不阻塞主流程
        pass


def _is_cancel_exc(exc: BaseException) -> bool:
    try:
        from src.core.cancel import ToolCancelled
    except Exception:  # pragma: no cover
        return False
    return isinstance(exc, ToolCancelled)


async def _run_single_tool(tool: BaseTool, tool_call: dict) -> ToolMessage:
    tool_name = tool_call.get("name", tool.name)
    args = tool_call.get("args", {})

    # S1 闸门 0：已取消的请求不再派发新工具（避免"点了停止还在查/还在写"）。
    from src.core.cancel import current_token
    token = current_token()
    if token is not None and token.cancelled():
        _note_late_result(tool_name, "before_dispatch")
        return _cancelled_result(tool_name, tool_call["id"], "before_dispatch")

    reject_reason = await _check_tool_sandbox(tool_name, args)
    if reject_reason:
        return ToolMessage(content=reject_reason, tool_call_id=tool_call["id"], name=tool_name, artifact={})

    # content_and_artifact tools bypass pydantic below (raw_func(**args)), so a
    # wrong keyword would surface as an unactionable TypeError. Check the
    # declared schema first and name the legal parameters.
    from src.tools.argument_check import check_tool_arguments
    argument_error = check_tool_arguments(tool, tool_name, args)
    if argument_error:
        return ToolMessage(content=argument_error, tool_call_id=tool_call["id"],
                           name=tool_name, artifact={})

    exec_timeout = _tool_exec_timeout(tool_name)

    # 注意：对于 response_format="content_and_artifact" 的工具，tool.ainvoke()
    # 会丢弃 artifact 只返回 content 字符串。
    # 必须直接调用底层函数才能获取完整的 (content, artifact) 元组。
    response_format = getattr(tool, "response_format", None)
    raw_func = getattr(tool, "func", None)
    ctx = contextvars.copy_context()
    # 记下真正在执行的工具名（写进 ctx，随 ctx.run 传入工具线程）。工具内部的
    # 成果登记用它记录生产者，使"文件由谁产出"可核查。
    from src.core.tracing import set_current_tool_name
    ctx.run(set_current_tool_name, tool_name)

    if response_format == "content_and_artifact" and raw_func is not None:
        try:
            from src.core.executors import TOOL_EXECUTOR
            content, artifact = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    TOOL_EXECUTOR, lambda: ctx.run(lambda: raw_func(**args))
                ),
                timeout=exec_timeout,
            )
        except asyncio.TimeoutError:
            logger.error(f"[PartitionedNode] 工具 {tool.name} 执行超时(>{exec_timeout}s)")
            return ToolMessage(
                content=_timeout_message(tool.name, exec_timeout, args),
                tool_call_id=tool_call["id"], name=tool.name, artifact={})
        except Exception as e:
            if _is_cancel_exc(e):
                _note_late_result(tool_name, "artifact_branch_exc")
                return _cancelled_result(tool_name, tool_call["id"], "cancelled_in_tool")
            logger.error(f"[PartitionedNode] 工具 {tool.name} 执行失败: {e}")
            error_msg = _classify_error(e, tool)
            return ToolMessage(content=error_msg, tool_call_id=tool_call["id"], name=tool.name, artifact={})
        # S1 闸门 1：工具线程跑完了，但请求已被取消 → 结果不采用、artifact 丢弃。
        if token is not None and token.cancelled():
            _note_late_result(tool_name, "artifact_branch")
            return _cancelled_result(tool_name, tool_call["id"], "after_exec")
        if isinstance(content, str) and tool_name not in {
                "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "local_hybrid_search"}:
            content = _offload_large_result(content, tool_name)
        return ToolMessage(content=content, artifact=artifact, tool_call_id=tool_call["id"], name=tool_name)
    else:
        try:
            result = await asyncio.wait_for(_invoke_tool_with_ctx(ctx, tool, args),
                                            timeout=exec_timeout)
        except asyncio.TimeoutError:
            logger.error(f"[PartitionedNode] 工具 {tool.name} 执行超时(>{exec_timeout}s)")
            return ToolMessage(
                content=_timeout_message(tool.name, exec_timeout, args),
                tool_call_id=tool_call["id"], name=tool.name, artifact={})
        except Exception as e:
            if _is_cancel_exc(e):
                _note_late_result(tool_name, "plain_branch_exc")
                return _cancelled_result(tool_name, tool_call["id"], "cancelled_in_tool")
            logger.error(f"[PartitionedNode] 工具 {tool.name} 执行失败: {e}")
            error_msg = _classify_error(e, tool)
            return ToolMessage(content=error_msg, tool_call_id=tool_call["id"], name=tool.name, artifact={})

        # S1 闸门 1：工具线程跑完了，但请求已被取消 → 结果不采用。
        if token is not None and token.cancelled():
            _note_late_result(tool_name, "plain_branch")
            return _cancelled_result(tool_name, tool_call["id"], "after_exec")

        if not isinstance(result, ToolMessage):
            if isinstance(result, str):
                result = ToolMessage(content=_offload_large_result(result, tool_name), tool_call_id=tool_call["id"], name=tool_name)
            elif isinstance(result, tuple) and len(result) == 2:
                content, artifact = result
                if isinstance(content, str) and tool_name not in {
                        "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "local_hybrid_search"}:
                    content = _offload_large_result(content, tool_name)
                result = ToolMessage(content=content, artifact=artifact, tool_call_id=tool_call["id"], name=tool_name)
            else:
                result = ToolMessage(content=str(result), tool_call_id=tool_call["id"], name=tool_name)
        elif isinstance(result.content, str) and tool_name not in {
                "deepseek_search", "deepseek_fetch", "deepseek_fetch_paper", "local_hybrid_search"}:
            result.content = _offload_large_result(result.content, tool_name)

        return result


async def run_tool_checked(tool: BaseTool, args: dict) -> str:
    """v8.13 第四批: 工具执行统一出口——供图内联/流水线直调复用。

    此前 write_pipeline 直调 write_local_file.func、expert_graph 内联
    read/pdf/write 绕过 _check_tool_sandbox 与超时/offload（ask 审批模式对
    这些路径形同虚设，且大文件阻塞事件循环）。统一经此出口：
    - 沙箱/权限检查（fail-closed）
    - 超时中断（TOOL_EXEC_TIMEOUT_SEC）
    - 大结果 offload + 错误分类
    Returns: content 字符串。以 [ERR_* 或 "Error:" 前缀判失败。
    """
    tool_name = tool.name
    reject_reason = await _check_tool_sandbox(tool_name, args)
    if reject_reason:
        return reject_reason

    from src.tools.argument_check import check_tool_arguments
    argument_error = check_tool_arguments(tool, tool_name, args)
    if argument_error:
        return argument_error

    exec_timeout = _tool_exec_timeout(tool_name)
    ctx = contextvars.copy_context()
    raw_func = getattr(tool, "func", None)
    response_format = getattr(tool, "response_format", None)

    # S1 闸门 0：已取消则不再派发（内联/流水线路径同样适用）。
    from src.core.cancel import current_token
    token = current_token()
    if token is not None and token.cancelled():
        _note_late_result(tool_name, "checked_before_dispatch")
        return f"[TOOL_CANCELLED] 任务已停止，工具 {tool_name} 的结果未采用。"

    try:
        if response_format == "content_and_artifact" and raw_func is not None:
            from src.core.executors import TOOL_EXECUTOR
            content, _artifact = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    TOOL_EXECUTOR, lambda: ctx.run(lambda: raw_func(**args))),
                timeout=exec_timeout)
            if token is not None and token.cancelled():
                _note_late_result(tool_name, "checked_after_exec")
                return f"[TOOL_CANCELLED] 任务已停止，工具 {tool_name} 的结果未采用。"
            return content if isinstance(content, str) else str(content)
        result = await asyncio.wait_for(_invoke_tool_with_ctx(ctx, tool, args),
                                        timeout=exec_timeout)
        if token is not None and token.cancelled():
            _note_late_result(tool_name, "checked_after_exec")
            return f"[TOOL_CANCELLED] 任务已停止，工具 {tool_name} 的结果未采用。"
        if isinstance(result, ToolMessage):
            content = result.content
        elif isinstance(result, str):
            content = result
        elif isinstance(result, tuple) and len(result) == 2:
            content = result[0]
        else:
            content = str(result)
        return content if isinstance(content, str) else str(content)
    except asyncio.TimeoutError:
        return (f"[ERR_TIMEOUT] 工具 {tool_name} 执行超过 {exec_timeout}s，已中断。"
                + _resume_hint(tool_name, args))
    except Exception as e:
        if _is_cancel_exc(e):
            _note_late_result(tool_name, "checked_exc")
            return f"[TOOL_CANCELLED] 任务已停止，工具 {tool_name} 的结果未采用。"
        return _classify_error(e, tool)


class PartitionedToolNode:
    """Partitioned concurrency tool node — LangGraph compatible"""

    def __init__(self, tools: list[BaseTool]):
        self.tools_by_name: dict[str, BaseTool] = {t.name: t for t in tools}

    async def _execute_group(self, group: list[dict]) -> list[ToolMessage]:
        results = []
        for tc in group:
            tool = self.tools_by_name.get(tc.get("name", ""))
            if tool is None:
                logger.warning(f"[PartitionedNode] 未知工具: {tc.get('name')}")
                results.append(ToolMessage(
                    content=f"[ERR_UNKNOWN_TOOL] 工具 '{tc.get('name')}' 不存在。可用工具: {list(self.tools_by_name.keys())}",
                    tool_call_id=tc.get("id", "unknown"),
                    name=tc.get("name", "unknown"),
                    artifact={},
                ))
                continue
            t_tool = time.perf_counter()
            msg = await _run_single_tool(tool, tc)
            dt_tool = (time.perf_counter() - t_tool) * 1000
            logger.info(f"[PartitionedNode] tool {tool.name} done ({dt_tool:.0f}ms)")
            # v8.4.6 B8: 结构化结果 envelope——熔断/统计只读 _meta 字段，
            # 不解析自由文本（书 §1.2.2"验证：输入隔离"）
            try:
                _content = str(getattr(msg, "content", "") or "")
                _meta = {"tool": tool.name, "status": "ok", "code": "OK"}
                if _content.startswith("[DEDUP]"):
                    _meta.update(status="skipped", code="DEDUP")
                elif _content.startswith("[ERR") or _content.startswith("[Error"):
                    _m = re.match(r"\[([A-Z_]+)\]", _content)
                    _meta.update(status="error",
                                 code=_m.group(1) if _m else "ERR_UNKNOWN")
                _art = getattr(msg, "artifact", None) or {}
                if isinstance(_art, dict):
                    _art = dict(_art)
                else:
                    _art = {}
                _art["_meta"] = _meta
                msg.artifact = _art
            except Exception:
                pass
            # v8.4.1: 业务日志——工具级事件（成功/失败/结果长度，排查检索与工具问题）
            try:
                from src.core.business_logger import blog
                content = str(getattr(msg, "content", ""))
                blog("tool_done", name=tool.name,
                     ms=int(dt_tool),
                     err=content.startswith("[ERR") or content.startswith("[Error"),
                     chars=len(content))
            except Exception:
                pass
            results.append(msg)
        return results

    async def _execute(self, tool_calls: list[dict]) -> list[ToolMessage]:
        if not tool_calls:
            return []
        groups: dict[str, list[dict]] = {}
        for tc in tool_calls:
            name = tc.get("name", "")
            key = concurrency_key_for(name) or name
            groups.setdefault(key, []).append(tc)
        logger.info(f"[PartitionedNode] {len(tool_calls)} 个工具调用, {len(groups)} 个并发组: {list(groups.keys())}")
        t_groups = time.perf_counter()
        tasks = [self._execute_group(group) for group in groups.values()]
        group_results = await asyncio.gather(*tasks)
        dt_groups = (time.perf_counter() - t_groups) * 1000
        all_results = []
        for grp in group_results:
            all_results.extend(grp)
        logger.info(f"[PartitionedNode] all {len(tool_calls)} tools done ({dt_groups:.0f}ms)")
        return all_results

    async def execute_tools(self, tool_calls: list[dict]) -> list[ToolMessage]:
        return await self._execute(tool_calls)

    async def ainvoke(self, inputs: dict) -> dict:
        messages = inputs.get("messages", [])
        if not messages:
            return {"messages": []}
        last_msg = messages[-1]
        tool_calls = getattr(last_msg, "tool_calls", None)
        if not tool_calls:
            return {"messages": []}
        results = await self._execute(tool_calls)
        return {"messages": results}

    def __call__(self, inputs: dict) -> dict:
        import asyncio
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
        return loop.run_until_complete(self.ainvoke(inputs))
