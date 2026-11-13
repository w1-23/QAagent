# 通用智能体生态收敛：源码交付说明（2026-11-13）

范围：`01_src` 源码、固定系统提示词、前端原生 JS、离线回归。
未运行 Case A/B/C、FW20/40 或 240 题路由基准；未修改 gold、评分器、
外部 harness（`E:/codex_WORKSPACES/QAfinalexperiment/`）与历史结果；
未重启、停止或请求正在运行的服务实例。

## 一、目标

把此前针对审核任务暴露的修复，收敛为可泛化的智能体工程主线：

- 用户意图 / 环境能力分开表达；
- 工具可见性与执行授权分开表达；
- 正常、DSML 恢复与交付门强制调用共用同一个执行账本；
- 终态由服务端唯一推导，并原样传到前端、job 与历史；
- 断线、刷新后按同一身份恢复，不用"最新一条消息"猜归属。

## 二、四份类型化契约

| 契约 | 文件 | 作用 |
| --- | --- | --- |
| `RequestPlan` | `src/graph/request_plan.py` | 请求事实、用户约束、环境能力、可见工具、必需工具组、执行授权、路由建议（advisory/required 分离） |
| `DeliverableSpec` | `src/graph/export_contract.py` | 产物身份、数量、正文归属（shared/distinct）、重述合并、歧义；二分匹配与不可变产物校验 |
| `ExecutionLedger` | `src/core/execution_ledger.py` | 每个 `tool_call_id` 一个 start、一个 terminal；来源 provider/dsml/forced；强制调用生成配对 AI+Tool 历史消息 |
| `TurnEnvelope` | `src/core/turn_envelope.py` | 执行/接受/显示/job 状态、history_saved、notices、已验证产物 ID、terminal_seq |

权威状态路径：
`POST → job 事件（身份）→ 有序 SSE（seq/id）→ done(TurnEnvelope) → job 终态快照 → /messages.turns → 前端 reducer`

## 三、本轮补齐的关键接线

### 1. 服务端终态（API）

- 正常、空回答、用户取消、内部异常四条 `done`/`error` 分支都构造同一份
  `TurnEnvelope`；`done` 同时下发 `turn_status`、`turn_envelope` 与
  `artifact_ids`。
- `TurnEnvelope` 新增 `artifact_ids`：只包含 `valid=true` 的产物校验记录，
  去重保序；产物归属不再依赖"账号文件库最近新增"。
- 取消分支的停止说明（`stop_notice`）与空回答的诚实提示随终态下发。
- 异常分支的 `error` 事件同样携带 failed 信封。
- `job` 首帧增加 `protocol: citrus-stream/1`，与 `seq/id`、`terminal_seq` 一起
  构成有版本号的流协议。
- job 表新增 `terminal_snapshot`，`/jobs/{job_id}` 与
  `/sessions/{sid}/jobs` 返回可恢复的最小终态。

### 2. 执行账本

- `_execute_calls` 注册 start；`_emit_completed_tool_step` 注册唯一 terminal；
  DSML 恢复调用标记来源；交付门强制调用在真正执行前注册 start（来源
  `forced`），并在成功后生成配对 `AIMessage.tool_calls` + `ToolMessage`。
- 强制调用配对插入最终回答之前，`_validate_trace` 刷新后仍保留该步骤。
- 重复终态只计数，不覆盖第一个终态；请求结束仍 running 的调用由 `close()`
  统一记为失败/取消，不伪造成功。

### 3. 历史一致性（本轮发现并修复的真实泛化缺陷）

真实会话库里 2565 条 human 行中有 626 条是主循环自己写入的"人类口吻"提示
（预算延长、反思、工具补齐、截断重试、回合提醒）。旧实现只靠一份固定短语表
识别，实测已漏掉 `预算已延长…`、`[反思] …`、`还剩 N 个回合…` 等措辞，导致：

- 这些行被当成新提问，重置轮次键为空；
- 历史分组出现没有问题的"幽灵轮次"；
- 右侧导航条长出假刻度；
- 真实会话的分页测试在数据增长后开始失败。

修复采用结构规则而非继续扩词表：**真实提问一定携带请求身份
（client_request_id / idempotency_key），主循环 nudge 一定没有**。
在存在身份映射的会话里，空身份 human 行不再开启轮次、不再生成导航刻度；
完全没有身份的旧会话仍沿用文本规则，不丢历史。该规则同时用于
`/messages` 历史与 `/turns` 导航索引。

### 4. 前端终态与历史

