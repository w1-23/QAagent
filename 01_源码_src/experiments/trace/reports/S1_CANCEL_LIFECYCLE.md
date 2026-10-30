# S1 交付：停止（取消）作为一轮可靠终态

> 阶段：S1（取消生命周期）。**只动取消链路**，未改检索、未改工具绑定、未改前端其它交互。
> 生产基线仍是 `ff58b1f`；本阶段改动只在本地仓库，未部署、未 push。

---

## 1. 修改目的

S0 基线核对发现的四个取消缺口（见 `AUDIT_REPORT_20261030.md` 第一编 C 节）：

| 缺口 | 用户可见后果 |
|---|---|
| 只有"删掉整条回复"的本地动作 | 点停止 → 回复消失、历史没记录、不知道停在哪一步 |
| 取消只发生在 LLM await 边界 | 长工具（检索/联网/绘图）跑完仍然被采用，可能已写文件 |
| 断线 = 直接终止 | 刷新或切网就丢任务，且被记成 `cancelled`（其实是可恢复的） |
| 历史里没有取消终态 | 刷新后那一轮要么消失了，要么被画成"失败" |

S1 的目标是把"停止"做成**一轮的可靠终态**：立刻响应、可恢复、状态真实、
历史可回放，且"停止后仍在跑的工具"不会把结果或成果留下来。

---

## 2. 实际修改的文件与行号

| 文件 | 行号 | 变更 |
|---|---|---|
| `01_源码_src/src/core/cancel.py` | 新增（1–127） | `CancelToken`（`threading.Event` 跨线程可见）、`ToolCancelled`、`current_token/set/reset`、`raise_if_cancelled`、`bound_token` |
| `01_源码_src/src/tools/registry.py` | 284–304 | `_cancelled_result()` 统一取消结果（`[TOOL_CANCELLED]` + `artifact.cancelled=True`）、`_note_late_result()` 诊断 |
| 同上 | 307–312 | `_is_cancel_exc()` 识别 `ToolCancelled`（不当失败） |
| 同上 | 319–324 | **闸门 0**：已取消的请求不再派发新工具 |
| 同上 | 358–360 / 364–367 | **闸门 1**：工具跑完但已取消 → 结果与 artifact 丢弃（`content_and_artifact` 分支） |
| 同上 | 382–384 / 389–392 | 同上（普通工具分支） |
| `01_源码_src/src/core/jobs.py` | 151 `request_cancel` / 172 `mark_detached` / 192 `mark_stopped` | 任务状态机：`running → cancel_requested → cancelled`，`detached` 与 `stop_reason` 分离 |
| `01_源码_src/src/api/main.py` | 301–344 `_cancel_one_job` | 幂等停止：终态不改、重复点击不重复清理、非本进程 task 只停在 `cancel_requested`（**不伪称已终止**） |
| 同上 | 347–397 `_stop_marker_messages` | 把停止写成可回放的一轮：问题 + 步骤行（`ToolMessage` + `_citrus_execution`）+ 停止说明 |
| 同上 | 398–437 `_schedule_detach_grace` | 断线宽限期：先 `detached`，到期才停，`stop_reason=detached_timeout` |
| 同上 | 1287 起 | job→`asyncio.Task` 注册、`_job_tokens[job_id] = CancelToken()` 绑定请求级令牌 |
| 同上 | 1442–1520 | 取消分支：区分 `user` / `internal`，落库停止轮、发 `done.turn_status=cancelled`、审计 `finish_request(status="cancelled")` |
| `01_源码_src/src/core/turn_status.py` | 31 `derive_turn_status` | 新增 `stopped` 参数：停止轮 = `cancelled`；**有真实失败时至多 `partial`**（不把失败洗白成取消） |
| `01_源码_src/src/tools/outcome.py` | 93–96 | `[TOOL_CANCELLED]` → `status=cancelled`、`reason_code=stop_requested`（不计失败） |
| `01_源码_src/web/assets/app.js` | 1909 `stopCurrentTurn` | 停止不再删气泡：保留本轮，标"已停止"，显示真实步骤状态 |
| 同上 | 2522–2529 | 停止按钮 → `POST /api/v2/chat/jobs/{id}/cancel`，失败时如实提示而不是假装停了 |
| `01_源码_src/src/core/test_cancel_lifecycle.py` | 新增（16 例） | 令牌/状态机/闸门/断线/停止轮落库/工具层丢弃 |
| `01_源码_src/experiments/trace/probes/probe_cancel_e2e.py` | 新增 | 纯外部观察者（只走 HTTP）验收停止行为 |

