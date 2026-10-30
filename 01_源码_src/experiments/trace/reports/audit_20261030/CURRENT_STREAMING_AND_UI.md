# 当前模型输出、SSE、前端与历史恢复链路

基线：HEAD `80bab4b`；证据均取自当前工作区文件与本次实跑（`experiments/trace/runs/20261030/`）。

---

## 1. 模型调用形态（**关键结论：非流式**）

- `01_源码_src/src/graph/general_agent_graph.py:932`：`async def _call_llm(llm, messages, *, timeout: float)`
- 主循环内无 `stream=True` / `astream` / `astream_events`（全文件 grep 无命中）。
- ⇒ 一轮模型输出是**一次性返回**：`AIMessage(content, tool_calls=...)`。没有任何“边生成边推送”的通道。

## 2. 公开文本是否存在（实跑证据，而非推断）

查本地实例真实落库的 transcript（`agent/state/sessions.db`，`messages` 表，列含 `msg_type/content/tool_calls_json/reasoning/execution_json`）：

```
===== R1 61858eb3… rows=5
  human  len= 1172 tc=0 rlen=0
  ai     len=    0 tc=1 rlen=0   ← 带工具调用，content 为空
  tool   len=  133 tc=0 rlen=0   '[WEB_TOOL_ERROR] HTTP 401: Authentication Fails, Your api key: ****db23 is invalid'
  tool   len=   53 tc=0 rlen=0   '本轮检索均未能返回候选资料…'
  ai     len=  736 tc=0 rlen=0   '说明：本轮联网检索与本地文献库均未能返回结果…'

===== R4 841d8d00… rows=14
  ai  len=0 tc=1   ← 第 1 批工具调用
  ai  len=0 tc=1   ← 第 2 批
  ai  len=0 tc=1   ← 第 3 批
  ai  len=0 tc=1   ← 第 4 批
  ai  len=345 tc=0 ← 最终回答
  ai  len=487 tc=0 ← 未完成说明
```

**结论 [已证实]**：当前配置下，模型在工具调用前后**没有产生任何公开 assistant 文本**（4 批工具调用对应的 `ai` 行 `content` 长度全为 0，`reasoning` 列全为空）。所以 Codex 式“分析”不是“已有文本被丢弃”，而是**根本还没有被产生**。

辅助事实：`01_源码_src/src/core/llm_pool.py:24-61` 装了 `reasoning_content` 透传补丁（把字段塞进 `additional_kwargs`），但本次落库 `reasoning` 列全空 ⇒ 该字段在本 provider/本配置下没拿到；且 `progress_bus.py:210` 明确“never forward model-generated reasoning text”。前端 `app.js:2186` 对 `reasoning` 事件直接 `break`。

## 3. SSE 事件产生与转发

| 层 | 位置 | 机制 |
|---|---|---|
| 请求级队列 | `src/core/progress_bus.py:22-50` | `_current_queue: ContextVar[asyncio.Queue]`；`set_request_queue()` / `clear_request_queue()`；`get_progress_queue()` 回退全局队列 |
| 发射 | `progress_bus.py:106-112` | `emit_progress()`：`q.put_nowait()`，`except asyncio.QueueFull: pass` ⇒ **丢事件不抛异常** |
| 桥接 | `src/api/main.py:1077-1115` | `bridge_progress()` 阻塞 `get()` 立即转发到 `event_queue`；`tool_heartbeat()` 每 2s 对运行中工具补发 `tool_executing`（`:1093-1112`） |
| 编码 | `main.py` `_encode_event()` | `event: <type>` + `data: <json>` 两行；payload 平铺（不在 `data` 键下） |

事件类型（实跑采集，R1 一次请求）：
`context_status, context_usage, done, heartbeat, status, text, tool_call_start, tool_executing, tool_progress, tool_result`

## 4. 生命周期耦合（**本节是架构风险的核心**）

- `main.py:1456-1467`：请求任务被取消（含客户端断开）时，`jobs_mod.update_job(job_id, status="cancelled", error="client_disconnected_while_queued")` 并 `raise`。
- `main.py:293` 注释表明设计取向：不强行掐断，而是向任务发取消信号，在 LLM/tool 的 await 处抛 `CancelledError`。
- ⇒ **执行生命周期 = SSE 消费端生命周期**。浏览器刷新、断网、代理超时都会取消整轮科研任务，落库状态为 `cancelled`。

[潜在风险] 对“一次几十分钟的实验设计/数据审查”这类任务，这是可测量的损失面；且用户主观感受是“我只是刷新了一下页面”。展示层与执行层在**异常安全**上已隔离（emit 包 try/except、队列满即丢），但在**生命周期**上没有隔离。

## 5. 前端渲染

- `web/assets/app.js:1941` `var _toolStates = Object.create(null);` —— 以 `tool_call_id` 为键记真实终态
- `:2189-2194` `tool_call_start` → `appendTerminal(data)`（**创建行**），`_toolCount++`
- `:2195-2198` `tool_executing` → `appendTerminal`（**更新同一行**，靠 `tool_call_id` 合并）
- `:2202-2209` `tool_result` → 写 `_toolStates[callId]=status`（`:2014`）、更新行；失败/部分时把状态行文案改为“有一步未能完成，正在整理已有资料…”
- `:2211-2220` `text` → 追加最终答案，首次 token 记录 `_firstTokenElapsedMs`
- `:2076-2100` `finishStatus()` → 按真实行状态汇总：`failed>0 → 有步骤未完成`、`degraded>0 → 部分完成`、否则 `已完成`；计数 `N 项 · 跳过 K · 失败 M`；`has-failure` 加红
- `:2186` `case 'reasoning': break;` —— 私有推理永不展示（符合产品定义）

**现状：没有“分析”这一类行。** 时间线只有工具行与最终答案两种实体，中间不存在“公开工作说明”的渲染位。

## 6. 历史恢复

- 落库：`src/session/manager.py:429-443` 序列化 `content/tool_calls_json/execution_json`；`:497-499` 反序列化 `execution_json`；`:610/667` 写入
- 恢复接口：`GET /api/v2/session/{sid}/messages`（`main.py:2518` 附近组装历史行）
- 前端：`app.js:2733 async function restoreSessionHistory()`，按 `execution_json` 的 `status/summary/elapsed_ms/reason_code` 重建工具行；此前“刷新后全红/全部已完成”的两个 bug 已在 `828ceee` 修复
- [已验证正常] 本次 R1/R4 的 `/messages` 回放与实时事件一致（同一 `tool_call_id`，同一 `status`，同一 `elapsed_ms`）

## 7. 若要支持动态“分析”，最小字段缺口

1. SSE 需要一个**公开**事件类型（建议 `public_note`，不要复用 `reasoning`/`thinking`）：`{turn_id, seq, text, source, tool_call_id?}`
2. `messages` 需要落库位（现有 `reasoning` 列语义是私有推理，**不应复用**）
3. 前端需要第 3 种行实体（与工具行同容器、不同图标/语气），并在 `restoreSessionHistory` 里同步重建
4. 生成侧需要一个**不依赖模型自由发挥**的来源（见 `PUBLIC_PROGRESS_FEASIBILITY.md`）