- `done` / `error` 统一调用纯 reducer `applyTurnEnvelope(data)`：
  只读服务端 `turn_envelope.display_status`（兼容旧 `turn_status`），
  四态之外一律按 failed；`partial` 不再显示"已完成"，吉祥物只对
  `completed` 播放成功动画。
- `finishStatus()` 的终态标签只读服务端状态；本地 `_toolStates` 只提供计数。
  无工具的普通完成轮不再显示空的执行轨迹框；失败/部分/取消轮即使没有工具行
  也保留终态提示。
- 断线与用户取消分离：断线显示"状态未确认 / 后台可能仍在运行"，保留停止按钮
  并按 `job_id` 查询后台状态；用户随后取消时清除断线态，显示"已停止"。
- 停止回填按身份匹配（本轮 `clientRequestId` 或服务端 `stop:<job_id>`），
  删除"取最新一条 assistant"的猜测。
- SSE 解析器维护 `lastSeq`/`lastEventId`，重复或回放帧跳过；JSON 解析失败与
  handler 异常写入 `console.error`，不再静默吞掉终态事件。
- 历史优先消费 `/messages.turns`（`normalizeHistoryTurn` 归一化），
  `groupHistoryTurns` 降级为旧会话 adapter；分页按 message_id 合并，
  边界轮次按 turn key 合并，不重复渲染问题气泡。
- 本轮生成文件按 `done.artifact_ids`（匹配 `artifact_id`/`file_id`）归属；
  仅旧服务端无该字段时才退回"与基线做差"。
- 上下文面板：服务端估算与 provider 单次用量分开存放、合并渲染，
  `context_usage` 不再覆盖估算值；`context_degraded` 展示真实原因。
- `index.html` 脚本版本号 `app.js?v=36`。

### 5. 上下文预算

- `build_context_budget()` 成为唯一配置构造入口，完整读取
  `CONTEXT_BUDGET_ENABLED`、`TARGET_RATIO`、`PROTECT_RECENT_TURNS`；
  `target_ratio` 真正驱动压缩目标，最近轮次保护可配置。
- 会话节点不再复制三项阈值。

## 四、离线验收

运行（仓库根目录，不触碰运行中服务）：

```
python -X utf8 engineering/word_delivery_20261112/run_checks.py
```

结果（三组独立进程，2026-11-13）：

| 组 | 结果 |
| --- | --- |
| main | 541 passed / 0 failed / 0 errors / 0 skipped |
| word_download | 1 passed |
| attachment_boundaries | 14 passed |

- 基线为 422 passed；本轮新增 119 项断言，全部通过，无新增 skip。
- `git diff --check` 通过。
- 新增/更新的契约测试包括：
  `core/test_execution_ledger.py`、`core/test_context_budget.py`、
  `core/test_turn_envelope.py`、`core/test_cancel_lifecycle.py`、
  `api/test_terminal_state_frontend.py`、`api/test_stop_turn_contract.py`、
  `api/test_trace_turn_merge.py`、`api/test_history_pagination.py`、
  `session/test_turn_index.py`、`graph/test_reliability_contracts.py`。
- 新增源码文件：`core/execution_ledger.py`、`core/turn_envelope.py`、
  `core/tool_catalog.py`、`core/ooxml_determinism.py`、
  `graph/request_plan.py`、`tools/argument_check.py`。

注意：仓库 `.gitignore` 忽略 `**/test_*.py`；新增测试文件在提交时需
`git add -f` 显式纳入，否则不会进入版本库。

## 五、诚实边界

- 离线测试只证明工程契约：同一事件序列得到同一终态、每个调用一个终态、
  产物必须通过登记/所有权/字节校验、历史与导航不再产生幽灵轮次。
  它不能证明真实模型更聪明、科学结论更正确或历史跑分提升。
- 正在运行的服务实例仍是旧内存代码；以上改动需要之后由用户明确授权的
  受控重启才会生效。本次未重启、未停止、未向运行实例发请求。
- 未运行 Case A/B/C、FW20/40、240 题路由基准；未修改 gold、评分器、
  外部 harness 与历史结果。
- provider 超时、证据不足、真实截断与用户取消仍如实显示为未完成；
  断线恢复只恢复服务端确有的状态，不重放不存在的答案或产物。
- 交付门对 `body_scope=distinct` 且无独立正文的产物仍记
  `missing_product_content`，不会把完整内部报告复制成"脱敏简报"。

## 六、后续建议（需用户授权）

1. 选择一个无进行中任务的窗口，受控重启服务，使新代码进入内存。
2. 重启后做一次最小冒烟：普通问答、工具轮、取消、断线刷新、
   多产物导出、历史恢复。
3. 再按需运行真实端到端实验；离线结果不能替代真实实验结论。
