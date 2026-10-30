# 当前 Agent 执行流程与关键调用路径

审计时间：2026-10-30 · 代码基线：本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent` HEAD `80bab4b`（Phase 2A 已冻结）
审计性质：只读。本轮未修改任何生产代码、数据库结构、前端资源或服务器配置；未部署、未重启生产、未 push。

标注口径：
- **[已证实]** 有代码路径 + 日志或实跑证据
- **[潜在风险]** 静态分析发现，未复现
- **[已验证正常]** 有测试或实跑证据
- **[尚未验证]** 证据不足，不得视为通过

---

## 1. 请求入口

| 步骤 | 位置 | 说明 |
|---|---|---|
| 路由 | `01_源码_src/src/api/main.py:247-269` | 中间件：带 `citrus_auth` cookie 的请求必须带同源 `Origin`，否则 403；`/api/v2/admin/*`、`/api/v2/config` 非 GET 需管理员 |
| 请求体 | `main.py:302` | `client_request_id: Optional[str] = Field(default=None, max_length=64)` |
| 请求 ID 补齐 | `main.py:967-968` | 缺省时 `uuid4().hex` 自动生成，随后 `set_client_request_id()`（`main.py:970-974`）写入 contextvar |
| 幂等键 | `main.py:977` | `compute_idempotency_key(sid, query, client_request_id)` |
| 附件登记 | `main.py:854-889` | `client_request_id` 透传到 `register_file(..., client_request_id=...)` |

## 2. Agent 主循环（`01_源码_src/src/graph/general_agent_graph.py`）

| 阶段 | 位置 | 证据 |
|---|---|---|
| 请求级准备 | `:1010-1030` | `deadline`、`_source_scope(query)`、`resolve_tool_policy(query, scope, paper_only=...)`、`retrieval_disabled`、`figures.begin_request()`、`get_request_tables()` |
| 工具绑定 | `:1056-1069` | `tools, _ = _scoped_toolset(scope, query=..., has_table=..., has_paper_attachment=..., has_account_files=..., visualization_enabled=...)`；随后 `tools_by_name` |
| 模型调用 | `:932 _call_llm(llm, messages, *, timeout)` | **非流式**；循环内未出现 `stream=True` / `astream` |
| 无工具分支 | `:1307-1328` | 缺必需工具组时先补齐（`:1308-1319`，上限 `_MAX_SOURCE_ENFORCEMENT_ATTEMPTS=2`），否则 `emit_text(answer)` 并 `break` |
| 工具执行 | `:1333-1336` | `_execute_calls(response, tools_by_name=..., deadline=..., seen_call_signatures=..., tool_status_sink=tool_status_records)` |
| 证据累积 | `:1339-1353` | `_evidence_fingerprints(results)` 去重；`stagnant_batches` 连续无新证据则停 |
| 停止条件 | `:48-51` | `_NO_PROGRESS_BATCHES_TO_STOP = 2`、`_MAX_TOOL_CALLS_PER_BATCH = 8`、`_MAX_SOURCE_ENFORCEMENT_ATTEMPTS = 2` |
| 最终综合 | `:1361-1392` | 预留一次无工具调用，`remaining = deadline - now - 1.0`，失败时 `_provider_failure_notice` |

## 3. 工具执行器（同文件）

- `:865-867` 签名：`async def _execute_calls(response, *, tools_by_name, deadline, seen_call_signatures, tool_status_sink=None)`
- `:871-879` 批上限：超出 `_MAX_TOOL_CALLS_PER_BATCH` 的调用返回 `[TOOL_BATCH_LIMIT]`，**不发布 start 事件**，只由终态事件报 `skipped`
- `:880-889` 去重：`signature = sha256(json.dumps([name, args], sort_keys=True))`；命中则返回 `[TOOL_DUPLICATE_SKIPPED]`，不执行
- `:894-898` 发布 start：`emit_tool_call_start(...)` + `emit_tool_executing(...)`，**整段包在 `try/except Exception: pass`** —— 事件层异常不会中断工具执行

## 4. 工具结果的三种形态（**已证实**，检查过字段而非凭印象）

| 形态 | 产生位置 | 内容 |
|---|---|---|
| 工具原始返回 | 各工具实现（`src/research/tools.py` 等） | 全文/结构化结果 |
| 模型可见结果 | `ToolMessage.content` | 工具返回文本，超长时按预算截断 |
| 用户可见摘要 | `Outcome`（`src/tools/outcome.py:95-134`）+ `emit_tool_result(summary=...)` | `status/reason_code/summary`，由工具文本前缀判定 |

`outcome.py` 的判定顺序（原文）：`:109` 前缀命中后按关键词升级 `reason_code`——`401/403/unauthorized/认证` → `auth`（`:117-118`）、`timeout` → `timeout`、`network/connect` → `network`、`未配置` → `configuration_missing`（`:112-114`）、`quota/credit/额度/余额` → `quota`。**注意：该 `reason_code` 只进展示与统计，不回灌模型提示，也没有“该类失败不可重试”的表达。**

## 5. 结论（本文件）

- 执行路径本身是清晰的单一主循环，工具绑定已改为环境函数（Phase 2A，`80bab4b`）。
- 展示层（`emit_*`）与执行层已在**调用点**隔离（`try/except`），但生命周期仍耦合（见 `CURRENT_STREAMING_AND_UI.md` §4）。
- `reason_code` 目前是“展示标签”，不是“控制信号”，这是重复 `auth` 调用的机制性原因（见 `SYSTEM_STABILITY_AUDIT.md` §2）。