---

## 3. 变更前后的行为

### 3.1 点停止

| | 变更前 | 变更后 |
|---|---|---|
| 回复气泡 | 整条被删掉 | 保留，标注"已停止" |
| 正在跑的工具 | 无法感知，跑完照样采用 | 闸门 0 不再派发；闸门 1 丢弃结果与 artifact |
| 历史 | 没有记录 | 问题 + 步骤行（真实状态/耗时）+ 停止说明 |
| 服务端状态 | 一律 `cancelled` | `cancel_requested` → `cancelled`，`stop_reason=user` |

### 3.2 断线 / 刷新

| | 变更前 | 变更后 |
|---|---|---|
| 服务端任务 | 直接终止 | 先 `detached`，宽限期（默认 180s）内继续跑 |
| 宽限期内重连 | 任务已丢 | 继续读同一个 `job`，不重复执行 |
| 到期仍未结束 | — | `stop_reason=detached_timeout`（**绝不写 user**） |

### 3.3 状态语义

`completed / partial / failed / cancelled` 四态由 `derive_turn_status()` 统一推导，
不再用"回答开头是不是'本轮未完成'"这种字符串启发式：

* 用户停止且无失败 → `cancelled`
* 用户停止但前面有工具失败 → `partial`（不掩盖失败）
* 有工具失败未完成 → `failed`
* 全部完成 → `completed`

---

## 4. 测试数量与通过情况

| 套件 | 结果 |
|---|---|
| `src/core/test_cancel_lifecycle.py` | **16 passed** |
| 全量 `pytest src` | **195 passed / 0 failed**（改动前基线 179，本阶段 +16） |

其中 4 例专门覆盖"展示真实"：

1. `test_result_of_tool_that_finished_after_stop_is_discarded`：慢工具在停止后跑完 →
   内容不含工具输出、以 `[TOOL_CANCELLED]` 开头、`artifact.cancelled is True`。
   （构造：`slow_tool` 睡 0.6s，0.2s 时点停止；必须先 `register_tool`，否则会先被
   HITL 白名单闸门以 `[ERR_HITL_REJECT]` 拒绝，测不到取消闸门。）
2. `test_tool_dispatched_before_stop_reports_cancelled_in_outcome`：取消结果被分类为
   `cancelled/stop_requested`，不会画成红色失败。
3. `test_detach_grace_stops_with_detached_timeout`：宽限期内不杀、到期才杀，理由写
   `detached_timeout`。
4. `test_stop_marker_persists_steps`：停止轮落库为 问题 + 步骤行 + 说明。

---

## 5. 真实测试的输入与结果（本机 8010 实例，真实 HTTP）

探针：`experiments/trace/probes/probe_cancel_e2e.py`（只调公开 API，不 import 内部）。

**输入**：`检索黄龙病的传播途径与防控要点，分条列出`，跑 ~4 秒后对
`POST /api/v2/chat/jobs/3dc7066ebea6/cancel` 发停止。

**结果**（`experiments/trace/runs/20261030/probe_cancel_e2e.json`）：

```json
cancel_http: {"status":"ok","accepted":true,"target":"in_process","stop_reason":"user"}
done_turn_status: "cancelled"
job_record: {"status":"cancelled","stop_reason":"user","finished_at":"..."}
history_tool_rows: [
  {"name":"本地检索","status":"failed","reason_code":"tool_error",
   "summary":"没有找到候选资料","elapsed_ms":5},
  {"name":"网页搜索","status":"failed","reason_code":"auth",
   "summary":"没有找到网页资料","elapsed_ms":253}
]
history_has_answer: true
checks: job_cancelled=true, stop_reason_is_user=true, turn_status_cancelled=true,
        no_fake_completed_row=true, no_half_baked_answer=true
```

落库实查（`agent/state/sessions.db`）：

```
12387 human  检索黄龙病的传播途径与防控要点，分条列出
12388 tool   local_hybrid_search  failed  execution_json={"status":"failed","reason_code":"stop_requested",...}
12389 tool   deepseek_search      failed  execution_json={...}
12390 ai     已按你的停止请求中止本轮……停止前已执行的步骤：…
```

**第一次尝试失败并已修复**：最初只存了 问题 + 说明两行，`history_tool_rows` 为空。
根因是 `SessionManager` 会丢弃"没有对应 `tool_call` 的 ToolMessage"（孤立消息）。
修法：步骤行挂到一条 `AIMessage.tool_calls` 之下（`main.py:347 _stop_marker_messages`）。

**一个诚实的说明**：本机实例的本地语料被判 `quarantined`、联网无 key，所以两个工具
都是**秒级失败**，这一轮没有"正在执行中被取消"的步骤，`has_cancelled_row` 为 false。
该字段只有在"确有工具在跑时点停止"才会为 true，需在语料/联网可用的实例（生产或
隔离实例）复测。

---

## 6. 尚存风险

1. **同步工具不可硬中断**：线程池里的工具没有检查点就无法即时退出，S1 只能保证
   结果与 artifact 不被采用。工具内部的写动作靠"写产物前"检查点（目前 `research/tools.py`
   有 3 处）兜底；新工具必须遵守"不要在子线程写产物"的约定。
2. **跨进程取消**：多 worker 时 `target=not_in_this_process`，只停在 `cancel_requested`，
   不伪称已终止。生产当前 `--workers 1`，不受影响；扩 worker 前需要共享信号（Redis 等）。
3. **停止轮不重建工具行的时间轴一致性**：步骤行按 journal 顺序写入，`completed_at_ms`
   用 `elapsed_ms` 近似（只有相对时长，没有绝对锚点）。
4. **本地实例语料不可用**（`chunk source/source_type/source_file is required`、11 批次
   向量库降级 BM25）：与 S1 无关，但会影响本地复测；生产语料是新 C2 包。

---

## 7. Git

* 提交：本文件与代码同一提交（见 `git log -1 --stat`）。
* 只提交本地仓库，**未 push、未部署**。
* 变更集：`src/core/cancel.py`、`src/core/turn_status.py`、`src/core/jobs.py`、
  `src/tools/registry.py`、`src/tools/outcome.py`、`src/api/main.py`、
  `web/assets/app.js`、`src/core/test_cancel_lifecycle.py`、
  `experiments/trace/probes/probe_cancel_e2e.py` 与本文件。

---

## 8. 部署建议

1. 先在生产同构的**隔离实例**复测（需语料 + 联网 key），确认停在一个真正执行中的工具上
   时出现 `cancelled` 行。
2. 部署步骤（服务器）：
   ```bash
   cd /home/WZQ/CitrusQA
   cp -a 01_源码_src/src /home/WZQ/CitrusQA/backups/src_$(date +%Y%m%d_%H%M%S)
   # 同步 S1 改动后：
   kill -TERM $(ps -eo pid,cmd | grep "uvicorn src.api.main" | grep -v grep | awk '{print $1}')
   setsid nohup bash 04_脚本_scripts/run_server.sh > agent/logs/server_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null &
   ```
3. 部署后自检：`GET /` = 200；一轮问答中点停止 → job 记录 `cancelled/stop_reason=user`；
   刷新后该轮仍在且显示"已停止 + 步骤"。

---

## 9. 回滚方法

* 代码回滚：`git checkout ff58b1f -- 01_源码_src/src 01_源码_src/web`（或 revert 该提交），
  重启服务。
* 数据兼容：`messages.execution_json` 是**新增列**，旧代码忽略它即可，无需回滚数据库。
* 语义回滚风险：回滚后"停止"恢复为"删除气泡 + 无历史"，`cancel.py` / `cancel` 端点
  变为死代码，不影响既有轮次。


---

## 10. 补丁（Round 6）：断线曾被记成"内部取消"

S1 交付时，"断线 ≠ 取消"只做了 jobs 层单测，没有真实断线实验，结果漏了一个真实缺陷：
真实 `http.disconnect` 会让任务落到 `cancelled / stop_reason=internal`。

- 根因：sse-starlette 3.4.5 断线时取消的是驱动任务，生成器被 `aclose()` 抛
  `GeneratorExit`，原 `except asyncio.CancelledError` 不执行，`finally` 里
  `graph_task.cancel()` 把任务杀掉。
- 修复：改用 `client_close_handler_callable` 在取消前标 `detached`；`except` 同时覆盖
  `GeneratorExit`；`finally` 只认 `conn_state["keep_alive"]`。
- 复测：`experiments/trace/probes/probe_disconnect_e2e.py` 8/8 通过
  （`detached` → 执行继续 → 终态非 cancelled/user）。
- 测试：`test_sse_disconnect_is_wired_to_the_client_close_hook`；全量 267 passed。

详见 `AUDIT_REPORT_20261030.md` §Q。
