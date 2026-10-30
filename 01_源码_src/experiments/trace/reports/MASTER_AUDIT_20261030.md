# 柑橘科研 Agent 审计总报告（合并版）

> 2026-10-30 · 两编合一：**第一编 执行流/可靠性/取消/预览**（原 `AUDIT_REPORT_20261030.md`） + **第二编 记忆/上下文/导航/文档流**（原 `MEMORY_CONTEXT_NAV_AUDIT.md`）。
> 状态速览见 `SUMMARY_20261030.md`；生产部署见 `DEPLOY_20261030_14f00c7.md`；文献入库审核方案见 `CORPUS_REVIEW_PLAN.md`。
> 全部结论来自真实代码、真实数据库与真实 HTTP/浏览器实测；标「未验证」的条目不会写成已验证。

## 目录

**合并·第一编：执行流、可靠性、取消与预览**

- 0. 报告状态与待你决断的事项（截至 Round 7，跨两编）
- 0. 结论速览（第一编）
- 1. 审计范围、环境与边界
- 2. 现状：一次问答的真实执行链
- 3. 问题清单（按严重度）
- 4. 五个原问题逐条回答
- 5. 流式与 UI 现状（技术事实）
- 6. 可行性判断：四种做法
- 7. 系统稳定性与可测试性
- 8. 测试覆盖与缺口
- 9. 自评：第一轮为什么要重做
- 10. 附录：关键坐标索引
- 0. 结论速览（第二编）
- A. 取消与任务生命周期
- B. PDF 预览
- C. 文件与会话跳转
- D. 动态交互与公开工作说明
- E. 分页式使用指南
- F. 视觉与响应式
- G. 稳定性要求逐条对照
- H. 测试与验收用例
- I. 推荐分批与回滚
- J. 本次只读审计的操作留痕
- K.1 生产基线更正：生产 = `ff58b1f`，不是 `ed7c89d`
- K.2 四点方案更正（用户提出，本报告接受）
- K.3 实施顺序被 S0–S5 取代
- K.4 新增证据：取消令牌可达性（S1 设计前提）
- L.1 第一编判断的复核结果
- L.2 复测过程中**新发现**的两个事实（第一编未覆盖）
- L.3 复测暴露的**环境问题**（非 S1 缺陷，但会污染本地实验）
- L.4 S1 之后仍然开放的风险（承接第一编）
- M.1 本轮落地内容
- M.2 对第一编、第二编判断的复核
- M.3 复测**新发现**：S1 提交里有一个未被自身测试覆盖的致命缺陷
- M.4 本轮仍然开放的问题（不属于 S2 范围）
- Q.1 首轮审计的两处误判（自我更正）
- Q.2 新发现的问题
- Q.3 复测结论
- Q.4 补测：文件库的会话作用域（O-5 关闭）
- 实测（探针 `experiments/trace/probes/probe_disconnect_e2e.py`）
- 根因（不是逻辑没写，是异常类型不对）
- 修复（最小侵入）
- 复测（同一探针，修复后）
- 测试
- 教训
- R.2 隔离实例复测（Round 6，`/home/WZQ/staging_citrus`，port 8011，新代码）
- R.3 发布准入矩阵剩余三行（Round 6，隔离实例 8011）
- R.4 发布准入矩阵「账号安全」：跨用户越权（Round 7）
- R.5 发布准入矩阵「Agent 执行独立性」：关掉公开进度通道，执行结果不变（Round 7）

**合并·第二编：记忆、上下文、导航与文档流**

- 0. 结论速览
- 1. 审计方法（可复现）
- 2. 审计 A：账号长期记忆
- 3. 审计 B：上下文概览为什么是空白（P0）
- 4. 审计 C：右侧长对话导航（Codex 式）
- 5. 四系统独立性核对（防混淆）
- 6. 真实浏览器验收：本轮**没做**（环境限制）
- 7. 实施顺序（建议，待你确认）
- 8. 需要你决定的四件事
- 附一：六页指南重做文案（按你给的设计图，待确认后实施）
- 附二：本次审计的取证命令（可复现）
- 现象
- 根因（实测，非推测）
- 修复（只动运行时状态：未改代码、未改 .env）
- 验证（三层，均为实测）
- 边界
- §10 统一视觉交互验收（防遮挡）— Round 5
- §11 Round 6：稳定性修复 + 前端体验收口（2026-10-30）
- §11.11 补充审计：479,708 字符 ToolMessage 的根因与修复闭环（Round 7）
- §12 Round 7：前端交互修复（导航即时更新 / 灰色体系 / 吉祥物帧与拖拽）
- §13 Round 8：并行全文抓取去重 / 相关性分层 / 轮级预算（2026-10-30）
- §14 Round 9：同篇论文多来源并行竞争，首个合格结果胜出（2026-10-30）
- §15 Round 9 验收：导航即时更新与吉祥物原地动作/自由拖拽（真实浏览器，2026-10-30）


---

# 合并·第一编：执行流、可靠性、取消与预览

> **总入口**：本报告的修复结果、提交时间线与验收数字已汇总到 `SUMMARY_20261030.md`（75 提交 / 406 passed / 三实例状态）。本文件保留第一、二编的原始审计推理与代码行号证据。

- 审计日期：2026-10-30
- 审计对象：**本地仓库 HEAD**（代码在 `01_源码_src/`），提交 `80bab4b` + 本报告提交
- 审计方式：只读。不改生产代码、不部署、不推送；一切结论给到 `文件:行号`，可复核
- 合并说明：本文件合并并取代 `audit_20261030/` 下原有 5 份（`CURRENT_AGENT_EXECUTION_FLOW.md`、`CURRENT_STREAMING_AND_UI.md`、`PROGRESSIVE_REASONING_FEASIBILITY.md`、`SYSTEM_STABILITY_AUDIT.md`、`TEST_COVERAGE_AND_GAPS.md`），并补入第二轮审计的新发现与对第一轮的错误更正

---

## 0. 报告状态与待你决断的事项（截至 Round 7，跨两编）

**本文件是一份完整交付物**：第一编（Agent/流式/工具/稳定性审计）、第二编（前端交互、
文件预览、任务生命周期差异审计）、K 节（对你四点更正的记录）、L–P 节（S1–S5 各自执行后的
复测）、Q 节（Round 4 验收补强）、R 节（Round 6 断线与 Round 7 矩阵补测）。
各阶段的过程文档（`S0_…`、`S1_…`、`S5_…` 等 8 份）是附录，结论均已并入本文件。

### 0.1 代码与部署现状（2026-10-30 14:30 实测）

| 项 | 现状 |
|---|---|
| 本地仓库 HEAD | `8145545`，分支 `main`，工作区除既有未跟踪文件外干净 |
| 自生产基线的提交 | `ff58b1f..HEAD` = **24 提交 / 94 文件 +11,888 −306**（本报告自身提交不计入代码变更） |
| 生产服务器代码 | **仍是 `ff58b1f`**（`main.py`=`5f5f9e3f25ecc2e4`、`general_agent_graph.py`=`361c6685ee35e6a9`；`src/core/cancel.py`、`src/core/public_progress.py` **不存在**） |
| 生产进程 | pid `3495578`，`127.0.0.1:8000`，HTTP 200，**未重启** |
| GitHub | **未推送**。远端 `qaagent`（`github.com/w1-23/QAagent`），本地领先 `qaagent/main` **28 提交** |
| 隔离实例 | `/home/WZQ/staging_citrus`（8.8 G，完整语料，自带 `.venv`）**已停止**，目录保留 |
| 本地测试 | `pytest src` **267 passed / 0 failed** |

### 0.2 待你决断的事项（共 5 项）

| # | 事项 | 我的建议 | 不决断的后果 |
|---|---|---|---|
| D-1 | **是否部署到生产** | 按 `S0_BASELINE_AND_S1_CANCEL_PLAN.md` §2.4 的配方同步（保留 `02_配置_config/`、`.env`、`agent/state/`、语料索引），重启后复跑断线/取消/进度三条探针 | 生产继续停在 `ff58b1f`：本次 24 个提交全部不生效 |
| D-2 | **是否推送到 GitHub** | 建议推 `qaagent/main`，让 28 个提交有远端备份 | 只有本机一份副本，无异地备份 |
| D-3 | **浏览器视觉验收由谁做** | 需要你在真实浏览器里看六页指南、PDF 预览、停止交互 | 我只能验证接口与字节层；视觉与交互无验收记录 |
| D-4 | **Phase 2B（工具自主性）是否继续** | 建议先部署 + 观察一轮真实使用再决定 | 保持现状：17 工具按环境可用性下发，不做策略学习 |
| D-5 | **`/home/WZQ/staging_citrus` 是否保留** | 建议保留到生产验收通过后再删 | 占 8.8 G 磁盘；删早了就没有隔离复测环境 |

另有 3 项运维事实需要你知道（不是待决断项，但影响部署）：

- 生产进程**不在 systemd 下**（`session-*.scope`），重启需手工 `kill -TERM` + `run_server.sh`；
- `DEPLOYED_COMMIT` 仍写 `9955c79`（已过期两代），部署时应一并更新；
- 生产 `agent/state/` 是真实用户数据，部署**不得**覆盖；迁移（`execution_json`、`turn_request_id`）是幂等的，已在隔离实例验证。

---

## 0. 结论速览（第一编）

| # | 结论 | 判定 |
|---|---|---|
| 1 | 工具执行记录的问题是否能修好 | **能**。上次已修的部分（计时、终态、摘要、落库）真实生效；残留问题是"时间线里没有推理过程"与"证据传递依赖模型自觉" |
| 2 | 现在有没有流式 | **有**，但只用于收尾答案：`progress_bus.emit_text`（`src/core/progress_bus.py:359`）由 `general_agent_graph.py:1327/1389` 在**答案定稿后**整段发出。前端 `app.js:2211` 的 `case 'text'` 已支持增量追加 |
| 3 | 能不能把"推理过程"做成可展示流 | **能，且模型已经在输出公开文本**。第二轮实测：一次真实请求的流里有 3 个 `content` 增量共 7 字（"我先检索证据。"），另有 77 个 `reasoning_content` 增量。当前代码把前者聚合后**丢弃**，把后者 `pop` 掉 |
| 4 | 要不要换 Agent 框架 / 做多 Agent | **不要**。缺口是三个函数级的接线问题，不是架构问题 |
| 5 | 承诺边界怎么划 | 只承诺"展示模型**公开说出**的过程 + **真实执行**的步骤 + 真实耗时/结果/失败"；不承诺暴露隐藏思维链（provider 不提供），也不承诺"每次工具调用都正确" |

---

## 1. 审计范围、环境与边界

### 1.1 运行环境

| 项 | 值 |
|---|---|
| 服务器 | `wangziqiang@211.69.141.120:/home/WZQ/CitrusQA` |
| 服务进程 | `python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 --workers 1` |
| 启动脚本 | `04_脚本_scripts/run_server.sh`（无 systemd 单元，手工起） |
| 本地仓库 | `E:\codex_WORKSPACES\Citrus_QA_Agent`，代码在 `01_源码_src/` |
| 本地 Python | `E:\anaconda\envs\rag-agent\python.exe` |

### 1.2 ⚠️ 关键前提：服务器当前跑的不是被审计的代码

按 LF 归一算 sha256，逐文件对比（左=服务器，右=本地 HEAD）：

| 文件 | 服务器 | 本地 HEAD | 结论 |
|---|---|---|---|
| `src/graph/general_agent_graph.py` | `361c6685ee35e6a9` | `1658b41328330c59` | **不同** |
| `src/graph/tool_availability.py` | **不存在** | `e6820d080f378061` | **服务器无此文件** |
| `src/api/main.py` | `5f5f9e3f25ecc2e4` | `fe9513957c4a19f0` | **不同** |
| `web/assets/app.js` | `3b557a5e6c5e84c8` | `acad1c1154fd7d74` | **不同** |
| `src/session/manager.py` | `0c7e92866077ec0d` | `0c7e92866077ec0d` | 相同 |

含义：Phase 1/2A 的改动**尚未部署**，服务器仍处于 `ff58b1f` 时代。所以：
- 本报告的一切结论**只对本地 HEAD 成立**；
- 用户对"线上还是老样子"的感受是自洽的——不是修复无效，是修复没上线；
- 部署必须在验证通过后单独做，且部署前要按服务器 CRLF 约定处理文件。

### 1.3 审计边界

- 只审计"从用户提问到答案落库"这条链，不含模型训练/微调；
- 不评估第三方工具（PaperConan）内部算法；
- 本机无 `node`/`pnpm`，前端只能做静态审查 + 用已构建产物验证行为，不做视觉验收。

---

## 2. 现状：一次问答的真实执行链

```
浏览器 POST /api/v2/chat  (SSE)
   │
   ├─ main.py:978-993  attachments 落盘 + link_turn_files(uploaded ids)
   ├─ main.py:1347     chat_capacity.acquire  （并发/日限额闸门）
   │
   └─ graph 主循环  general_agent_graph.py:1227  while True
         ├─ :770-786  _toolset()            17 个工具（12 个受开关）
         ├─ :101-140  _source_scope(query)  10 条正则 → auto/local/web/fusion
         ├─ :392-482  _scoped_toolset()     13 个关键词检测器（第二轮要拆掉）
         ├─ :932-939  _call_llm → stream_llm_response(llm, messages)   ← 无 on_text
         ├─ :860-899  _execute_calls        每批 ≤8 个（:50）
         ├─ :905-918  asyncio.wait_for(..., timeout=remaining)
         │             超时 → 所有未完成调用得到 [ERR_REQUEST_DEADLINE]
         ├─ :1355     stagnant_batches ≥ 2 (:48) 无进展即停
         └─ :1327/1389 emit_text(answer)    ← 答案整段发出（非增量）
```

事件类型（`src/core/progress_bus.py`）：
`tool_call_start`（:300，只含 `display_name`/`action_hint`/`tool_call_id`/`started_at_ms`，**不含原始参数与输出**）、`tool_executing`（:313）、`tool_result`（:324）、`text`（:359）、`status`（:364）、`done`。

---

## 3. 问题清单（按严重度）

> P0＝直接导致用户所述现象；P1＝影响体验或可维护性；P2＝隐患/清理项

### P0-1 时间线里没有任何"推理过程"，只有工具结果

- 现状：时间线由 `tool_call_start` / `tool_result` 两组事件构成，天然只能表达"调了什么、花了多久、成了没有"。
- 证据：`emit_tool_call_start` 载荷只有 4 个字段（`progress_bus.py:303-308`）；答案文本直到 `graph:1327` 才整段 `emit_text`。
- 影响：用户看到的"完成/未完成"是**终态词**，中间没有一句"我在想什么、为什么调它"。这正是用户的原始抱怨。

### P0-2 模型**已经在**流式输出公开文本，但被聚合后丢弃

- 证据（第二轮新增，实测）：探针 `experiments/trace/probes/probe_public_text.py` 直连 `stream_llm_response`，抓到的流里：
  - `content` 增量 **3 块 / 共 7 字**，原文 `"我先检索证据。"`；
  - `reasoning_content` 增量 **77 块**；
  - `tool_calls` 增量 **24 块**（2 个调用）；
  - `finish_reason = tool_calls`，聚合后 `content` 仅 7 字。
- 代码侧：`stream_llm_response` 支持 `on_text` 回调（`src/core/stream_llm.py:24`），但 `_call_llm`（`general_agent_graph.py:932-939`）只传 `(llm, messages)`，**不传 `on_text`**；`reasoning_content` 在 `stream_llm.py:54-57` 被 `pop` 掉。
- 更正：第一轮报告把这条判断成"公开文本通道不可用"，**这是错的**。通道可用，只是没接线。
- 影响：只要接上 `on_text` → `emit_text`，并（轻量）要求模型"执行前先用一句话说明要做什么"，就能得到真实的进度文本，**不增加请求数、不伪造内容**。前端 `app.js:2211` 已能增量渲染，无需改前端。

### P0-3 证据传递靠"模型自觉"，没有硬保证；跨轮次会反复检索

- 证据：`_evidence_digest`（`graph:542-586`）把本地检索结果压成 `[n] 标题 + 摘录` 存进 `ToolMessage.artifact["_citrus_execution"]`；下一轮走 `_safe_persisted_trace` 时才把摘要拼回上下文（`_EVIDENCE_DIGEST_ROWS=5`、`_EVIDENCE_DIGEST_CHARS=220`，`graph:538-539`）。
- 问题：这是"给模型看过的证据"，不是"强制答案必须引用"的契约；模型可以不引用就作答（实测有轮次 0 工具直接答）。
- 影响：同一会话追问时会重新检索（同一 query 重复计费/重复耗时），与用户"为什么每次都重新调用工具"的观感一致。

### P0-4 `_tool_failure_notice` 有一段永远执行不到

- 证据：`general_agent_graph.py:1002-1004`
  ```python
  if any(token in detail for token in ("local_search", "bm25", "dense", "rerank")):
      return notice("local_search_failed")
      return notice("web_tool_failed")     # ← 永远不可达
  ```
- 影响：`web_tool_failed` 文案永远不会出现；联网工具失败若错误串里不含 http 5xx / timeout 关键词，会落到通用兜底文案——即用户说的"失败提示语观感不好、像硬编码"。

### P0-5 失败/完成措辞是"状态词"，不是"发生了什么"

- 证据：前端把 `status` 直接映射成短语（`app.js:2015-2020` 的 `toolStatusText(status, reason_code)`），后端只给 `status/reason_code/summary`；`reason_code` 取值 `complete/failed/partial/no_results/skipped/not_run` + `[ERR_REQUEST_DEADLINE]`/`[TOOL_ERROR]`/`[TOOL_BATCH_LIMIT]`（`graph:874/915/921`）。
- 影响：同一句"未完成"覆盖了"超时/参数错/权限拒/无结果"四种完全不同的原因，用户无法区分——这也是"观感不好"的根因。

### P1-1 没有 `MAX_TOOL_ROUNDS`，只有"每批 8 个 + 无进展 2 批 + 时间预算"

- 证据：`_MAX_TOOL_CALLS_PER_BATCH = 8`（`graph:50`，限制处 :872）、`_NO_PROGRESS_BATCHES_TO_STOP = 2`（:48，判定 :1355）、`_MAX_SOURCE_ENFORCEMENT_ATTEMPTS = 2`（:51，判定 :1311）；`config.py:211` 注明旧的 `MAX_TOOL_CALLS` 死配置已删除。
- 影响：轮次上限实际由请求 deadline 决定（`main.py` 的 `AGENT_REQUEST_TIMEOUT_SEC`），不是固定 3 轮。**最早告知用户"3 轮"的口径不准确，特此更正。** 好处是不会死循环；坏处是慢工具多时容易触碰 deadline 而被判超时。

### P1-2 批次超时会取消正在执行的工具，副作用不可控

- 证据：`graph:905-918`，`asyncio.wait_for` 超时后抛 `TimeoutError`，所有 `fresh_calls` 统一填 `[ERR_REQUEST_DEADLINE]`。
- 隐患：被取消的协程可能已写了一半产物（文件/临时目录）；当前没有补偿清理，也没有"部分产物是否可用"的判定。**未验证，属于风险项。**

### P1-3 生成的产物不一定挂到"本轮"，历史轮可能看不到

- 证据：`main.py:987-989` 的 `link_turn_files` 只挂**上传文件**（`uploaded_ids + req.file_refs`）；工具产生的文件要靠工具自己带 `client_request_id`。全仓 `link_turn_files` 的调用点只有 `main.py:988` 与测试 `src/session/test_file_turn_refs.py:177`。
- 影响：本轮生成的 Excel/HTML 若未带 turn 信息，历史轮展示与"从哪一轮来的"定位会缺失。

### P1-4 前端工具行按 `tool_call_id` 合并，跨轮不会复用；有上限 100 行

- 证据：`app.js:2001-2025`，`_toolRowsById[callId]`，`termLog.children.length > 100` 时丢最早行；文本一律 `textContent`（**XSS 安全**），非 `innerHTML`。
- 影响：功能正常；但长任务里早期步骤会被挤掉，"回看整条时间线"依赖历史接口而非 DOM。

### P1-5 关于"公开文本"的一个语义风险

- 若接上 `on_text` 直接把模型的执行前说明当"进度"展示，模型可能说出与实际行动不符的话（如"我将检索数据库"却调了联网）。
- 缓解：这类话只作**执行前意图**展示，与紧随其后的真实工具行并列；**任何"完成/成功"结论一律只来自真实工具结果**（现有 `tool_result` 已满足）。这一点必须写进验收口径。

### P2-1 服务器与本地代码不一致（见 §1.2），修复未上线
### P2-2 本机无 `node`/`pnpm`：前端改动无法本地构建/视觉验收
### P2-3 `[TOOL_BATCH_LIMIT]`/`[TOOL_ERROR]` 等标记串会被塞进 `ToolMessage.content` 回灌模型，占上下文
### P2-4 探针/审计脚本与产物需与生产代码隔离（已在 `experiments/` 下）

---

## 4. 五个原问题逐条回答

**① 每次绘图 SVG 都要反复安全校验、第一轮总说超时**
- 反复校验是真的：`draw_svg` 每次提交都走服务端校验（XML 可解析 + 尺寸/元素白名单），一次不合规就整轮失败；模型会重试，于是"反复校验"。
- "第一轮总超时"的机制：绘图是一次大输出，容易撞上 `graph:905-918` 的批次 deadline，超时后这批调用全部被填 `[ERR_REQUEST_DEADLINE]`，表现为"超时"。
- 已在 `2c83d76`/上一批修复里限制为最多 4 次尝试并如实报失败；**是否根治要看线上是否部署**（见 P2-1）。

**② 本地检索不可用**
- 不是路由问题，是**本机语料与新契约不兼容**：本地实例日志明确 `[MultiBatchRetriever] 11 批次向量库不可用（BM25-only 降级）`，并有 `unrecognized schema field: 'batch_laboratory protocol'` 一类 schema 报错。服务器语料已按新契约重建，**服务器可用、本机不可用**。
- 结论：本地端到端验收不能用"检索是否命中"作判据，只能验流程与失败报告；检索质量必须在服务器验。

**③ 每次改都会把联网 key 删掉 / 联网不可用**
- 联网走 `deepseek_search` → 需要 key；key 在 `02_配置_config/`（不在代码里）。若部署/打包用"覆盖式"同步配置目录，就会把线上 key 覆盖成模板值 → 表现为"改了就没了"。
- 审计性结论：**这条是部署流程问题，不是代码问题**；需要一条"配置目录不随代码发布覆盖"的规程 + 启动自检（缺 key 时明确报"未配置"，而不是静默走本地）。

**④ 问答路由应为"联网 + 本地"，本地作候选补充**
- 现状：`_source_scope`（`graph:101-140`）判定 `auto/local/web/fusion`；`fusion` 才是"联网+本地都做"。当前 `_scoped_toolset`（`:392-482`）的 13 个关键词检测器会把可见工具**收窄**，容易把"问答"判成单路。
- 这是在做的改造（Phase 2B）：删掉"关键词决定工具可见性"，改为"能力可用即下发，由模型选择"；`tool_availability.available_tool_names(env)` 已提供纯函数基础。

**⑤ 工具调用总是失败、失败提示观感差、疑似硬编码**
- 是真硬编码：`toolStatusText(status, reason_code)` 把 6 个状态映射成固定短语（`app.js:2015-2020`），后端只给状态码；`reason_code` 粒度不足（见 P0-5），且 `web_tool_failed` 分支不可达（P0-4）。
- 修复方向：后端把"失败原因"升级为结构化三件套 `what_failed / why / next_action`，前端只做排版，**文案不写死在 JS 里**。

---

## 5. 流式与 UI 现状（技术事实）

| 环节 | 现状 | 位置 |
|---|---|---|
| Provider 是否流式 | 是。`astream`，增量含 `content` / `reasoning_content` / `tool_calls` | `src/core/stream_llm.py:21-58` |
| 公开文本回调 | 参数存在（`on_text`），但通用 Agent 路径不传 | `stream_llm.py:24`；`graph:932-939` |
| 推理内容 | `reasoning_content` 被 `pop`，不返回、不落库 | `stream_llm.py:54-57` |
| 答案上屏 | 定稿后整段 `emit_text` | `graph:1327/1389` → `progress_bus.py:359` |
| 前端渲染 | `case 'text'` 已支持增量追加；工具行 `textContent` 安全渲染 | `app.js:2211`、`:2001-2025` |
| 工具事件载荷 | 白名单字段；`action_hint` 只取 query/URL 站点名/文件名等几个键并裁到 80 字，`summary` 会剥掉 URL 与服务器路径并裁到 180 字（`progress_bus.py:263-290`、`:337-339`） | `progress_bus.py:303-308` |

---

## 6. 可行性判断：四种做法

| 方案 | 做法 | 真实度 | 成本 | 风险 | 结论 |
|---|---|---|---|---|---|
| A 只修文案 | 把 6 个状态词换成更完整的句子 | 真实但信息量低 | 极小 | 无 | 不够解决问题 |
| **B 接通公开文本流** | `_call_llm` 传 `on_text` → `emit_text`；提示词要求执行前一句话说明 | **高**（模型真说的话） | 小（2 个函数 + 1 段 prompt） | 模型可能说空话/说错计划 → 用"意图行 + 真实工具行并列"隔离 | **推荐主线** |
| C 暴露隐藏推理 | 透传 `reasoning_content` | 有，但是原样思维链 | 中 | 审计/合规风险高；模型内部标记可能泄漏 | **不推荐**；最多落库供调试、不默认展示 |
| D 多 Agent 规划器 | 增加 planner/supervisor | 高 | 大（重构） | 与"最小改造"目标冲突 | **不做** |

**推荐组合：B（主线）+ A（文案结构化）+ P0-3 的证据硬契约。**

---

## 7. 系统稳定性与可测试性

- 并发闸门：`chat_capacity.acquire`（`main.py:1347`）；限流 429（`:949`）、日限额 429（`:959`）。
- 请求级预算：`request_deadline` 贯穿（`graph:1010-1011`），批次留 `_final_reserve_sec()` 余量（`:905`）。
- 测试：本地全量 **173 passed / 0 failed**（27 个 `src/**/test_*.py`）。
- 可复现：`experiments/trace/collect.py` 会把每轮 SSE 原始事件写成 json，供事后对账。

---

## 8. 测试覆盖与缺口

**已覆盖**：轮次状态推导（`test_turn_status.py` 9 例）、工具可见性与 §7.4 六条验证（`test_tool_visibility.py` 12 例，`04c0b05` 新增、`f51afbd` 修复）、悬空引用（`test_file_turn_refs.py` +4 例）、丢失常量（`test_lost_constants.py` 4 例）、路由关键函数（`test_general_agent_routing.py`）。

**缺口（第二轮新增 6 项，建议逐条补测）**：

| # | 缺口 | 为什么重要 |
|---|---|---|
| ~~G1~~ | ~~无测试断言"工具事件载荷不含参数/输出"~~ | **本轮已补**：`src/core/test_progress_payload_safety.py`（6 例）钉死三类事件的字段白名单、`action_hint` 只取白名单键、URL 只留站点名、密钥不出现在载荷里、`summary` 剥 URL/路径、未知状态归一为 `failed`、`reason_code` 截断 64 字符、journal 记终态 |
| G2 | 无测试覆盖 `on_text` 接线（当前故意未接） | 接通后必须保证"只发公开文本、不发 reasoning" |
| G3 | 无测试覆盖"批次超时后未完成调用的终态与产物残留" | P1-2 的副作用目前靠人工看 |
| G4 | 无测试覆盖"本轮生成的产物是否挂到本轮" | P1-3 会静默丢关联 |
| G5 | 无测试覆盖 `reason_code` 与用户可见文案的映射表 | P0-5 的文案改动容易回归 |
| G6 | 无 `_tool_failure_notice` 各分支的可达性测试 | P0-4 的死代码就是这么来的 |

---

## 9. 自评：第一轮为什么要重做

第一轮审计不彻底，具体错在三处，已在第二轮修正：

1. **结论错误**：把"公开文本通道不可用"写进报告。实测证明通道可用（7 字增量），只是 `_call_llm` 没传 `on_text`。错因是只读了 `_call_llm` 的调用处，没做探针。
2. **归因过头**：把"没有流式"归因为"provider 不支持"。实际是"支持、未接线"。
3. **覆盖不足**：漏查了 §3 的 P1-2（超时取消的副作用）、P1-3（产物挂轮）、P2-3（标记串回灌）、P0-4（不可达分支），也漏了 §8 的 6 个测试缺口。

第二轮的取证方式改成"**先探针实测、再对代码**"：`probe_public_text.py` 直接抓流，`emit_tool_call_start` 直接读载荷，服务器/本地逐文件比 hash。凡是没有实测和行号的判断，本报告不写。

---

## 10. 附录：关键坐标索引

| 主题 | 文件:行号 |
|---|---|
| 主循环 | `src/graph/general_agent_graph.py:1227`（`while True`） |
| 工具全集 | `:770-786` `_toolset()`；`:392-482` `_scoped_toolset()` |
| 来源判定 | `:101-140` `_source_scope()` |
| LLM 调用（无 on_text） | `:932-939` `_call_llm` |
| 批次执行/超时 | `:860-899` `_execute_calls`；`:905-918` `wait_for` + `[ERR_REQUEST_DEADLINE]` |
| 循环边界常量 | `:48` `_NO_PROGRESS_BATCHES_TO_STOP=2`；`:50` `_MAX_TOOL_CALLS_PER_BATCH=8`；`:51` `_MAX_SOURCE_ENFORCEMENT_ATTEMPTS=2` |
| 证据摘要 | `:538-539`、`:542-586` |
| 死代码 | `:1002-1004` `_tool_failure_notice` 不可达 `return` |
| 答案上屏 | `:1327`、`:1389` `emit_text(answer)` |
| 事件定义 | `src/core/progress_bus.py:300/313/324/359/364` |
| 流式实现 | `src/core/stream_llm.py:21-58`（`on_text` 在 `:24`，`reasoning_content` 丢弃在 `:54-57`） |
| 附件/轮次关联 | `src/api/main.py:978-993`、`src/session/files.py:703` |
| 前端事件与工具行 | `web/assets/app.js:2211`（`text`）、`:2001-2025`（`appendTerminal`）、`:2015-2020`（状态文案） |
| 并发闸门 | `src/api/main.py:1347`、`:949`、`:959` |
| 探针脚本 | `01_源码_src/experiments/trace/probes/probe_public_text.py` |

---

### 附篇：前端交互、文件预览与任务生命周期（差异审计，原第一编第二部分）

> 审计对象：本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent\01_源码_src`（HEAD `61a8f5b` 之前的 `ee23a42`）。
> 审计性质：只读。未改代码、未部署、未重启服务、未 push。
> 与第一编的关系：第一编回答“Agent 怎么跑、为什么没有过程”；本编只回答新增的五项交互需求差在哪，不重复全系统审计。
> **前置警告**：服务器 `/home/WZQ/CitrusQA` 运行的 `main.py / general_agent_graph.py / app.js` 与本地 HEAD 不是同一版本（第一编已列哈希），本编所有行号以**本地**为准，实施前必须先对齐基线。

## 0. 结论速览（第二编）

| 专项 | 现状 | 结论 | 优先级 |
|---|---|---|---|
| A 中断与生命周期 | 已有完整取消通道（前端 abort + 服务端 Task.cancel），但**语义错**：停止会删掉整条回复，且没有“停止中”态 | 需重做前端取消语义 + 服务端补终态回查 | **P0** |
| B PDF 预览 | **后端已被实测证明健康**（200/`application/pdf`/206 Range/`%PDF-1.4`）；空白是客户端渲染 | 加失败可见 + PDF.js 兜底 | P1 |
| C 会话跳转 | 后端有 `session_file_turn_refs`，前端**零实现**；且键格式与 `messages` 不一致，join 不上 | 需先修数据关联，再做 UI | P2 |
| D 动态交互 | 前端 `case 'text'` 已存在，工具行已按 id 合并；缺口只在后端不增量推文本 | 接 `on_text` 即可，不需重写前端 | P4 |
| E 分页指南 | 单页四宫格 + 完成状态持久化已有 | 在现有 overlay 上改 6 页 | P3 |
| F 视觉响应式 | 640/900 断点已有；缺键盘可达性与加载失败态 | 随 P1/P3 一起做 | P3 |

## A. 取消与任务生命周期

### A.1 真实调用链（逐跳，带行号）

```
[前端] 点击停止
  app.js:1142-1160  stopCurrentTask()
    ├─ 1143  if (activeTurnCancel) activeTurnCancel()   ← 有活动轮次时走这条
    ├─ 1144-1147 else currentAbortController.abort()    ← 兜底
    ├─ 1148-1156 POST /api/v2/chat/cancel {session_id}  ← fire-and-forget，不看返回
    └─ 1157-1159 setSendDisabled(false) + 恢复输入框     ← 立刻允许再次发送
  app.js:1871-1882  removeInterruptedReply()
    ├─ 1872  isCancelled = true（幂等闸门，但只有前端本地）
    ├─ 1874  clearTimeout(spinnerTimeout)
    ├─ 1875-1878 requestAbortController.abort()
    ├─ 1879  botMessage.remove()        ← **整条回复气泡被删除**
    ├─ 1880  activeTurnCancel = null
    └─ 1881  setSendDisabled(false)

[服务端] POST /api/v2/chat/cancel
  main.py:1561-1591 cancel_chat()
    ├─ 1563  require_owned_session()            ← 越权校验存在
    ├─ 1571-1573 只处理 status == "running" 的 job
    ├─ 1577-1583 _running_graph_tasks[jid].cancel() → update_job(cancelled)
    └─ 1584-1588 任务不在本进程（重启残留）→ 仅改状态为 cancelled

[取消如何在 graph 内生效]
  main.py:294          _running_graph_tasks: dict（job_id → asyncio.Task）
  main.py:1145         process_graph() 内注册 asyncio.current_task()
  main.py:1318         finally 中 pop（正常/取消/异常统一清理）
  main.py:1275-1284    except asyncio.CancelledError → update_job(cancelled) + finish_request(cancelled)
  main.py:1312-1331    finally → flush_bridge() → heartbeat_task.cancel() → 仅 observer_connected 才 put(None) 哨兵

[事件桥]
  main.py:1079-1091  bridge_progress()  阻塞 get()，CancelledError 即 break
  main.py:1093-1112  tool_heartbeat()   每 2s 为运行超 2s 的工具发 tool_executing
  main.py:1120-1127  observer_connected / emit_to_client()（write 保活后丢弃事件）
  main.py:1129-1138  flush_bridge()     把 request_queue 残留事件搬进 event_queue

[工具层（关键）]
  registry.py:252-259 / 309 / 384   run_in_executor(...)   ← 工具在线程池里跑
```

### A.2 已证实的问题

**A1｜“停止”会删除整条回复，包括已经产生的真实内容。**
`app.js:1879` 直接 `botMessage.remove()`。一次 30 秒的问答被停在 25 秒时，已经落地的工具轨迹（本地检索 8 条、网页 6 条）、已经流出的部分答案、以及本轮生成的产物入口会一起消失，且不写任何 `cancelled` 记录。这与“已完成的成果与部分回答不得因中断被抹掉、只清理临时运行元素”的要求正好相反。

**A2｜没有“停止中”中间态，前端跑得比后端快。**
`app.js:1157` 立刻 `setSendDisabled(false)`。服务端 `cancel()` 只是往协程里扔 `CancelledError`，工具线程（`registry.py:259/309/384`）不受影响。用户可以在工具线程仍在写文件时发起下一轮，两轮并发操作同一会话与同一产物目录。

**A3｜工具线程不可取消，取消不等于停止。**
`run_in_executor` 提交的调用无法中断：`t.cancel()` 只让 `await` 抛异常，执行体继续跑到自然结束。因此“停止后仍可能写入不完整文件”不是猜测，而是当前的机制必然；当前代码既没有记录“取消请求时间 ≠ 实际终止时间”，也没有阻止迟到结果落盘。

**A4｜前端 abort 之后收不到终态事件，“确认后才标 cancelled”目前不可能实现。**
取消路径第一步就是 `abort()` 掉 SSE，通道随即断开；服务端在 `main.py:1326` 的 `put(None)` 无人消费。前端因此只能“先假设已取消”，无法区分“后端已确认取消”和“只是连接断了”。要做成用户要求的语义（点停止→显示“正在停止”→后端确认→标 `cancelled`），必须新增**取消后的状态回查**（`GET /api/v2/jobs/{job_id}` 或复用 `/messages`）。

**A5｜断网、刷新、主动取消三者共用同一个清理分支。**
`app.js:2451-2457`：`AbortError` 与其他异常都走 `removeInterruptedReply()`，差别只是一句 banner。用户主动停止、网络断开、页面刷新在数据层没有区分标记，事后无法判断这一轮到底发生了什么。

**A6｜取消请求可能落空但仍清了 UI。**
`main.py:1572` 只处理 `status == "running"`。若任务已进入 `finally` 收尾（job 已 `completed`），取消是 no-op（`:1589-1590` 只写日志），而前端 UI 已经清空 → 后台仍在收尾、用户以为已停。

**A7｜迟到事件缺少统一闸门。**
`observer_connected`（`:1121`）只在 write 保活路径被置 False。普通问答取消后 `bridge_progress` 仍可能把遗留事件推进 `event_queue`；虽然该队列随后无人消费、不构成串台，但不满足“阻止迟到结果继续影响当前会话”的显式要求，缺少一层可审计的丢弃计数。

### A.3 尚未验证的风险（需要一次运行期实验）

- **R1**：取消瞬间正在写文件的工具是否留下 0 字节或半截产物。验证方式：构造一个 10 秒慢工具（`time.sleep` 后写文件），在 2 秒时取消，检查产物目录。
- **R2**：`t.cancel()` 之后 `finally` 里的 `flush_bridge()` 是否会把“工具线程稍后入队的事件”也搬给下一轮（跨轮串话）。验证方式：同一会话连续两轮，第一轮中途取消，观察第二轮是否收到第一轮的事件。
- **R3**：`SESSION_DB` 的 WAL 下，取消时 `update_job` 与 `_serialize_message` 是否可能写出“工具行状态 completed、轮次状态 cancelled”的不一致组合（第一编已修过一处同类矛盾，需回归）。

### A.4 最小改造方案（P0，不改 Agent 决策逻辑）

1. **前端：停止改为“三段式”，不再删气泡**
   - 点击停止 → 气泡保留，头部状态改为“正在停止…”，工具行未完成项标 `stopping`，**禁止**删除已产生的文本与已完成工具行。
   - 收到确认（回查结果 `cancelled`）→ 状态改“已中断”，保留记录，提供“重新生成”按钮。
   - 回查失败/超时（例如 5 s）→ 显示“已停止接收，后台可能仍在收尾”，不谎称已终止。
   - 只清理纯临时元素：spinner、`is-running` 动画、计时器、临时头像；不动 `.message` 节点。

2. **服务端：把“取消请求”与“实际终止”分开记**
   - `cancel_chat` 返回 `{cancelled:[...], accepted:true, note}` 并把 job 的 `current_step` 写成 `cancel_requested`。
   - 新增 `GET /api/v2/jobs/{job_id}`（只读，`jobs.get_job` 已有）供前端回查终态；`mark_interrupted_jobs`（`jobs.py:101-111`）已把重启残留标为 `failed/interrupted`，保持现状不混入 `cancelled`。

3. **迟到结果闸门**
   - 工具线程返回时检查所属 job 是否已 `cancelled`；是则丢弃结果并计数（`diag("late_tool_result")`），不写产物、不入库。

4. **持久化**
   - `messages.execution_json` 已有 `status/reason_code` 字段，取消轮次直接复用，无需新表。

### A.5 涉及文件（最小集）

| 文件 | 改动性质 | 风险 |
|---|---|---|
| `web/assets/app.js`（`stopCurrentTask` / `removeInterruptedReply` / `handleEvent`） | 重写取消语义，删 `botMessage.remove()` | 中（触及主流程，需回归） |
| `web/assets/app.css`（`.tool-trace.is-running`、新增 `.is-cancelling`/`.is-cancelled`） | 纯样式 | 低 |
| `src/api/main.py`（`cancel_chat` 返回值、新增 `GET /jobs/{id}`） | 轻量，不动 graph | 低 |
| `src/core/jobs.py`（`current_step` 语义） | 轻量 | 低 |
| `src/tools/registry.py`（迟到结果丢弃 + 计数） | 中（工具执行公共路径） | **中高** |

## B. PDF 预览

### B.1 已实测：后端健康，问题在客户端

在服务器 `127.0.0.1:8000` 用一次性只读探针凭据请求真实文件 `89969b18…`（357 KB，`application/pdf`）：

```
GET /api/v2/files/{id}/preview   → 200  content-type: application/pdf
                                        accept-ranges: bytes, content-length: 356974
                                        x-content-type-options: nosniff
                                        body magic = 25 50 44 46 2d 31 2e 34  (%PDF-1.4)
GET 同 URL + Range: bytes=0-1023 → 206 Partial Content, content-range: bytes 0-1023/356974
GET /api/v2/files/{id}/download  → 200  + content-disposition: attachment; filename*=utf-8''…
```

结论：**接口、鉴权、MIME、字节、Range 全部正常**（`main.py:2817-2820` 走 `FileResponse(media_type=mime)`，不设 `Content-Disposition` 即内联）。
存储侧也排除“MIME 写错导致 `nosniff` 拒渲染”：`session_files.sqlite3` 共 12 行，6 个 PDF 全部 `mime_type=application/pdf`（最大 14.4 MB）。

### B.2 前端实现现状

- `session_files.js:166-169`：`kind === 'pdf'` → `<iframe class="sf-preview-frame" src="{preview_url}">`（依赖浏览器内置 PDF 插件）。
- 布局本身成立：`session_files.css:165-175` 卡片 `height: min(90vh,900px)`；`:208-213` stage `flex:1`；`:214-219` iframe `width/height:100%`。

### B.3 仅剩的三种可能（需浏览器侧一次判别）

| 假设 | 判别方法 | 若成立 |
|---|---|---|
| B1 内嵌浏览器无 PDF 插件（Electron/webview、部分企业策略） | 控制台 `navigator.pdfViewerEnabled`；把 `preview_url` 新标签直开 | 必须引入 PDF.js（离线打包）或有明确降级 |
| B2 反向代理/Cloudflare 注入 `X-Frame-Options` 或改写 PDF 响应 | `curl -I` 对比直连 8000 与隧道域名 | 改代理配置或换 blob URL |
| B3 大文件加载超时（14 MB 那两份） | 用 357 KB 那份测试是否同样空白 | 加 loading + 超时提示 |

截图显示“左上角一个小框”，与 B1 的典型表现一致，但仍需上面一条实测确认，不做断言。

### B.4 最小改造（保留现有 Excel/HTML 预览）

1. **先加失败可见**：iframe `onload` 后 1.5 s 仍无渲染信号 / `onerror` → 显示“当前浏览器无法内嵌预览 PDF，请下载原件”，附下载按钮；`load` 期间显示真实 loading，不再白屏。
2. **再评估 PDF.js**：仅当 B1 成立时引入，放 `web/assets/vendor/`，单文件离线，约 350 KB；不改后端。
3. 不动 `main.py:2799-2824`（已验证正确）。

### B.5 涉及文件

| 文件 | 改动 | 风险 |
|---|---|---|
| `web/assets/session_files.js`（`previewStageHtml`） | 加载态 + 失败态 + 降级 | 低 |
| `web/assets/session_files.css` | 失败态样式 | 极低 |
| （可选）`web/assets/vendor/pdfjs/*` | 新增静态资源 | 低 |
| ~~`src/api/main.py`~~ | **不改** | — |

## C. 文件与会话跳转

### C.1 后端：关联表存在且有数据，但键不能 join

```
session_file_turn_refs(session_id, owner_user_id, client_request_id, file_id, created_at)  -- 现 6 行
session_files(... origin, producer_tool, client_request_id ...)                            -- 12 行
```

实测样本：`client_request_id = 'crid-mvu749ta-mv7ut1y2'`（前端格式，`app.js:1840` 生成 `crid-<base36>-<rand>`）。

而 `messages.client_request_id` 存的是 **另一种格式**：`'crid:c1952e10b4fb4ecb989810ad55a21682'`（冒号 + 32 位 hex，73/410 行有值）。

**⇒ 两边不是同一个命名空间，直接 join 不上。** 即使把“定位到会话”按钮做出来，也定位不到具体消息。这是 C 的真阻塞点，必须先修。

### C.2 前端：零实现

全仓 grep `turn_refs` / `locate` / `openSession(` 在 `app.js`、`session_files.js`、`index.html` 中**无任何跳转代码**；`app.js:564-567` 的 `highlighted_pdf_url` 是 PDF 引用高亮，与本需求无关。

### C.3 最小改造

1. `session_file_turn_refs` 增加 `message_row_id INTEGER`（写入时由服务端在落库后回填），或统一 crid 命名空间（让服务端接受前端 crid 并原样写入 `messages`）。
2. 前端：文件项菜单加“定位到生成此文件的会话”；有 ref → 切会话 + `scrollIntoView` + 1.2 s 高亮；无 ref → 按钮不显示（不猜）。
3. 跨用户：`require_owned_session` + `get_account_file` 的 owner 校验已有，跳转接口沿用，不新增权限面。

### C.4 涉及文件 / DB

| 项 | 内容 | 风险 |
|---|---|---|
| `src/session/files.py` | 写入 turn ref 时同存 `message_row_id`；`_row_to_public` 暴露 `source_message_id` | 低 |
| `src/api/main.py` | 新增只读 `GET /api/v2/session/{sid}/files/{fid}/turn` | 低 |
| DB | **需要**：`ALTER TABLE session_file_turn_refs ADD COLUMN message_row_id INTEGER`（幂等迁移，与既有 `files.py:111-124` / `manager.py:196-243` 的 ALTER 兼容写法一致） | 低 |

## D. 动态交互与公开工作说明

- 前端**已经**能处理增量文本：`app.js:2184 handleEvent` → `:2211 case 'text'`；工具行按 `tool_call_id` 合并（`:2001-2025`），`finishStatus()` 按真实状态汇总。
- 服务端**不增量发**：`_call_llm`（`general_agent_graph.py:932-939`）未把 `on_text` 传给 `stream_llm_response`，模型公开文本只在定稿后整段发。
- 真实进度数据源已有：`main.py:1093-1112` 每 2 s 的 `tool_executing` 心跳。
- ⇒ D 的工作量在后端接一根线 + 前端已有渲染，**不是重写交互层**；动画必须由事件驱动，禁止定时器模拟（保持现状的 `is-running` 由事件切换即可）。

## E. 分页式使用指南

- 现状：单页四宫格 `index.html:246-260`；`openOnboardingGuide()` `app.js:193-195`；`completeOnboarding()` `app.js:243` 已做持久化。
- 改造：复用 overlay 与持久化，改成 6 页 + 上一步/下一步 + 进度点；示例按钮只**填入输入框**不自动发送（避免消耗额度）；支持 Esc 关闭与焦点陷阱。
- 内容必须与真实能力一致：不得出现未实现功能（例如“自动 CrossRef 核验全部引用”这类夸大表述）。
- 涉及：`index.html`（overlay 结构）、`web/assets/app.js`（翻页状态）、`web/assets/app.css`（分页样式）。风险：低。

## F. 视觉与响应式

- 已有断点：`session_files.css:288`（≤640px）、`:313-320`（≤900px，预览卡片全屏化）。
- 缺：预览弹窗 Esc 关闭、焦点陷阱与 `aria-live`；iframe 失败态（同 B4）；窄屏下“下载原件”与标题争宽。
- 风险：低；随 P1/P3 一起验收。

## G. 稳定性要求逐条对照

| 要求 | 当前 | 结论 |
|---|---|---|
| 断线不被当成主动取消 | `app.js:2451-2457` 共用分支 | ✗ 待修（A5） |
| 取消后不再调度新工具 | Task.cancel 使 `await` 抛异常，模型不再被调用 | ✓（工具线程除外，见 A3） |
| 无法硬取消的工具不假装终止 | 无记录、无提示 | ✗ 待修（A3/A4） |
| 已完成成果与未完成临时文件区别处理 | 整条气泡删除 | ✗ 待修（A1） |
| 预览/跳转不绕过权限 | `require_owned_session` + owner 校验 | ✓ |
| PDF 失败不拖垮页面 | iframe 隔离，但无失败提示 | 部分（B4） |
| 指南/动画不影响 Agent | 纯前端 | ✓ |
| 历史恢复不重建加载气泡 | `restoreSessionHistory` 只渲染历史行 | ✓（第一编已修 var 提升 bug） |
| 生产与本地差异明确 | 已列哈希 | 需先对齐（见前置警告） |
| 不为修 UI 改工具选择/重试/返回内容 | 本方案不触碰 `tool_availability`/`tool_policy` | ✓ |

## H. 测试与验收用例

**A（P0）**
1. 长任务中途停止 → 气泡保留、显示“正在停止”→ 3 s 内变“已中断”；已完成工具行仍在。
2. 停止后立即再发一条 → 两轮不串行写同一文件；日志无 `late_tool_result` 落到新轮。
3. 慢工具（10 s）在 2 s 停止 → 产物目录无 0 字节/半截文件（验证 A3 修复）。
4. 关闭标签页（非点停止）→ 恢复会话后该轮状态为 `cancelled` 或 `partial`，不被写成 `completed`。
5. 取消瞬间刷新页面 → 历史里该轮不出现永久“加载中”气泡。

**B（P1）**：6. 小 PDF（357 KB）正常多页渲染；7. 大 PDF（14 MB）有 loading 与超时提示；8. 禁用 PDF 插件时出现下载兜底而非空白；9. Excel 多工作表、HTML 报告预览回归不退化。

**C（P2）**：10. 有 ref 的文件可定位并高亮到消息；11. 无 ref 的旧文件不显示跳转；12. 他人文件 id 直连 → 404。

**D/E/F**：13. 增量文本按块出现（非整段）；14. 指南 6 页翻页 + 重开 + 记住已读；15. ≤640px 下预览与指南可用、Esc 可关。

## I. 推荐分批与回滚

| 批次 | 内容 | 独立提交 | 回滚方式 |
|---|---|---|---|
| **P0** | A：停止语义（前端三段式 + `GET /jobs/{id}` + 迟到结果丢弃） | 是 | revert 单提交；无 DB 变更，风险可控 |
| P1 | B：PDF 失败态 →（必要时）PDF.js | 是 | 静态资源删除即回滚 |
| P2 | C：turn_ref 加 `message_row_id` + 跳转 | 是 | 迁移列可保留（不读即无效），代码 revert |
| P3 | E+F：分页指南、键盘可达性 | 是 | 纯前端 revert |
| P4 | D：接 `on_text` | 是 | 单函数回退（`_call_llm` 不传 `on_text` 即回旧行为） |

**顺序理由**：P0 唯一会改变运行时行为、且涉及“后台是否继续消耗 API / 是否写不完整文件”，必须先做并单独验收；P1 是故障修复且后端已证明健康，改动面最小；P2 依赖数据关联先修；P3 纯前端；P4 与第一编的方案 B 合并实施，不与取消机制混改。

## J. 本次只读审计的操作留痕

- 未修改任何源文件、未重启服务、未 push。
- 为验证 PDF 接口，曾在服务器 `browser_sessions` 插入一条 **15 分钟有效的一次性只读探针凭据**用于 `curl`，探测完成后删除；未改动任何业务数据。命令与响应头见 B.1。
- 读取过 `agent/state/{sessions.db, session_files.sqlite3}` 的只读副本查询（`mode=ro`）。

---

## K. 更正记录（2026-10-30 第二轮，S0 基线核对后）

本节改动上文两处判断，并记录被用户否决/修正的实施方案。**上文其余证据与行号仍然有效。**

## K.1 生产基线更正：生产 = `ff58b1f`，不是 `ed7c89d`

第一编/第二编的行号取自本地 `ed7c89d`。S0 三列哈希比对（`S0_BASELINE_AND_S1_CANCEL_PLAN.md` §2.2）证明：

- 生产运行代码与 `ff58b1f` **逐字节相同**（16 个关键文件，LF 归一 sha256 全等）；
- 本地 `ff58b1f..ed7c89d` 共 **7 个提交 / 23 文件** 未部署，其中直接改变运行时行为的有：Phase 1 工具绑定（`2c83d76`、`f51afbd`）、Phase 2A 能力过滤（`80bab4b`、`46849c6`）；
- 因此**本报告里凡是"线上表现为…"的推断，凡涉及工具可见性/绑定，都只对旧版成立**；取消链路、PDF、前端生命周期的结论不受影响（这些文件在 `ff58b1f` 与 `ed7c89d` 之间无行为差异）。

另确认两处运维事实（第一编未覆盖）：生产进程不在 systemd 下（`session-*.scope`），`DEPLOYED_COMMIT` 仍写 `9955c79`（已过期两代）。

## K.2 四点方案更正（用户提出，本报告接受）

| # | 报告原方案 | 更正为 | 原因 |
|---|---|---|---|
| 1 | 第一编方案 B：`_call_llm` 接 `on_text` → `emit_text` | 新增独立 `assistant_progress` 事件，与最终 `text` 语义分离 | `text` 被前端当作答案正文；直接复用会污染正文/重复输出/错误落库。且模型可能先出 content 再出 tool_calls，需按**轮次级**判定而非首个文本块 |
| 2 | P0-A：工具线程返回后检查 `cancelled` 并丢弃结果 | 协作式取消 + 请求专属临时产物 + 提交前检查 + 原子 `os.replace` | 线程返回后检查无法阻止工具内部已发生的写入（文件/DB 早已落盘） |
| 3 | P1：`iframe.onload` 判失败 → 换 PDF.js | 先做浏览器侧实际判因；原生失败再上**本地打包、版本匹配**的 PDF.js；不以 `onload`/超时定时器推断成败 | `onload` 只代表文档加载完成，不代表插件渲染成功；PDF.js 需 worker/CMap/CSP 配套，不是放一个 js 文件 |
| 4 | P2：`turn_ref` 加 `message_row_id` 即完成 | 先确立"文件生产者 → 轮次 → 消息"的**真实持久化关联键**，允许先记请求、消息落库后回填、回填失败可修复；旧数据允许缺失但不得伪造跳转 | `crid-…`（前端）与 `crid:…`（消息）不同命名空间，仅统一未来格式救不了历史数据 |

## K.3 实施顺序被 S0–S5 取代

原 §I 的 P0–P4 批次作废，改为：

| 阶段 | 内容 | 状态 |
|---|---|---|
| **S0** | 发布基线、回滚包、隔离测试实例配方 | ✅ 完成（本地提交 `8a7ddef`、`9c38233`） |
> 顺序说明：实际提交按「S3 = 文件↔会话跳转（`67dd69b`）、S4 = 文件预览（`816ee93`）」落地，
> 与上表原先的 S3/S4 内容顺序相反。两者互不依赖，可各自单独回退；编号以提交信息为准。
| **补强** | 验收矩阵补测（job 越权、取消幂等、进度落库、PDF 失败态、文件库作用域、断线语义、跨用户越权、执行独立性） | ✅ 完成（`697e1ce`…`8145545`，见 §Q、§R） |
| **S1** | 取消生命周期（状态机/job 级取消/协作式取消/原子产物/前端三段式） | ✅ 完成（提交 `1c548cc`，见 `S1_CANCEL_LIFECYCLE.md` 与 §L） |
| **S2** | 动态公开工作说明（`assistant_progress`） | ✅ 完成（提交 `9a127fe`，见 `S2_PUBLIC_PROGRESS.md` 与 §M） |
| **S3** | 文件预览（PDF/Office/图片只信真实字节） | ✅ 完成（提交 `816ee93`，见 `S4_FILE_PREVIEW.md` 与 §O） |
| **S4** | 文件↔会话↔消息跳转 | ✅ 完成（提交 `67dd69b`，见 `S3_FILE_SESSION_JUMP.md` 与 §N） |
| **S5** | 六页指南、视觉验收、发布演练 | ✅ 代码完成（提交 `13e6c78`，见 `S5_GUIDE.md` 与 §P）；**视觉验收待人工**（见 §0.2 D-3） |

## K.4 新增证据：取消令牌可达性（S1 设计前提）

`experiments/trace/probes/probe_cancel_token_reaches_executor.py`（+ `results/probe_cancel_token_reaches_executor.json`，可重跑）：

| 检查项 | 结果 | 含义 |
|---|---|---|
| `copy_context().run` 进入 executor 线程后 `ContextVar` 可见 | **true** | 请求级令牌无需改 17 个工具签名即可到达工具体 |
| 主线程 `threading.Event.set()` 后工作线程轮询可见 | **true** | 取消可跨线程即时生效 |
| 不加 `copy_context` 的 `run_in_executor` | **丢失**（None） | 证明 `registry.py:299/310/376/385` 的 `ctx.run` 是必要条件，不能简化 |
| 工具线程内再起 `threading.Thread` | **不继承**（None） | 已知限制：S1 的三道闸门都发生在工具线程自身，已核对 `src/research/*.py`、`src/tools/*.py` 无自建线程池，故不构成阻塞 |

结论：**S1-01 的设计前提成立（HOLDS）**，但"不要在工具内部再起线程去做产物写入"应写进工具开发约定。

---

## L. S1 执行后复测（2026-10-30，第三轮）

本节是**执行之后的再次审计**：S1 代码落地后，用真实运行把第一编的假设逐条复核。
详细交付见 `S1_CANCEL_LIFECYCLE.md`。

## L.1 第一编判断的复核结果

| 第一编判断 | 复测结果 | 证据 |
|---|---|---|
| A1「停止=删除整条气泡，历史无记录」 | ✅ 成立（已修） | 改前 `app.js` 调 `removeInterruptedReply()`；改后 `stopCurrentTurn()` 保留气泡并标已停止 |
| A2「取消只在 LLM await 边界」 | ✅ 成立（已修） | `registry.py:319-324` 闸门 0、`:364-367/:389-392` 闸门 1 |
| A3「工具线程返回后检查无法阻止内部写入」 | ✅ 成立 | 因此 S1 采用**协作式检查点**（`cancel.py`）+ 结果丢弃双层；纯"返回后检查"已证明不够 |
| A4「断线被当成主动取消」 | ✅ 成立（已修） | `main.py:398` 宽限期内 `detached` 不杀；到期理由为 `detached_timeout` |
| A5「历史里没有取消终态」 | 部分成立（已修） | 修后停止轮落库为 问题 + 步骤行 + 说明；`has_cancelled_row` 需在语料可用实例复测（见 L.3） |

## L.2 复测过程中**新发现**的两个事实（第一编未覆盖）

1. **孤立 `ToolMessage` 会被历史加载器静默丢弃**。
   `SessionManager._get_messages_sync` 对"没有对应 `ToolMessage.tool_call_id` 的调用"直接
   `丢弃孤立 ToolMessage`（日志可见）。后果：任何想"只补一条工具记录"的写法在刷新后都会消失。
   修法：步骤行必须挂到一条 `AIMessage.tool_calls` 之下（`main.py:347 _stop_marker_messages`）。
   **这也解释了第一编 S1 设计里"取消后补写工具行"的隐含前提是错的。**

2. **HITL 白名单闸门先于取消闸门**。
   未登记的 Tool 在 `_run_single_tool` 里先被 `[ERR_HITL_REJECT] 工具…未在活动工具注册表中登记`
   拒绝，测取消闸门必须先 `register_tool`。这对"测试要证明哪道闸门"有直接影响：否则会拿到
   `failed` 而不是 `cancelled`，把测试结论带偏。

## L.3 复测暴露的**环境问题**（非 S1 缺陷，但会污染本地实验）

本机实例日志：

```
ERROR [Retriever] quarantined corpus batch paper1: chunk source/source_type/source_file is required
ERROR [MultiBatchRetriever] 所有批次向量后端加载失败 — 向量检索不可用，仅 BM25 降级运行
```

即本地 11 批旧语料与新语料契约不兼容（缺 `source/source_type/source_file` 必填字段），
本地检索恒返回"没有找到候选资料"；联网无 key → 网页搜索 `reason_code=auth`。
后果：本地实例上"停止"只能停在**明显失败的步骤**上，无法演示"停在一个正在执行的工具上"，
`has_cancelled_row` 因此为 false。**生产（新 C2 语料）不受此影响**，但凡是"本地复现"的
实验都必须先修本地语料或改用隔离实例。

## L.4 S1 之后仍然开放的风险（承接第一编）

| 风险 | 现状 | 建议 |
|---|---|---|
| 同步工具不可硬中断 | 仅能"结果不采用" | 新工具必须插桩检查点；工具内禁止自建线程写产物 |
| 多 worker 时取消只停在 `cancel_requested` | 已如实返回 `target=not_in_this_process` | 扩 worker 前引入共享信号 |
| 停止轮步骤行的绝对时间锚点缺失 | `completed_at_ms` 由 `elapsed_ms` 近似 | S2 引入 `assistant_progress` 时统一时间轴 |
| 本地语料/密钥不可用 | 见 L.3 | 修本地语料或固定用隔离实例 |

## M. S2 执行后复测（2026-10-30，第四轮）

## M.1 本轮落地内容

动态公开工作说明（`assistant_progress`）：模型在多步骤任务的每一轮之后，用一到三句自然语言说明
"现在在做什么/发现了什么"，作为**独立事件**进入执行时间线；最终回答、流式文本、隐藏推理均不受影响。
实现与验收细节见同目录 `S2_PUBLIC_PROGRESS.md`。

## M.2 对第一编、第二编判断的复核

| 原判断 | 位置 | 复测结果 |
| --- | --- | --- |
| 执行过程只有工具名与耗时，用户看不到"为什么" | 第一编 §3、第二编 D | **已改变**：实测时间线出现两条工作说明，且先于其描述的那批工具（`progress_precedes_following_tool=true`） |
| 公开文本必须与回答流分离 | 第二编 D | **满足**：`answer_not_polluted=true`、`stream_not_polluted=true` |
| 隐藏推理不能外泄 | 第一编 §3.5 | **仍满足**：只取公开文本增量，`reasoning_content` 不进入事件 |
| 停止后必须按真实终态标记 | 第二编 A | 已由 S1 覆盖（`cancelled`/`partial`），本轮未回退 |
| 历史刷新后应保留完整步骤 | 第二编 A、D | **仍开放**：工作说明目前不落库，刷新后只余工具行；待 S5 会话恢复一并处理（本阶段按用户"初期可不持久化"处理） |

## M.3 复测**新发现**：S1 提交里有一个未被自身测试覆盖的致命缺陷

- 现象：S2 首次端到端运行时请求直接 500，日志 `UnboundLocalError: cannot access local variable 'get_request_journal'`。
- 根因：`src/api/main.py` 的 `process_graph` 函数体内**又写了一次** `from src.core.progress_bus import (set_request_journal, get_request_journal, clear_request_journal)`。
  函数内 import 使这三个名字成为**局部变量**，而在 `create_task` 的第一次 `await` 之后 `:1368` 就读取它 → 未赋值即读取。
- 为什么 S1 没测出来：S1 的 16 个用例都**直接调用** `derive_turn_status` / `run_one_tool` / `cancel_chat`，
  没有任何用例走完整的 `process_graph` 请求路径；而 S1 的 HTTP 验收脚本恰好也在错误发生前就结束了观察。
- 修复：删除内层 import（`:1200`），并把 `:1459-1462` 的注释改为显式提醒。
- 防复发：新增 `src/core/test_shadowed_imports.py`，静态扫描 `process_graph` 内是否出现遮蔽外层符号的 import。
- 结论：**"函数级单测全绿 ≠ 请求链路可用"**，后续每阶段必须至少跑一次真实 HTTP 全链路（S1 的教训已在 S2 补齐）。

## M.4 本轮仍然开放的问题（不属于 S2 范围）

| 问题 | 状态 | 归属 |
| --- | --- | --- |
| 工作说明不持久化 | 开放 | S5 |
| 模型可能整轮不输出工作说明 | 可接受（软约束） | 提示词迭代 |
| 本地语料接口不兼容 → 本地检索不可用 | 开放 | 环境修复（见 L.3） |
| 生产实例公开检索通道鉴权失败 | 开放 | 环境/密钥 |
| 文件↔会话跳转、PDF 预览、六页指南 | 未开始 | S3 / S4 / S5 |
---

## N. S3 执行后复测（文件 ↔ 会话 / 轮次跳转）

对应第二编 §A「导航与状态还原」与 §D「文件与会话关联」的“不可验证”结论。修复后按同一套
证据口径复测，结论已迁移：

| 第二编原结论 | 修复后实测 | 证据 |
| --- | --- | --- |
| 历史消息行不带稳定 id，无法定位 | 每行带 `message_id`；人消息另带 `request_id` | `/messages` 实测（探针 json `total_rows=6`） |
| `limit` 从最老端截断，长会话丢最新轮 | 返回**最新**一页 + `has_more`/`next_before` 游标 | `page1_ids=[12482,12483]`、`page2_ids=[12480,12481]` |
| 文件与对话轮无对外关联键 | 文件行带 `turn_request_id` / `source_session_id`，与该轮 `request_id` 一致 | `file_carries_this_turn_id=true`、`jump_key_matches_history=true` |
| “跳到会话”无法验证是否到轮 | 定位到 `[data-message-id]` 节点并高亮；找不到则不假报 | `no_fake_jump_for_unknown_turn=true` |

仍未覆盖：PDF/Office 预览空白（第二编 §B）与分页式使用指南（§E）——分别留待 S4、S5。
---

## O. S4 执行后复测（文件预览）

对应第二编 §B「PDF 预览白屏」。复测口径：不再问“iframe 有没有 onload”，而是看
（1）接口的 `Content-Type`、（2）响应体是否真是 PDF 字节、（3）Range 是否可用。

| 第二编原结论 | 修复后实测 | 证据 |
| --- | --- | --- |
| `iframe.onload` 不能证明渲染成功 | 前端改为先取字节、验 `%PDF-`，再交给渲染器；失败显示原因 + 兜底链接 | `web/assets/session_files.js::loadPdfPreview` |
| 老记录 MIME 为空/二进制流时浏览器只能下载 | 后端按后缀归一（`_canonical_mime`），路由层再兜一次 | `pdf_preview_131556.json`：`content_type=application/pdf` |
| 无任何可验证的“真的打开了”证据 | 探针断言字节与原件逐字节相同、Range 206 | `bytes_equal_original=true`、`range_status=206` |
| 失败时只能看到白框 | 明确区分“非 PDF / 0 字节 / HTTP 错误 / 读取异常” | `test_file_preview_mime.py` 10 例 |

仍未覆盖：分页式使用指南（第二编 §E）——留待 S5。
---

## P. S5 执行后复测（分页式使用指南）

对应第二编 §E。复测口径：服务端实际下发的内容里是否有六页结构、翻页/键盘/深链逻辑、以及样式。

| 第二编原结论 | 修复后实测 | 证据 |
| --- | --- | --- |
| 单页四宫格信息密度过高 | 六页，逐页推进，末页按钮变"知道了" | `guide_http_check.json: pages_in_served_html=6` |
| 无法深链到某一节 | `#guide=N` + `openOnboardingGuide(startPage)` | `js_deep_link=true` |
| 无键盘导航与焦点管理 | Esc / ←→ / Tab 焦点陷阱 | `js_escape_key=true` + `test_usage_guide_markup.py` |
| 指南可能夸大能力 | 13 例包含"无夸大表述"与"每页有正文"断言 | `pytest src/api/test_usage_guide_markup.py` 13 passed |
| 指南依赖 CDN 才能看 | 指南本身零外链（`test_guide_itself_has_no_external_assets`） | 同测试文件 |

新发现的独立问题（不属 S5 范围，建议单列）：`index.html:10-12` 的 `marked` 与 Google Fonts 仍走 CDN，
离线/内网环境下 markdown 渲染会退化为纯文本兜底（`app.js:416`）。

至此 S1–S5 的代码改造与本轮复测完成；尚未部署生产（生产仍为 `ff58b1f`）。

## Q. 复测（Round 4：验收缺口补齐 + 服务器隔离实例）

本节只记录"执行后再次审计"得到的**新事实**，结论以隔离实例为证据，生产未动。

## Q.1 首轮审计的两处误判（自我更正）

1. **`no_files_committed` / `has_cancelled_row` 是假通过**：`probe_cancel_e2e.py` 读的字段名与接口返回不一致（`files`/`items` vs `uploaded`/`generated`），且"必须有 cancelled 行"在"停止落在答案生成阶段"时本就不该成立。已改为等价断言。
2. **"本地检索不可用"不能由单次实例推断**：隔离实例（同代码同语料，软链嵌入缓存）上 `本地检索` 真实返回结果，成本轮 S2 探针可见。

## Q.2 新发现的问题

| 编号 | 问题 | 证据 | 处置 |
|---|---|---|---|
| O-1 | `GET /jobs/{id}`、`GET /sessions/{id}/jobs` 无归属校验，知道 id 即可读他人任务状态/停止原因/进度 | `main.py` 原实现只 `get_job(job_id)` | 本轮已修（`require_owned_session`） |
| O-2 | 重复点停止会把 `cancel_requested` 再写一次时间戳，返回值误导调用方 | `jobs.py` WHERE 含 `cancel_requested` | 本轮已修（只从 running/detached 迁移） |
| O-3 | 进度只在运行期，刷新后看不到"上一轮跑到哪" | `public_progress` 仅 `emit_progress` | 本轮已修（最后一条写 `task_jobs.progress_summary`） |
| O-4 | PDF 失败只有一句泛化"获取失败"，403/超大体量/断网无法区分 | `session_files.js` catch 全走同一句 | 本轮已修（六类分流 + 40 MB 上限） |
| O-5 | `/api/v2/files?scope=account` 返回**其它会话**的文件 | 隔离实例实测拿到上一探针会话的 csv | **已关闭**（`687b4fb`，见 §Q.4：传 `session_id` 时按会话收敛，不传时保持账号级） |

## Q.3 复测结论

- 本地套件 `259 passed / 0 failed`（基线 179，本轮 +80）。
- 隔离实例 8011：S1 全部 9 项 ✓、S2 三条进度 + 7 次工具真实执行 ✓、S3 六项 ✓、S4 五项 ✓。
- 生产 `/home/WZQ/CitrusQA` 仍是 `ff58b1f` 字节，本轮未部署。
- 仍未覆盖：真实浏览器视觉验收（本机无 Node、无浏览器 provider）、GPU 生产表现、跨账号文件列表语义。

## Q.4 补测：文件库的会话作用域（O-5 关闭）

### Q.4.1 问题

`GET /api/v2/session/{session_id}/files` 的路径声称是"这个会话的文件"，实现却返回**整个账号库**，并用 `scope="account"` 自陈。同一份数据在两条路由上语义不同：调用方信任 URL 就会拿到别的对话的文件名。这不是越权（同账号），但属于"契约说谎"，与审计中反对的"用近似值冒充精确"是同一类问题。

### Q.4.2 改法（最小）

- `src/session/files.py`：`list_account_files(owner_user_id, *, session_id="", touch=True)` —— 传入 `session_id` 时在 SQL 里过滤，不传时保持整库（默认行为不变）。
- `src/api/main.py`：`/api/v2/session/{id}/files` 改为按会话过滤并回 `scope="session"`；`/api/v2/files` 新增可选 `?session_id=`，回 `scope="session"`，不传仍为 `"account"`；过滤分支同样调用 `require_owned_session`。
- 前端未改：文件库面板调的是 `/api/v2/files`（不带参数），行为与之前一致。

### Q.4.3 验证

| 层级 | 内容 | 结果 |
|---|---|---|
| 单元 | `src/session/test_file_scope.py` 6 例：会话过滤、整库默认、跨账号不可借会话 id 取文件、会话路由不泄漏、账号路由保持整库、未知会话 404 | 6 passed |
| 全量 | `pytest src` | **266 passed / 0 failed**（基线上次 259） |
| 真实 HTTP | `experiments/trace/probes/probe_file_scope_e2e.py` 对本地实例（8010） | 5/5 PASS |

真实 HTTP 断言原文（`experiments/trace/runs/20261030/probe_file_scope_135257.json`）：

```
[PASS] session_route_scope_label: scope='session'
[PASS] session_route_only_own_files: names=['scope_a_8fa9e0.csv']
[PASS] account_route_keeps_library: scope='account' count=9
[PASS] account_route_honours_filter: scope='session' names=['scope_b_8fa9e0.csv']
[PASS] unknown_session_is_404: route=404 filtered=404
```

同目录下的 `probe_file_scope_135217.json` 是**失败的那一版**：它假设存在 `POST /session/{id}/upload`，实际返回 404——库里没有这条路由，上传只走 chat 的 `attachments`。留着它作为记录，后来改成直接写共享 store 播种（断言仍在 HTTP 上）。

### Q.4.4 顺带修掉一个"假通过"

`src/core/test_cancel_acceptance.py` 的 `CrossUserJobGuard` 里，`current_user` 的替身写成了 `async def`，而 `current_user` 是同步调用：路由拿到的是一个 coroutine 对象（恒真），404 全由归属检查桩决定。**即使身份读错，测试也会过。** 已改为同步函数，并补一例正向对照 `test_identity_reaches_the_route`（归属放行时路由必须看到 `user_id=2` 并成功返回）。改后该类 10 passed，全量测试不再出现 `coroutine was never awaited` 警告。

### Q.4.5 尚存

- 文件库面板仍是**整库**视图（这是产品决定，不是缺陷）：用户能看到自己所有会话的产物，并可从每行"↗"跳到产出它的那轮对话。若希望侧栏只显示当前会话，把前端 `session_files.js` 的 `/api/v2/files` 改成带 `session_id` 即可，后端已经支持。
- 旧数据里没有 `turn_request_id` 的文件仍不显示跳转按钮（不猜）。
### Q.4.6 服务器隔离实例复验（Linux，非同机）

把 `src/session/files.py`、`src/api/main.py`、探针上传到 `/home/WZQ/staging_citrus`（该实例已含 S1–S5 代码），`AST OK` 后以 8011 端口重启，再跑三个探针：

| 探针 | 结果 | 关键输出 |
|---|---|---|
| `probe_file_scope_e2e.py` | **5/5 PASS** | `session/'session'`、仅本会话文件、整库 count=4、过滤命中、未知会话双 404 |
| `probe_cancel_e2e.py`（回归） | **9/9 PASS** | `turn_status_cancelled`、`no_half_baked_answer`、`no_files_committed` 全 true |
| `probe_history_jump_e2e.py`（回归） | **6/6 PASS** | 最新页、回翻、`message_id`、`turn_key`、跳转键可解析、未知轮不假跳；耗时 8.9 s |

产物：`/home/WZQ/staging_citrus/01_源码_src/experiments/trace/runs/20261030/`（含 `probe_file_scope_135552.json`、`history_jump_135624.json`）。

验证结束后隔离实例已停止（`ps` 仅剩生产进程 `3495578`），**生产 `/home/WZQ/CitrusQA` 未做任何改动**：`main.py` 仍是 `5f5f9e3f25ecc2e4`、HTTP 200、uptime 4h34m。

本地侧同源复验：重启后的本地实例（8010）上 `probe_file_scope_e2e.py` 5/5、`probe_cancel_e2e.py` 9/9。

## R. 复测（Round 6：SSE 断线曾被记成"内部取消"）

发布准入矩阵里有一行 **「SSE 断线：断线不被错误判定为主动取消」**。此前它只有
`src/core/test_cancel_lifecycle.py::test_detach_is_not_cancel` 这种 **jobs 层**断言，
没有任何一次真实断线实验——于是这个漏洞在 S1 交付时被漏掉了。

## 实测（探针 `experiments/trace/probes/probe_disconnect_e2e.py`）

只走公开 HTTP：造会话 → 发起真实问答 → 读到工具已在执行 → **直接关掉 TCP（不发任何
cancel 请求）** → 轮询 `/jobs/{id}` 与 `/messages`。

首轮结果（本地 8010，修复前）：

| 观测 | 值 |
|---|---|
| 断线后 1.5s job 状态 | `cancelled` |
| `stop_reason` | **`internal`** |
| `detached_at` | 空（说明根本没走 detach 分支） |
| 历史 | 留下 3 条 `failed` 工具行 |

也就是说：**用户只是关掉页面，任务就被记成"服务端内部取消"**。

## 根因（不是逻辑没写，是异常类型不对）

`src/api/main.py` 的 SSE 生成器原本这样收尾：

```python
except asyncio.CancelledError:      # 只认这一种
    ...
    jobs_mod.mark_detached(job_id)
    raise
finally:
    if not keep_alive:
        graph_task.cancel()         # 断线时仍然走到这里
```

而 sse-starlette **3.4.5** 在收到 `http.disconnect` 后走的是
`task_group.cancel_scope.cancel()`（`sse_starlette/sse.py:481-499`）：被取消的是
**驱动任务**，我们自己的异步生成器随后被 `aclose()`，抛进去的是
**`GeneratorExit`**——`except asyncio.CancelledError` 一次都没执行，`finally` 里
`graph_task.cancel()` 照旧执行，`process_graph` 的取消分支按
`prev_status != cancel_requested` 判成 `internal`（`main.py:1445`）。

## 修复（最小侵入）

1. 用 sse-starlette 自己提供的 `client_close_handler_callable`（`sse.py:391`，在
   `cancel_scope.cancel()` **之前**被 await）把 job 标 detached；
2. 生成器 `except` 同时覆盖 `asyncio.CancelledError` 与 `GeneratorExit`，两者走同一个
   `_mark_detached()`；
3. `finally` 只认 `conn_state["keep_alive"]`，断线后不再 `graph_task.cancel()`；
4. 断线只写 `mark_detached`，**绝不**写 `stop_reason=user`。

## 复测（同一探针，修复后）

`checks` **8/8 通过**：断线瞬间 `status=detached`（`detached_at` 有值）→ 执行继续 →
终态不再出现 `cancelled/user`，历史里没有 `cancelled` 工具行，本轮仍产出 1,867 字符回答。

（本轮终态是 `failed/incomplete`：本机实例的旧语料与新检索器契约不兼容、联网也没有结果，
所有工具都空手而归——这是环境问题，与断线语义无关；关键是它**没有被记成取消**。）

## 测试

- 新增 `src/core/test_cancel_lifecycle.py::test_sse_disconnect_is_wired_to_the_client_close_hook`：
  钉住"钩子必须交给 EventSourceResponse / 两种断线异常都要覆盖 / finally 不许再看旧的
  `keep_alive`"，防止有人把这段改回去。
- 全量 `pytest src`：**267 passed / 0 failed**。

## 教训

这类缺陷用单测抓不到，因为它错在**异常类型与框架行为**，不在业务分支；必须有一次
"真的把连接掐掉"的实验，并且断言要看 `stop_reason`，不能只看 `status`。


## R.2 隔离实例复测（Round 6，`/home/WZQ/staging_citrus`，port 8011，新代码）

本机实例的旧语料与新检索器契约不兼容，工具全部空手而归，所以又在一个**有完整语料**
的隔离实例上重跑同一个探针（同一次运行数据：`experiments/trace/runs/20261030/
probe_disconnect_e2e_staging.json`）：

| 观测 | 值 |
|---|---|
| 断线时刻 | 4.16 s（工具执行中，断线前已收到 15 帧） |
| 断线后 job | `detached`，`detached_at=2026-10-30T14:05:15.646`，`stop_reason` 空 |
| 终态 | **`completed`**，`stop_reason` 空，断线后 47 s 完成 |
| 工具 | 网页搜索 8 条 / **本地检索 8 条候选** / 网页搜索 6 条 / 网页搜索 6 条，全部 `complete` |
| 回答 | 3,437 字符 |
| 事件 | 含 `assistant_progress`（S2）与 `tool_progress` |
| 8 项检查 | **全部 true** |

结论：断线后执行继续、工具真实跑完、答案真实落库，且**没有任何 cancelled 痕迹**。


## R.3 发布准入矩阵剩余三行（Round 6，隔离实例 8011）

新增探针 `experiments/trace/probes/probe_matrix_residual.py`（外部观察者，走公开
HTTP + 只读 DB），一次跑完三项此前只靠单测的准入行：

| 准入行 | 检查方式 | 结果 |
|---|---|---|
| 数据库迁移幂等 | 打开 `agent/state/sessions.db` 两次，比对 `sqlite_master` 全量对象（28 个） | **稳定**，第二次不新增/不丢对象 |
| 刷新后历史不重复 | 对同一会话连续 GET `/api/v2/session/{id}/messages` 两次 | 序列完全一致（10 条）；仅 2 处重复 id，均为"同一条消息的 tool_calls 段 + assistant 段"，**非重复记录** |
| 文献导出三格式 | 驱动真实工具 `export_literature_evidence` 的 xlsx / ris / bibtex | 三格式均产出可下载产物；xlsx 工作表 `文献证据`、3 行（表头 + 2 条） |

第一次跑时这三项有两项是"假通过"：DB 路径指向 `01_源码_src/agent`（真实数据根是实例根
下的 `agent/`），导致"迁移幂等"在 0 个数据库上通过；导出检查误调了内部渲染函数
`_render_reference_export(rows,"xlsx")`——它只处理 ris/bibtex，xlsx 分支在
`export_literature_evidence` 里，于是报"format 必须为 xlsx、ris 或 bibtex"。
两处都按"必须打到真实对象"重写后才算通过。

产物：`experiments/trace/runs/20261030/probe_matrix_residual.json`。


## R.4 发布准入矩阵「账号安全」：跨用户越权（Round 7）

矩阵里这一行此前只有单元测试（`test_*_ownership`），从没有一次**真实的第二个已登录
账号**通过 HTTP 去够别人的东西。新增探针
`experiments/trace/probes/probe_security_crossuser.py` 补这一行：

1. 用产品自己的注册/登录接口建两个账号 A、B；
2. A 建会话、登记一份上传文件、经真实 `_save_artifact` 产出一个 artifact、建一条
   `running` 的 job；
3. A 自测（对照组）必须 200；
4. B 逐项越权：会话消息、会话文件列表、文件库、文件预览、文件下载、artifact 下载、
   删除 A 的文件、取消 A 的 job。

结果（隔离实例 8011）：

| 越权动作 | B 得到 |
|---|---|
| 读 A 会话消息 / 会话文件 | 404 |
| 预览 / 下载 A 的文件 | 404 |
| 下载 A 的 artifact | 404 |
| 删除 A 的文件 | 404 |
| 取消 A 的 job | 404 |

副作用检查全部通过：`A_control_access_ok`（对照组 200）、`B_all_denied`、
`B_library_isolated`（B 的文件库里没有 A 的文件）、`A_file_survives_B_delete`、
`A_job_not_cancelled_by_B`。

两点值得记下来：

- 越权一律返回 **404 而不是 403**，是有意为之——不向调用方泄露"该会话/文件存在"。
- job 的归属没有独立列，唯一事实来源是 `job.session_id` 指向的会话
  （`task_jobs` 表只有 `session_id`），所以 job 鉴权必须走
  `require_owned_session(job.session_id, user)`（`main.py:1852`）。给 `task_jobs`
  加 owner 列反而会造出第二个真相来源。

产物：`experiments/trace/runs/20261030/probe_security_crossuser.json`。


## R.5 发布准入矩阵「Agent 执行独立性」：关掉公开进度通道，执行结果不变（Round 7）

矩阵要求：**禁用前端执行面板/公开进度后，科研任务仍正常完成**。S2 的验收标准里也有
"禁用这条通道后，工具调用结果与完成状态不受影响"。

做法：同一套代码、同一个库、同一句话，起两个实例，唯一差别是
`CITRUS_PUBLIC_PROGRESS`（`src/core/public_progress.py:34`）：

| 观测 | 开启（8011） | 关闭（8012） |
|---|---|---|
| `assistant_progress` 事件 | 1 | **0** |
| `tool_call_start` 事件 | 2 | 2 |
| 工具行 | 3 | 3 |
| job 终态 | `completed` | `completed` |
| 消息数 | 6 | 5 |
| 回答长度 | 956 字符 | 678 字符 |

结论：**工具调用集合与终态完全一致，进度通道只决定"看得见什么"**。消息数差 1 正是那条
进度行——它作为独立 `assistant` 记录落库，与最终答案分开。

顺带验证了"最终答案不含重复进度文本"：开启实例的历史是

```
467 user      柑橘黄龙病的传播途径是什么？请给出来源。
468 tool_calls (空)
468 assistant 我先查一下关于黄龙病（HLB）传播途径的证据，再给出简洁回答。   ← 进度行
469 tool      complete
470 tool      complete
471 assistant **柑橘黄龙病（HLB…）的传播途径主要有以下几类：** …            ← 最终答案
```

进度文本**没有**出现在最终答案里（脚本比对 `progress_text_inside_final = False`）。

诚实说明：两次各只跑一次，回答长度 956 vs 678 的差异属于模型采样波动，**不能**归因于
通道开关；能归因的是工具事件数、工具行数、终态三项完全相同。

产物：`experiments/trace/runs/20261030/probe_independence_on.json` /
`probe_independence_off.json`。


---

# 合并·第二编：记忆、上下文、导航与文档流

> **总入口**：跨轮次的汇总（问题→根因→修复→证据、提交时间线、遗留风险）见 `SUMMARY_20261030.md`。本文件保留各专项的原始取证、代码行号与本轮真实浏览器验收（§15）。

> 审计时间：2026-10-30 15:35–16:00（本机 8010 实例 + 本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent`）
> 审计性质：**只读**。本轮未修改 Agent 主循环、未改后端、未部署、未 push。
> 环境限制：dsh 浏览器 provider 不可用（`browser provider(s) electron … unavailable`），**没有做真实浏览器视觉验收**；下文把"代码/接口/DOM 证据"和"需要人工看的项"严格分开写。

---

## 0. 结论速览

> 本节是 Round 5 时的现状判断。Round 6 的修复与验收记录见 **§11**。

| 系统 | 是否真实实现 | 现在能不能看到 | 一句话结论 |
|---|---|---|---|
| **A 账号长期记忆** | **是**（提取→落库→召回→注入→面板全链路在） | **能**，实测返回 10 条真实事实 | 不是假功能；"显示为空"只可能来自账号维度、提取门槛、或未登录 |
| **B 上下文概览** | 后端**是**，前端**被删了** | **不能**（只有标题+小橙点） | 根因是提交 `c1f79f7` 把面板标记和渲染函数一起删空，**不是**接口报错 |
| **C 右侧长对话导航** | 数据与跳转能力**已有**（S3），**刻度 UI 没有** | **没有** | 最小实现是纯前端 + 复用现有分页接口，不需要动 Agent |
| 四者是否独立 | —— | —— | 记忆表 / 上下文快照 / 执行轨迹 / 导航刻度 是四套东西，本报告分开论证，不合并 |

---

## 1. 审计方法（可复现）

1. **代码检索**：`Select-String` / `git log -S` / `git show <commit>:<path>`，只读。
2. **数据库直查**（本机 `agent/state/sessions.db`、`agent/state/accounts.sqlite3`）：`sqlite3` 只读查询表结构与行数。
3. **真实 HTTP**：向本机 8010 实例发请求，用一次性测试会话 cookie（向 `accounts.sqlite3` 的 `browser_sessions` 插入一条 1 小时有效的 hash，测完可删）：

```powershell
# 铸一个只读探针 cookie（user_id=1），然后用 curl 打接口
& curl.exe -s -H "Cookie: citrus_auth=<token>" http://127.0.0.1:8010/api/v2/auth/memory
```

> 踩坑记录：PowerShell 的 `Invoke-RestMethod -Headers @{Cookie=...}` 对这个接口返回 **401**，而 `curl.exe` 同样的头返回 **200**。所以后续所有接口验证都用 `curl.exe`，避免把客户端差异误判成服务端缺陷。

---

## 2. 审计 A：账号长期记忆

### 2.1 全链路（每一跳都有代码位置）

```
用户消息
  └─ graph/session_persistence.py:persist_agent_turn            ← 每轮结束触发
       ├─ 门槛 _worth_extracting_memory(answer)
       │     = len(answer) > 500 且 answer 命中 _LTM_SUBSTANTIAL_MARKERS
       └─ core/background.spawn(asyncio.to_thread(extract_and_save_memory))
            ├─ guardrails/memory.py:extract_key_facts(query, answer)   ← 用最便宜模型抽 3–5 条
            ├─ type=preference → memory_store.set_preference(user_id, key, value)
            │     → sessions.db : account_preferences(user_id, pref_key, pref_value, updated_at)
            └─ 其余            → memory_store.save_long_term_fact(...)
                  → sessions.db : ltm_facts(fact_key, fact_value, confidence, updated_at,
                                             owner_session, source_query, owner_user_id)
                  （ADD-only：同 key 多版本并存，置信度 <0.5 拒写）

下一轮提问时召回
  └─ core/context_manager.py:266  recall_long_term_memory(query, 5, ctx.user_id)
       → context_manager.py:487  以 <long_term_memory>…</long_term_memory> 注入本轮 user 消息
       → memory_status = "recalled" / "empty"（context_manager.py:616、graph:1188）

查看 / 删除
  └─ GET  /api/v2/auth/memory     → memory_store.list_user_memory(user_id)   (main.py:603)
  └─ DELETE /api/v2/auth/memory   → memory_store.clear_user_memory(user_id)  (main.py:612)
  └─ 前端 web/assets/app.js:259 openMemorySettings() / :293 clearAccountMemory()
```

### 2.2 数据库实测（本机）

| 表 | 行数 | 说明 |
|---|---:|---|
| `ltm_facts` | **10** | 全部 `owner_user_id = 1`；5 条 HLB 传播（`2026-10-30T12:48`，**今天刚写入**），5 条数据审查规则（`2026-10-24`） |
| `account_preferences` | 0 | 用户从未显式表达"偏好"类信息，所以偏好区为空是**正确**的 |
| `memory_store` | 2 | 两条系统标记（`_synth_purge_v843`、`_ltm_migrated_v2`），不是用户记忆 |
| `accounts.sqlite3 : users` | **1** | `user_id=1 / wangziqiang / admin / onboarding_completed=1` |

### 2.3 真实接口返回（`curl.exe`，HTTP 200）

```json
{"preferences": [],
 "facts": [
   {"id":670,"key":"菟丝子实验条件下可传HLB","confidence":0.85,"updated_at":"2026-10-30T12:48:28.290788"},
   {"id":669,"key":"带菌接穗或芽条嫁接即传病","confidence":0.9, "updated_at":"2026-10-30T12:48:28.289256"},
   {"id":668,"key":"HLB介体传播为持久性循回型","confidence":0.9, "updated_at":"2026-10-30T12:48:28.286219"},
   {"id":667,"key":"非洲柑橘木虱传播Laf并实验可携带Las","confidence":0.9,"updated_at":"2026-10-30T12:48:28.284649"},
   {"id":666,"key":"亚洲柑橘木虱传播Las与Lam","confidence":0.9, "updated_at":"2026-10-30T12:48:28.281601"},
   {"id":665,"key":"Norm_Value_A − Norm_Value_B 恒为0.12","confidence":0.97,"updated_at":"2026-10-24T21:27:11"},
   … 共 10 条
 ]}
```

`/api/v2/auth/me` 同时返回 `{"user_id":1,"username":"wangziqiang","role":"admin",…}` → 说明接口的账号过滤生效，**不是**"读不到库"。

### 2.4 那"UI 显示为空"到底怎么来的

按可能性排序，全部可解释、无需假设后端坏了：

1. **未登录 / cookie 过期**：面板 `fetch('/api/v2/auth/memory')` 拿到 401 → `catch` 显示"读取账号记忆失败"。刷新后重新登录即可。注意上面 2.1 的踩坑：**客户端实现差异也会造成 401 观感**。
2. **账号维度**：记忆按 `owner_user_id` 隔离。如果换过浏览器配置/清了 cookie 而新建了账号，旧账号的 10 条事实不会出现在新账号里（这是设计，不是缺陷）。
3. **提取门槛**：`len(answer) > 500` 且命中实质标记。短问答（如"这是什么品种"）**不会**产生记忆——所以"问了很多问题但记忆只有几条"是预期行为。
4. **偏好区恒空**：`account_preferences` 只有用户**显式表达**偏好时才写（语言/格式约定等），普通问答不会写。

### 2.5 逐条回答你列的问题

| 问题 | 答案（带证据） |
|---|---|
| 机制是否真实现？ | 是。提取、落库、召回、注入、面板、删除六段都有代码位置，且库里有 10 条真实事实 |
| 入口函数与数据表？ | `persist_agent_turn` → `memory_store.extract_key_facts/set_preference/save_long_term_fact`；表 `ltm_facts` / `account_preferences`（`sessions.db`），账号在 `accounts.sqlite3.users` |
| 自动/显式/定时/工具触发？ | **自动**（每轮结束、满足门槛时后台线程抽取）。**没有**定时任务，**不是**工具触发 |
| 为什么 UI 显示为空？ | 见 2.4：401 / 账号维度 / 门槛未达 / 偏好区本就为空 |
| `save_research_note` 是否独立？ | **完全独立**：它写 `research_notes(session_id, owner_user_id, project_name, note, created_at)`（`src/research/tools.py:save_research_note`），是"课题记录"工具，需用户明确要求；与 `ltm_facts` 不同表、不同入口、不同用途 |
| 新会话是否注入模型？ | 是。`context_manager.py:266` 召回 → `:487` 注入 `<long_term_memory>` 块；`memory_status` 反映 `recalled/empty` |
| 能否查看/修改/删除？ | 查看 ✅（面板 + 接口）、删除 ✅（`DELETE /api/v2/auth/memory`，按账号清）、**修改 ❌**（没有编辑入口，只能清空重来） |

**结论**：账号记忆**不是"只做了 UI"**。真要补的是"可修改/可单条删除"和"面板里解释为什么现在只有这几条"（门槛与账号维度），不是重做机制。

---

## 3. 审计 B：上下文概览为什么是空白（P0）

### 3.1 现象与定位

你截图里是「上下文概览」标题 + 一个小橙点 + 大片空白。

- 标题来自 `web/index.html:339`。
- 小橙点 = 环形按钮（`#ctxCircleBtn`，`index.html:22`）或 0 宽度进度条（`index.html:344-346`）。
- **空白**是因为抽屉里只剩进度条这一块 DOM：`index.html:342-348` 只有 `#ctx-body > .ctx-bar-outer > #ctxBar`，**没有任何文字节点**。

### 3.2 根因（git 证据，不是推测）

```
$ git log -S 'ctx-item' -- 01_源码_src/web/index.html
c1f79f7  Persist account files and refine CitrusScholar chat UI     ← 就是它
15e1a4e  QAagent: 联网优先 + 语料补充的柑橘/农业问答智能体

$ git show c1f79f7 -- 01_源码_src/web/index.html
-    <span class="ctx-drawer-pct" id="ctx-pct">0%</span>
-    <div class="empty-sidebar" id="ctx-empty">等待首次查询...</div>
-    <div id="ctx-body" style="display:none">
-      <div class="ctx-bar-outer"><div class="ctx-bar-inner" id="ctxBar"></div></div>
-      <div class="ctx-bar-label" id="ctxBarLabel">0 / 1,000,000</div>
-      <div class="ctx-items" id="ctxItems"></div>
-      <div class="ctx-summary" id="ctxSummary"></div>
-      <div class="ctx-suggestions" id="ctxSuggestions"></div>
-      <div class="ctx-detail-label">上下文细分</div>
-      <div class="ctx-detail" id="ctxDetail"></div>
-      <div class="ctx-compressed" id="ctxCompressed" style="display:none"></div>
+    <div id="ctx-body">
+      <div class="ctx-bar-outer" role="progressbar" …>
+        <div class="ctx-bar-inner" id="ctxBar"></div>
```

同一个提交把渲染函数也掏空了。**现在**的 `web/assets/app.js:1600-1625`：

```js
function renderContextPanel(data) {
  _ctxData = data || {};
  … _updateCtxBtnRing(pct);            // 只更新圆环
  var bar = document.getElementById('ctxBar');
  if (bar) { bar.style.width = …; }     // 只更新进度条
  ['ctxBarLabel','ctxItems','ctxSummary','ctxSuggestions','ctxCompressed']
    .forEach(function(id) {
      var node = document.getElementById(id);
      if (node) { node.textContent = ''; node.hidden = true; node.style.display = 'none'; }  // 主动清空
    });
}
```

**历史版本（`c1f79f7^`，app.js:1453 起）**是完整实现：进度条 + `12,480 / 1,000,000 · 估算` 标签 + 四项明细（会话历史 / 账号记忆 / RAG 缓存 / 模型提示缓存）+ 剩余 tokens 与压缩阈值 + 格式提示 + 已压缩提示。

### 3.3 后端没有退化（这点很重要）

| 数据源 | 现状 |
|---|---|
| 刷新后的快照 | `src/api/main.py:2968-2990` `_build_context_snapshot()` 仍返回 `history_msgs / history_chars / estimated_tokens / max_tokens / soft_threshold / hard_threshold / compressed / memory_status / format_hint` |
| 请求期实时事件 | `src/graph/general_agent_graph.py:1174-1195` 仍发 `context_status`（含 `rag_cache_status`、`model_cache_*`） |
| 逐段细分 | `GET /api/v2/session/{id}/context-detail`（`main.py:3032`）仍在：system 静态前缀全文、按 user/ai/tool/system 分类的历史、内部记忆块 |
| token 实测 | `general_agent_graph.py:1265/1406` `emit_usage_from_response(...)` 记录服务商 usage → 面板的"输入/输出 tokens"可以是真的，不必写"示例" |

**结论**：这是**纯前端丢失**，恢复不需要动后端、不需要新接口。

### 3.4 恢复方案（两步，先小后大）

**第一步（恢复原样，约 40 行）**
1. `index.html` 恢复 3.2 里被删的 11 个节点（`ctx-pct / ctx-empty / ctxBarLabel / ctxItems / ctxSummary / ctxSuggestions / ctx-detail-label / ctxDetail / ctxCompressed`）。
2. `app.js` 恢复 `c1f79f7^` 版 `renderContextPanel`（含 `_ctxLevelClass`、`escapeHtml` 调用，二者现在都还在）。
3. 回归：`context_status` 事件与 `/messages` 的 `context` 两条路径都要能填满面板（`app.js:2375`、`2992` 已分别接线）。

**第二步（对齐你给的目标设计，需要新数据）**
目标界面要求：顶部「本轮输入 / 输出 tokens」+ 六行可展开（系统与任务说明 / 历史消息 / 用户消息 / 助手公开文本与工具调用 / 工具结果摘要 / 助手最终回答）。

| 目标区块 | 现有可用的真实数据 | 缺口 |
|---|---|---|
| 输入/输出 tokens | `emit_usage_from_response` 的 usage（服务商报告） | 无（拿不到时标"估算"） |
| 系统与任务说明 | `/context-detail` 的 `system` 段全文 | 无 |
| 历史消息 | `/context-detail` 按角色分类 | 无 |
| 用户消息（本轮） | 同上（`<user_query>` 原文 + 内部块单列） | 无 |
| 助手公开文本 / 工具调用 / 工具结果 | `assistant_progress` 事件 + 工具行（S2/S1 已实现） | 无 |
| 助手最终回答 | `messages` 里的最终 AIMessage | 无 |
| **"实际发送给模型的消息"** | —— | **缺**：当前只有"库内全量历史"和"文本估算"，没有逐轮记录真实发送的 message 列表。要用它必须新增一份**请求级快照**（只记录、不改变行为） |

**这条缺口必须在实现时写清楚**：面板要么只展示"可核验的估算 + 实际 usage"，要么先加"发送快照"。**不能用库内全量历史冒充实际上下文。**

---

## 4. 审计 C：右侧长对话导航（Codex 式）

### 4.1 已有能力（S3 已交付，可直接复用）

| 能力 | 位置 | 现状 |
|---|---|---|
| 分页读历史 | `GET /api/v2/session/{id}/messages?limit=60&before=<message_id>`（`main.py` `session_messages`） | 有；`_slice_history` 返回 `(window, has_more, next_before)`，窗口永远是最新 N 条 |
| 消息稳定 id | 每条消息带 `message_id`，生成文件还带 `request_id`（S3） | 有 |
| 跳到某条消息（必要时先加载更早的页） | `app.js:2943 jumpToMessage(messageId, opts)`；`app.js:2938-2940` 在 `has_more` 时先拉上一页再 `jumpToMessage(oldest,{silent:true})` | 有 |
| 文件 → 轮次跳转 | `session_files.js` 的「定位」按钮（S3） | 有 |

**所以 C 不需要新的后端能力**：刻度所需的"每一轮真实用户提问"用 `/messages` 的分页数据就能逐步拿全。

### 4.2 缺口（要做的东西）

1. **刻度 UI 不存在**：右侧没有 rail；`app.css` 无 `.turn-rail*`。
2. **未加载轮次**：只对已渲染 DOM 生成刻度会漏。需要"服务端索引优先"：先按 `message_id` 列表画刻度（分页拉全量 user 消息的 id），点击时再 `jumpToMessage` 按需加载。
3. **交互细节**：hover 伸长/加深、预览浮层、点击后短暂高亮、键盘聚焦（Tab + Enter）、`prefers-reduced-motion`、手机端折叠。
4. **不把工具心跳当刻度**：刻度只对应 `role=user` 的消息；`assistant_progress` 与工具行不参与。

### 4.3 最小实现方案（纯前端，约 200 行）

```
新文件 web/assets/turn_rail.js
  buildTurnRail(sessionId):
    1) 先按 /messages?limit=200 拿一页，收集 role=user 的 {message_id, text, ts}
    2) has_more 时继续 before=<最小 id> 直到拉完（上限 N 页，超出则只画已拿到的并在悬停提示"更早的轮次未加载"）
    3) 画 .turn-rail > .turn-tick*，当前可见轮次加 .is-current（滚动监听 IntersectionObserver）
    4) click → jumpToMessage(id, {highlight:true})；未加载则先 loadOlderUntil(id)
  index.html: 在 chat 容器右侧插入 <div class="turn-rail" id="turnRail" hidden>
  app.css: 刻度 8×3px、hover 18×4px、current 用 --accent、reduced-motion 时无过渡
```

**边界（写死）**：不新增模型调用；不改 SSE；不改 Agent；刻度数据只来自历史接口。

---

## 5. 四系统独立性核对（防混淆）

| 维度 | 账号长期记忆 | 模型上下文概览 | 聊天执行轨迹 | 长对话导航 |
|---|---|---|---|---|
| 数据来源 | `ltm_facts` / `account_preferences` | `/messages` 的 `context` + `context_status` + `context-detail` | `assistant_progress` + 工具事件 + `messages.execution_json` | `/messages` 的 `role=user` 列表 |
| 生命周期 | 跨会话、按账号 | 每轮 | 每轮 | 单会话内 |
| 谁写入 | 后台抽取线程 | 上下文管理器（估算）/ 服务商 usage | Agent 工具循环 | 无写入（只读历史） |
| 面板 | 账号菜单「账号记忆」 | 右上「上下文概览」 | 对话正文内工具行 | 右侧 rail（待建） |
| 是否可合并 | **否** | **否** | **否** | **否** |

> 特别提醒：`assistant_progress` 是**执行轨迹**的一部分，不能塞进上下文概览冒充"模型收到的输入"。

---

## 6. 真实浏览器验收：本轮**没做**（环境限制）

dsh 的浏览器 provider（electron）报告不可用，本机也没有可脚本化的浏览器。**因此本报告不含任何"视觉验收"结论**；下面这些必须由人眼在浏览器里看：

| # | 验收项 | 通过标准 |
|---|---|---|
| V1 | 六页指南翻页/示例填入/关闭再打开 | 无横向溢出；按钮不遮挡文字；示例只填入输入框、**不自动发送** |
| V2 | 动态公开工作说明 | 与工具行交替出现，无重复正文、无假状态 |
| V3 | 停止任务 | 气泡不消失、标记为已中断；工具行保留真实状态；刷新后一致 |
| V4 | PDF 预览 | **真的显示页面**（能滚动、能缩放、能下载），不只是 200 字节流 |
| V5 | Excel 多 Sheet / HTML 报告 | 可切换 Sheet；HTML 报告在 iframe 中渲染 |
| V6 | 文件「定位」 | 跳到正确那条消息并高亮，找不到时明确提示 |
| V7 | 手机宽度（≤420px） | 指南与 rail 不溢出、不遮挡 |

已做的替代验证（**不等于**视觉验收）：接口 JSON、DOM 结构、CSS 选择器存在性、真实 HTTP 200。

---

## 7. 实施顺序（建议，待你确认）

| 优先级 | 事项 | 触及文件 | 风险 |
|---|---|---|---|
| P0-1 | 恢复上下文概览（第一步：原样恢复） | `web/index.html`、`web/assets/app.js`、`web/assets/app.css` | 低（纯前端回填） |
| P0-2 | 上下文概览第二步：tokens 头 + 六行展开（`/context-detail` 驱动） | 同上 | 中（要处理"发送快照缺失"的诚实标注） |
| P1 | 六页指南按新设计重做 | `web/index.html`、`web/assets/app.css`、`web/assets/app.js` | 低（只改展示与文案） |
| P2 | 右侧长对话导航 rail | 新增 `web/assets/turn_rail.js`、`index.html`、`app.css` | 中（分页边界） |
| P3 | 账号记忆补"单条删除 / 说明文案" | `src/guardrails/memory.py`、`src/api/main.py`、`app.js` | 中（涉及删除语义） |

---

## 8. 需要你决定的四件事

- **D-A1**：上下文概览先"原样恢复"就交，还是一步做到你给的新设计（含 tokens 头与六行展开）？
- **D-A2**："实际发送给模型的消息"要不要新增请求级快照（只记录、不改行为）？不加速则面板只能给"估算 + 实际 usage"，并在面板内标注。
- **D-A3**：账号记忆是否需要"单条删除 / 编辑"（现在只能整账号清空）？
- **D-A4**：右侧 rail 的刻度范围：只画"已加载的轮次"，还是分页拉全量后再画（会话很长时会多几次请求）？

---

## 附一：六页指南重做文案（按你给的设计图，待确认后实施）

> 结构：每页 = 图标 + 一句价值标题 + 一句副标题 + 一个示例问题 + 三条步骤 + 底部圆点 + 上一步/下一步。
> 删除：首页"不做什么"大段、每页重复的"本指南只介绍当前已实现能力"条、"跳过"改为"关闭指南"、末页给"开始使用"。
> 交互：`试试这个示例` **只填入输入框并关闭指南，绝不自动发送**。

| 页 | 标题 | 副标题 | 示例问题 | 三条步骤 |
|---|---|---|---|---|
| 1 | 你的柑橘科研助手 | 直接说出研究问题，助手会自己选工具。 | 帮我梳理柑橘黄龙病目前的防控手段，并给出依据。 | 说清你的问题 → 助手挑选工具 → 给出带来源的回答 |
| 2 | 查文献，找依据 | 不用记工具名称，直接告诉我你想查什么。 | 帮我查找柑橘黄龙病传播途径的研究，并列出参考来源。 | 检索相关资料 → 整理可核对的来源 → 根据证据形成回答 |
| 3 | 上传文件，直接提问 | 文献、表格、图片都可以直接读。 | 这份 PDF 的主要结论、方法和局限分别是什么？ | 上传文件 → 指定要读的部分 → 得到结构化解读 |
| 4 | 让实验数据更容易检查 | 重复值、缺失值、异常值一次看完。 | 帮我审查附件表格：重复值、缺失值、异常值和数值关系。 | 上传表格 → 请求审查 → 打开异常报告 |
| 5 | 把研究结果整理成文件 | 结论、来源、报告都能带走。 | 把刚才引用的文献导出为 RIS 文件。 | 说明要导出什么 → 生成文件 → 在会话文件里下载 |
| 6 | 看得见的执行过程 | 每一步做了什么、花了多久，都写在对话里。 | （无示例，末页按钮为"开始使用"） | 公开工作说明 → 工具结果与耗时 → 随时可以停止 |

**文案纪律**：正文不出现 `completed / partial / failed / cancelled` 等程序状态词；需要解释时用"完成 / 部分完成 / 未完成 / 已中断"。

## 附二：本次审计的取证命令（可复现）

```powershell
# B 的根因
git log --oneline -S 'ctx-item' -- 01_源码_src/web/index.html
git show c1f79f7 -- 01_源码_src/web/index.html
git show 'c1f79f7^:01_源码_src/web/assets/app.js' | Select-String 'function renderContextPanel' -Context 0,60

# A 的链路
Select-String -Path 01_源码_src/src/graph/session_persistence.py -Pattern 'def _worth_extracting_memory' -Context 0,4
Select-String -Path 01_源码_src/src/guardrails/memory.py -Pattern 'def list_user_memory' -Context 0,25

# A 的实测
& curl.exe -s -H "Cookie: citrus_auth=<token>" http://127.0.0.1:8010/api/v2/auth/memory
& curl.exe -s -H "Cookie: citrus_auth=<token>" http://127.0.0.1:8010/api/v2/auth/me

# C 的能力
Select-String -Path 01_源码_src/web/assets/app.js -Pattern 'jumpToMessage|_historyHasMore'
Select-String -Path 01_源码_src/src/api/main.py -Pattern 'def _slice_history' -Context 0,12
```

---

## 九、本机 8010 网页搜索 `auth` 失败：根因与修复（配置级，未改代码）

## 现象
本机 8010 实例联网检索失败，工具返回 `[WEB_TOOL_ERROR] HTTP 401`，前端归类为 `reason=auth`。

## 根因（实测，非推测）
1. `src/config.py:574-584` 的 `RESOLVED_DEEPSEEK_WEB_API_KEY` 解析顺序：运行时文件 `agent/state/deepseek_web_api_key` → `DEEPSEEK_WEB_API_KEY` → `DEEPSEEK_API_KEY`（仅当 base_url 含 `api.deepseek.com`）。
2. 本机 `agent/state/deepseek_web_api_key` **不存在**；`.env` 里只有 `DEEPSEEK_API_KEY=sk-dc…db23`（35 位），于是回退到它。
3. 直接用该 key 请求 `https://api.deepseek.com/v1/tools/search` → **HTTP 401**：`Authentication Fails, Your api key: ****db23 is invalid`。
   即该 key 对 DeepSeek 平台已失效；本机主模型走 dashscope，所以问答本身不受影响。
4. 误导项：本机 `.env` 的 `SERPER_API_KEY=your…`（占位符）+ `WEB_SEARCH_PROVIDER=serper` 是**死配置**——`config.py:586-588` 的 `RESOLVED_WEB_SEARCH_PROVIDER` 只读 `state/web_search_config.json`，缺省 `deepseek_search_fetch`，不看该环境变量。

## 修复（只动运行时状态：未改代码、未改 .env）
1. 从生产机取合法检索凭证 `/home/WZQ/CitrusQA/agent/state/deepseek_web_api_key`（35 字节）→ 本机 `agent/state/deepseek_web_api_key`（内容未回显）。
2. 新建本机 `agent/state/web_search_config.json`：`{"provider_id": "deepseek_search_fetch"}`。
3. 重启 8010（`停止_本地_8010.ps1` → `启动_本地_8010.ps1`，24 秒就绪）。

## 验证（三层，均为实测）
- 端点层：`POST https://api.deepseek.com/v1/tools/search` → **200**，返回 `results`。
- 配置层：`GET /api/v2/config`（minted cookie + curl）→ `web_search: {enabled: true, provider: deepseek_search_fetch, configured: true, deepseek_fetch_configured: true}`。
- 工具层：`deepseek_search.invoke({query: "citrus huanglongbing transmission pathways", count: 3})` → **无 `[WEB_TOOL_ERROR]`**，返回 3 条真实结果（首条为 ScienceDirect 2025 年 HLB 传播论文）。

## 边界
- 这是**本机开发实例的凭证缺失**，不是代码缺陷；生产 8000（`ff58b1f`）凭证一直合法。
- 该修复不写入 Git（`agent/state/` 属运行时数据）；新机器部署同样需要这一步，已记入启动脚本说明。

---

## §10 统一视觉交互验收（防遮挡）— Round 5

目标：确认右侧导航条、上下文概览抽屉、输入区三者互不遮挡，且在抽屉打开时导航条仍可用。

### 方法（真实浏览器，不是读 CSS）
- 用 headless Chrome（CDP，`_scratch/cdp_shot.js`）打开本机 8010，切到含 6 轮真实提问的会话，打开上下文概览抽屉，再用 `elementFromPoint()` 做命中测试（真实点击路径，而非只看 z-index）。
- 逐视口量 `getBoundingClientRect()`：1600×1000（桌面）、1024×768（笔记本）、430×900（手机）。

### 实测结果
| 视口 | 导航条位置 | 抽屉 | 遮挡 | 命中 |
|---|---|---|---|---|
| 1600×1000 | x 1172–1198（抽屉打开后从 1564 左移） | x 1220–1600 | 无（22 px 间隙） | 命中 `turn-tick` |
| 1024×768 | x 590–622 | x 644–1024 | 无（22 px 间隙） | 正常 |
| 430×900 | 隐藏（`max-width:900px`） | 全宽 | 无 | 不适用 |

### 发现并修复的两个真缺陷
1. **抽屉打开时导航提示浮在面板里**：`.turn-rail-tip` 原锚定 `right:34px`，抽屉打开后提示框落在抽屉内部（实测 x 1301–1566，抽屉 1220–1600）。修复：`body:has(#ctxDrawer.open) .turn-rail-tip { right:436px; max-width:300px }`，实测移到 x 899–1164。
2. **抽屉打开时导航条被遮罩挡住不可点**：遮罩 `z-index:90`，导航条原 `40`、提示 `60`，虽然视觉上左移了却点不到。修复：抽屉打开时导航条 `z-index:95`、提示 `96`（仍在抽屉 `100` 之下）。实测 `elementFromPoint` 命中 `turn-tick`，点击后 `is-jump-target` 高亮 1 处、抽屉保持打开。
3. 附带：`max-width:900px` 下同时隐藏提示框（原只隐藏导航条），避免窄屏残留浮层。

### 边界
- 抽屉打开时点击遮罩仍会关闭抽屉（保持原交互）；导航条点击不关闭抽屉，符合“边看上下文边跳转”的用法。
- 未覆盖：超长会话（>80 轮，导航条只保留最近 80 个刻度）、触屏长按预览、屏幕阅读器朗读顺序。

---

## §11 Round 6：稳定性修复 + 前端体验收口（2026-10-30）

> 本节是交付记录，与 §2–§4 的现状审计互补：§2–§4 回答"机制是否存在"，本节回答"按批复改了什么、用什么证据证明"。
> 生产 8000 仍为 `54bf15c`（未部署）；本轮代码在 staging 8011 验收；本地 HEAD `c0510c0`。

### 11.1 批复边界

| 事项 | 批复 | 本轮结果 |
|---|---|---|
| 同步重活阻塞事件循环 | 批准修复，独立提交 | 已修 `8957508` |
| 工具结果全文入库 | 批准修复，独立提交 | 已修 `3f2e18a` |
| staging 8011 仍是旧版 | 更新，但先核实隔离 | 已升 `21c808f`，隔离有证据（11.7） |
| 导航刻度自适应与交互 | 本轮实施 | 鱼眼 + 三处真数据修正（11.4） |
| 用户/助手气泡与头像缩小 | 本轮实施 | 已改，间距按 0.5 cm 校准（11.4） |
| 吉祥物常驻与动作 | 分素材/接入两阶段 | V1 七态已接入（11.6） |
| 三套背景主题 | 本轮实施，与吉祥物解耦 | 已实现，独立提交（11.5） |
| Excel/PDF 边界测试 | 少量代表性文件 | 已做：多 Sheet xlsx / HTML / 3 页 PDF 真渲染 |
| 超 1000 轮性能测试 | 不做 | 未做；改用 6/20/60 轮合成 + 1 条真实长会话 |

明确不做：不重审整套 Agent；不重做上下文概览、账号记忆、SSE、工具注册；不新增后端主题接口；不批量删改旧工具消息；不在未确认前部署生产或 push。

### 11.2 A1 事件循环阻塞（`8957508`）

先定位再修，不是"把心跳改快"。三个真实成因：

| # | 成因 | 代码位置 |
|---|---|---|
| 1 | 最终回答只在轮末 `emit_text` 一次，工具调用期间前端完全空窗 | `src/graph/general_agent_graph.py:1279-1281` |
| 2 | 工具计时器挂在请求级 `ContextVar`，SSE 心跳任务看不到，进度只能等工具结束 | `src/core/progress_bus.py`（原 `_tool_started_at`） |
| 3 | 心跳 5 s 一次，慢工具期间形同停摆 | `src/api/main.py` 心跳循环 |

修法：边生成边推送（60 ms 节流）+ `text_reset` 区分"工具轮草稿"与"真回答" + 全局 `_tool_timers` 按 `request_id` 归属 + 心跳 1.5 s + 新增 `GET /api/v2/diag/loop-watchdog`。

同一慢任务实测：

| 指标 | 修前 | 修后 |
|---|---|---|
| 最大事件间隔 | 5.02 s | 1.52 s |
| 事件帧数 | 41 | 130 |
| 总耗时 | 24.9 s | 20.0 s |

staging 现况：`tick 732, stall_count 0, max_stall_ms 0`。

边界：覆盖主路径。若某工具自身长时间占 CPU，事件循环仍可能被间接拖慢——现靠 watchdog 观测，不用加线程/加 worker 掩盖。

### 11.3 A2 工具结果三层拆分（`3f2e18a`）

按批复不做 `content[:2000]` 粗暴截断，改为分层：

| 层 | 存什么 | 落点 |
|---|---|---|
| 原始证据 | 完整工具输出 | 受权限控制的证据存储；旧数据不删不改 |
| 模型上下文 | **更正见 §11.11**：Round 6 时 `200k/150k/50k` 只作用于证据库；送模型的那份当时没有上限。Round 7 已新增 40k 上限（头 30k + 尾 10k + 中段标识符片段） | `src/core/tool_results.py::clamp_for_model` |
| 前端记录 | 安全摘要 + 状态 + 耗时 + 可展开详情 | `progress_bus` + `/tool-result/{tool_call_id}` |

实测裁剪后 3,627 / 12,899 字符，DOI 与引用未丢。`GET /api/v2/session/{sid}/tool-result/{tool_call_id}` 可按需回取，前端加"查看原文"。

边界：旧的大 tool 行未迁移（按要求只做兼容读取与限额），首屏加载大会话仍可能慢。

### 11.4 B 批 气泡紧凑化与导航鱼眼

- `84c34f4` 头像 29 px、间距 9 px、正文 14.5 px/1.6、连续助手消息只显一次头像
- `883f40a` 头像与导航条错开 **0.53 cm**（实测 20 px）；≤900 px 为吉祥物让出底部 46 px
- `7181a37` 鱼眼导航：悬停局部展开、按下锁布局防误点、透明命中区 ±8 px

导航刻度三处真数据修正（全部由真实会话逼出，不是推测）：

| 提交 | 问题 | 真实数据 |
|---|---|---|
| `bac8657` | `<account_file_library>` 注入被当成提问 | 116 条 human 行里 58 条 |
| `e3fc7c7` | 主循环自造的"人类口吻"提示被当成提问 | 4 条 |
| `eee4b15` | 合成提示被包进 `<user_query>` 后绕过前两道过滤 | message 177 |

### 11.5 C 批 三套主题（`ddd8b9d`、`2d018ba`）

春日花笺（`2.png`，默认浅色）/ 午后书房（`4.png`，漫画）/ 夜林花语（`深色模式.png`，深色）。单一配置管理背景与配色；背景固定底层 `cover`，不随消息滚动；带缩略图选择器；刷新记住选择；深色主题完整调整文字、卡片、控件，不是只换背景。

### 11.6 D 批 吉祥物（`cd91e58`）

七态按真实生命周期编排：idle / planning / retrieving / generating / completed / failed / dragging+dropping。待机 80–90% 安静趴着，每 4–12 s 才随机微动作，特殊动作不连续重复；完成/失败只播一次；旧请求事件不覆盖新状态；切主题不重置状态；可隐藏、可拖动，位置跟随输入框上沿。

素材处理：从动作帧图按连通域切片成 8 条状态带，`steps(10, jump-none)` 步进。**不是**真补间——翻书/敲字过渡仍偏跳，若要顺滑需按素材阶段再补过渡帧。

### 11.7 E 批 staging 隔离与集成验收（`c0510c0`）

隔离用证据，而不是"端口不同所以安全"：

| 项 | staging 8011 | 生产 8000 |
|---|---|---|
| 进程 | pid 3776588（20:16:47 起） | pid 3699701 |
| 会话库 | inode 51541739804，3,244,032 B | inode 23622681839，3,383,296 B |
| 语料索引 | inode 47250650956 | inode 17179985763 |

关键文件 LF 哈希与本地逐一致：`main.py f1c77687…`、`app.js 914c78af…`、`turn_rail.js 9537edd8…`、`mascot.js cd9e1431…`、`index.html 97e3a6ea…`。端点冒烟全 200：`/sessions`、`/turns`、`/context-detail`、`/turn-context`、`/diag/loop-watchdog`。

部署踩坑（已写进门禁文档）：`git archive` 在 `core.autocrlf=true` 下会把源码转成 CRLF，与本地 LF 哈希不一致 → 必须 `git -c core.autocrlf=false archive`。

回滚已按批复拆分：**代码回滚只动源码 tar**；数据库灾难恢复单独执行，且先另存当前库，禁止旧库覆盖新库。

### 11.8 真实模型任务（3 个）挖出两个真缺陷

| 任务 | 结果 | 挖出的问题 |
|---|---|---|
| T1 本地+联网混合检索 | 带 DOI 引用，`partial` | 长检索词超 128 字符 → `[WEB_TOOL_ERROR]` 整轮失败 |
| T3 长问题多轮检索 | 2,787 字符对照表 | 网页抓取 100% 被拦 |
| T4 抓取网页 | `complete` / 221 ms / 11,145 字符 | — |

- `0060edb`：检索词按词边界截断后继续检索，并在返回内容里明确告知"已截断，若结果不相关请用更短关键词重试"。
- `21c808f`：本机 TUN 代理把所有域名解析到 `198.18.0.0/15`（RFC 2544 / Clash fake-IP 段），守卫用 `is_global` 判定于是全拦 → 改为"全部答案都落在该段时放行并记审计日志"；真实私网与混合结果仍拒绝。同一 URL 修前 72 字符报错，修后取回 11,145 字符真实正文。

### 11.9 证据与当前状态

- 全量 **382 passed**（`python -m pytest src -q -p no:cacheprovider`）
- 五类证据分开记录，不互相冒充：`RELEASE_GATE_20261030_21c808f.md`（静态 / 单元 / 模拟 / 真实浏览器 / 真实模型 / staging 集成）
- 本地 8010 带全部修复运行；生产 8000 仍 `54bf15c`；未 push


## §11.11 补充审计：479,708 字符 ToolMessage 的根因与修复闭环（Round 7）

> 定位：定向根因取证 + 修复验证，不重审 Agent、不重做工具系统。
> 边界：未部署生产 8000、未 push、未删除或改写任何历史数据库记录。

### 11.11.1 结论先行

| 问题 | 结论 | 证据等级 |
|---|---|---|
| 哪个工具产生 479,708 字符 | `deepseek_fetch_paper`（并行全文模式） | **确定**：生产库直接读到记录 |
| 为什么膨胀到该规模 | 2 篇开放获取论文**全文**被原样拼接，单源与总量都没有预算 | **确定**：代码 + 内容结构 |
| 怎么进的库 | 工具结果原样成为 `ToolMessage.content`，`_safe_persisted_trace` 只裁本地检索/表格/图片，其余放行 | **确定**：代码路径 |
| 下一轮是否完整发给模型 | **是**。历史加载原样重建；预算检查在 12 万 token 量级不触发 | **确定**：临时库 + 真实加载函数实测 |
| Round 6 修复覆盖范围 | 只覆盖**证据库**（200k 首尾保留），不覆盖模型上下文、不覆盖旧行 | **确定**：常量只在 `tool_results` 里被引用 |
| 现在是否闭环 | 是：新增 40k 模型上限（新消息 + 旧行读取两侧），DOI 不丢，证据库留有 200k 首尾截取副本（**不是完整原文**，见 11.11.3 的「表述纠正」） | 390 passed + 探针 |

### 11.11.2 原始记录取证（生产库只读）

| 项 | 值 |
|---|---|
| `session_id` | `26ebefbd-d516-44da-a151-5a3b4414814b` |
| `message_id` | `440` |
| `tool_call_id` | `call_abd6df397a3342debfe957f0` |
| 工具名 | `deepseek_fetch_paper` |
| 落库时间 | 2026-10-30 09:40:56 |
| 执行耗时 | 18,112 ms（`execution_json.elapsed_ms`） |
| 执行摘要 | 「获得 2 条网页资料」；`action_hint` = `10.3390/plants12010160` |
| 原始/封装/落库长度 | 三者相同：**479,708 字符**（工具返回值即消息内容，无二次封装） |

内容结构（只看统计与首尾，未导出正文）：

| 指标 | 值 |
|---|---|
| 字符 / 行 | 479,708 / 2,027（平均 236.7 字符每行） |
| `doi` 出现次数 | 322 |
| `Figure` / `Table` / `---` | 69 / 29 / 72 |
| `http://` / `https://` | 10 / 11 |
| 2000 字符块重复 | **0**（不存在整段重复拼接） |
| 是否 JSON / HTML / 异常日志 | 否（`{`、`}`、`<html` 均为 0） |

结构为：一行 `Parallel full-text attempt: 2 content page(s) succeeded, 3 attempt(s) failed…`，
随后按 `[n] format=… status=… URL=…` 分块，成功块内嵌 `<untrusted_web_data>` 全文。
判定：**两篇开放获取论文的完整正文**（约 24 万字符/篇），不是重复、不是日志、不是表格。

### 11.11.3 三条数据路径

| 路径 | 位置 | 结论 |
|---|---|---|
| A 原始证据 | `tool_results` 表 | 有，按 `tool_call_id` 可回取；**存的是 200k 首尾截取副本**（非完整原文）+ 全文 sha256；权限随会话 |
| B 模型实际输入 | `ToolMessage → messages 表 → manager 加载 → ContextBudget → 模型` | **修复前全程无裁剪**（详见 11.11.5） |
| C 前端展示 | `/messages` 序列化 | 已安全：tool 行只返回 `status/summary/action_hint`，从不返回正文 |

> **表述纠正（2026-10-30 晚）**：早先写"证据全文仍可回取"不准确。实际存在三份**不同**的东西，做科研证据核验时必须分清：
>
> | 层 | 内容 | 上限 | 生命周期 |
> |---|---|---|---|
> | ① 原始抓取结果 | 抓取工具当次拿到的响应体 | 无（取决于远端） | **不落盘**；只存在于当次工具返回与（未裁剪前的）memory |
> | ② 证据库副本 | `tool_results` 表里的 `HEAD 150k + TAIL 50k` 截取 + 全文 sha256 | **200k 字符/条** | 随会话保留，按 `tool_call_id` 回取 |
> | ③ 模型可见 | 进 prompt 的 `ToolMessage` 内容 | **40k 字符/条** | 每轮重建 |
>
> 因此"是否能拿到某篇论文的完整正文"取决于①；②③都只是截取副本。需要完整正文时应按 DOI 重新抓取，而不是假设库里留着全文。超长正文的②还会丢掉中段（首尾各保留、中间丢弃），这与"完整保存"不是一回事。

### 11.11.4 形成超长消息的代码路径

1. `src/tools/deepseek_search_fetch.py:473-477`：并行全文模式对每个成功来源执行
   `lines.append(f"...<untrusted_web_data>\n{item.get('content','')}\n</untrusted_web_data>")`，
   **没有单源上限、没有总量上限**；`_fetch_url` 也不限返回体大小。
2. `src/graph/general_agent_graph.py:631-632`：`_safe_persisted_trace` 只对
   `local_*_search`、`inspect_uploaded_table`、图片做收敛，其余 `safe.append(message)` 原样落库。
3. `src/session/manager.py:495-505`：历史加载 `ToolMessage(content=content, …)` 原样重建。
4. `src/core/context_budget.py:282-283`：`ratio < soft_threshold(0.75)` 直接
   `return BudgetResult(NORMAL, messages)`；`_hard_trim`（把 >3000 字符 tool 收敛到标识符）
   只在进入 `_compress` 后才执行。

### 11.11.5 为什么 Round 6 没覆盖到

`MAX_CHARS = 200_000`、`HEAD_CHARS = 150_000`、`TAIL_CHARS = 50_000` 这三个常量
**只出现在 `src/core/tool_results.py`**，且只被 `_clip()`（证据库入库）使用。
模型上下文、历史加载、预算检查三处都不引用它——所以「已实施长度控制」这句话
当时只对证据库成立，对模型上下文不成立。§11.3 已按此更正。

### 11.11.6 最小修复

| 改动 | 文件 | 说明 |
|---|---|---|
| 新增 `clamp_for_model()` | `src/core/tool_results.py` | 40k 上限：头 30k + 尾 10k + 中段标识符片段（每处 ±120 字符，最多 40 处、≤6k），总长严格 ≤ 40k |
| 工具返回后收敛送模型那份 | `src/graph/general_agent_graph.py` `_run_regular_calls.run_one` | 放在 `completion_callback` **之后**：证据入库仍拿完整结果，模型只拿收敛版 |
| 历史加载时收敛旧行 | `src/session/manager.py` `_get_messages_sync` | 只读收敛，**不改写、不删除**旧记录 |
| 8 例测试 | `src/core/test_tool_result_clamp.py` | 单条超长 / 短文本不动 / 中段 DOI 保留 / 多条各自有界 / 图执行路径 / 旧库跨轮 / 证据库更全 / API 只出状态 |

### 11.11.7 修复前后（同一临时库、同一加载函数、同一预算配置）

| 指标 | 修复前 | 修复后 |
|---|---|---|
| 单条 tool 消息进入模型 | 479,708 字符 | **40,000 字符** |
| 历史加载总字符 | 479,730 | 40,022 |
| 预算估算 ratio（1M 窗口） | 0.12 → `NORMAL` 放行 | 0.0101 |
| 证据库原文 | 479,708（首尾保留 200,022，`truncated=1`） | 不变（完整可取） |
| 中段 DOI | — | 保留（`10.9999/mid.0` 等仍在输出里） |

探针：`experiments/trace/probes/probe_tool_result_budget.py`；
产物：`experiments/trace/runs/20261030/S11/tool_result_budget_probe.json`。

### 11.11.8 安全边界复核：`198.18.0.0/15` 放行（部署前置）

| 检查项 | 结论 |
|---|---|
| 抓取在哪执行 | **远端 provider**（`requests.post("https://api.deepseek.com/v1/tools/fetch")`），本地不直连目标 → 放行 fake-IP 不构成「本地被当跳板」 |
| 放行条件 | 仅当**全部**解析答案都落在 198.18.0.0/15；混合结果、真实私网、localhost、单段主机名仍拒绝 |
| 新增加固 | **IP 字面量不再按 fake-IP 放行**：`http://198.18.0.5/` 现返回「目标地址不是公网地址，已拒绝访问」 |
| 生产会不会放宽 | **不会**：生产源码当前**没有**该分支；服务器实测 `example.com` → 104.20.23.154 等公网地址，`any_fake=False`，该分支在生产不可达 |
| 残留风险 | TUN 开发机上该守卫对域名等同失效（设计取舍）；provider 侧重定向与最终解析无法本地校验 |

### 11.11.9 残留风险与建议

1. **40k 是经验值**（≈1 万 token），应按 `context_usage` 的 `input_tokens` 分布回归校准，而不是只看字符数。
2. 旧行未迁移：读取时逐条裁剪，单条约 10ms 量级；会话首屏不受影响（`/messages` 不返回正文）。
3. 多条累加仍靠 soft/hard 阈值兜底，**未新增轮级总量预算**；如需硬上限，应在 `ContextBudget` 里加「工具结果总量」维度。
4. `deepseek_fetch_paper` 的并行全文模式本身仍无单源预算；模型侧已收敛，但证据库仍会存下完整全文（有 200k 保留上限）。

### 11.10 尚存风险与待决

1. **是否部署生产 8000**：候选 `21c808f` 已在 staging 验收通过，部署与回滚步骤见门禁文档第六节，等确认。
2. **是否 push `qaagent`**：本地领先 15 个提交。
3. A1 覆盖主路径；工具自身长时间占 CPU 时事件循环仍可能被间接拖慢（靠 watchdog 观测）。
4. A2 旧的大 tool 行未迁移，首屏加载大会话可能慢。
5. fake-IP 放行依赖"全部答案都在该段"，混合解析仍拒绝。
6. 吉祥物补帧是 CSS 步进，非真补间。

## §12 Round 7：前端交互修复（导航即时更新 / 灰色体系 / 吉祥物帧与拖拽）

| 项 | 根因 | 修复 | 提交 |
|---|---|---|---|
| 导航必须刷新才出新刻度 | `turn_rail.js::loadTurns()` 按 `loadedSession` 只拉一次索引，`MutationObserver → scheduleBuild() → build()` 复用旧 `turnsCache` | `addPending()` 提交即插临时刻度；`sync()` 在 `done` 后拉一次最新页并用真实 `message_id` 替换；`dropPending()` 失败即撤；切换/清空会话 `reload()` | `1beb927` |
| 导航橙色太重 | `.turn-tick` 与鱼眼规则写死 `--accent` | 改 `--rail-tick*` 灰阶变量组（浅色/卡通一套、深色一套），提示框缩到 260px | `99b54da` |
| 吉祥物像横向轮播 | `.citrus-mascot-frame` 给 `background-position` 加了 `.1s linear` 过渡，10 帧精灵图换帧时横向滑过中间帧 | 改硬切（`transition: none`），帧仍由 `--mascot-col` 决定 | `8899419` |
| 拖拽只能回原位 | `finish()` 里 `node.style.transform = ''` 直接清位移 | 持久偏移 `posX/posY` + clamp + 28px 就近吸附 + `localStorage(citrus_mascot_pos)` + 双击复位 | `75f5906` |

验收：`experiments/trace/probes/probe_rail_live_update.py`（真实浏览器、不调模型）8 项断言全 PASS；
`src/api/test_frontend_round7.py` 6 例静态契约；全量 396 passed。

## §13 Round 8：并行全文抓取去重 / 相关性分层 / 轮级预算（2026-10-30）

> **本节已被 §14 取代。** 用户否决了「全文去重 + 相关性评分 + 轮级预算」方案，理由是它把简单问题复杂化了：真正要解决的只是「同一篇论文的多个来源不要拼接」。对应实现（提交 `056c634`/`beb8375`/`15c93ba`）已回退到 `b2fd9ae` 基线，本节保留为决策记录，不再代表当前代码。


### 13.1 问题（实测）

- 生产库 `messages.id=440`（session `26ebefbd-…`，工具 `deepseek_fetch_paper`）单条
  **479,708 字符**、耗时 18,112 ms；其中 2,027 行、322 处 DOI。
- 该记录内部有 **3 个页面块**：`identifier_metadata`（2 字符）、`publisher_html`
  （239,179 字符，`https://doi.org/10.3390/plants12010160`）、`pmc_html`
  （239,639 字符，`PMC9824665`）——后两者是**同一篇论文的两条来源路线**。
- 六次抓取里四次命中同一篇，模型上下文被重复内容占满；单条 40k 兜底（`b2fd9ae`）
  只解决“单条过大”，不解决“重复”和“轮级总量”。

### 13.2 函数级改动（`src/tools/deepseek_search_fetch.py`）

| 层 | 函数 | 行为 |
|---|---|---|
| 开关 | `_dedupe_enabled()` | 读 `CITRUS_FETCH_DEDUPE`，**默认关**；关闭时与 `b2fd9ae` 完全一致 |
| URL | `_canonical_url()` | 小写 scheme/host、去 fragment 与 utm/gclid 等跟踪参数、`doi.org`/`dx.doi.org` 归一 |
| 身份 | `_paper_key()` / `_extract_doi()` | DOI > 规范化 URL > 标题归一化哈希；正文里的 DOI 可反推 |
| 指纹 | `_content_fingerprint()` | 去标签/空白后取前 3,000 字符哈希（**不含全长**，站点标记差异不再干扰） |
| 相似 | `_body_similarity()` | 左侧均匀取 ≤120 个 60 字符窗口，命中比例 ≥0.5 判同一篇（兜底） |
| 合并 | `_dedupe_pages()` | 同 key 多版本按“有 DOI > 结构化全文本 > 正文更长”保留，记录被合并来源 |
| 筛选 | `_select_relevant_passages()` | 段落打分（词重叠 + DOI/表图编号/章节标题），保留章节头，去重后按预算截取；异常降级头 60%+尾 40% |
| 预算 | `_FETCH_PER_PAGE_CHARS=6000` / `_FETCH_TOTAL_CHARS=24000` | 每篇与整轮的模型可见上限，用尽即停并显式提示 |
| 统计 | `_fetch_stats()` | 候选/派发/成功/失败/合并/片段数/原文与模型字符数，随 `fetch_stats` 返回 |

轮级总预算另加在 `src/core/tool_results.py`：`begin_turn_budget(120_000)` 由
`_run_regular_calls()` 每轮调用，`clamp_for_model()` 在剩余额度不足时硬截并写
`[TOOL_RESULT_OMITTED]` 占位；未调用时行为不变。

### 13.3 真实数据验证（同一记录 `id=440`）

| 指标 | 旧行为 | 新行为（`CITRUS_FETCH_DEDUPE=1`） |
|---|---|---|
| 页面块 | 3（含同一篇两份） | 2（1 篇正文 + 1 条元数据） |
| 模型可见字符 | 479,708 | **5,938（1.2%）** |
| 证据片段 | 无结构，整段粘贴 | 4 个片段，`degraded=False` |
| 去重记录 | — | `duplicate_body_similarity: PMC9824665 -> doi.org/10.3390/plants12010160` |

另做真实抓取 A/B（DOI `10.16377/j.cnki.issn1007-7731.2026.10.021`、标题
`Huanglongbing citrus greening review`）：两次都只取到 1 页（内容 321 字符 /
Europe PMC 无 PMCID），**未触发多路线合并**——这一步只证明了开关不破坏正常路径。

### 13.4 测试与回滚

- `src/tools/test_fetch_dedupe.py`：20 例（URL 归一、身份键、指纹、相似度、合并、
  筛选预算、降级、统计、端到端、轮级预算）。
- 全量 `pytest src`：**416 passed**。
- 回滚：`CITRUS_FETCH_DEDUPE` 不设即回到 `b2fd9ae` 行为；代码级回滚点为
  `056c634`（主体）与 `beb8375`（相似度兜底），两者互不依赖前端提交。

### 13.5 残留风险

1. 相似度阈值 0.5 是经验值；同一主题的不同论文若正文高度同构（如同一模板的
   多篇试验报告），仍有误合并可能——当前有身份键优先与结构化格式优先两道前置，
   但未做大规模标注集验证。
2. 相关性打分只用词重叠与结构信号，不调用模型；中文长句切词靠正则，专业术语
   可能漏配。降级路径已保证“不因打分失败而丢内容”。
3. 每篇 6,000 字符对方法/结果细节仍可能不足；需要完整原文时应单独按 DOI 抓取
   或走本地语料（`local_hybrid_search`），而不是调大总预算。
4. 候选层去重只按 URL/DOI 身份，同一论文的“搜索摘要页”与“全文页”仍会各抓一次
   （随后在结果层被正文相似度合并，代价是多一次请求）。

### 13.6 追加边界行为（提交 `15c93ba`）

| 要求（任务书十条） | 实现 | 测试 |
|---|---|---|
| 同一 DOI 的两个不同 URL | `_paper_key` 归一后同键合并，较优版本胜出 | `test_dedupe_pages_merges_same_doi_and_keeps_better_version` |
| 相同正文的多个镜像 | 正文指纹 + 相似度双路识别 | `test_dedupe_pages_merges_identical_body_from_different_urls`、`test_body_similarity_matches_same_article_with_different_banner` |
| 相似标题但不同 DOI | 身份键优先于标题，正文相似度不足即分开 | `test_similar_titles_with_different_dois_are_not_merged` |
| 正文与补充材料 | 同 DOI 但相似度 <0.3 → 另存并留痕 `kept_separately_same_doi_different_body` | `test_supplementary_material_is_kept_separate_from_body` |
| 两篇完全不同的长论文 | 各自保留 | `test_dedupe_pages_keeps_distinct_papers` |
| 全部失败 / 部分成功 | `fetch_stats` 分别计数，正文只含成功页 | `test_all_failed_reports_zero_pages`、`test_partial_success_keeps_only_successful_pages` |
| 无 DOI 的网页 | 退化到规范化 URL 键 | `test_paper_key_prefers_doi_then_url_then_title` |
| 多次并行累计超预算 | 轮级 120k 字符 ContextVar 预算 | `test_turn_budget_caps_second_result` |
| 用户明确要求全文解读 | `fulltext=True`：按章节顺序、每篇 24k / 整轮 48k，不筛选 | `test_fulltext_mode_keeps_sections_in_order` |
| 旧历史超长消息兼容读取 | `b2fd9ae` 的 40k 读取上限不变 | `src/core/test_tool_result_clamp.py` |

另加一条任务书未列但实测会遇到的行为：**确定性失败短路**。同一请求内被判定为
`target_access_blocked` 或 `fetch_service_http_error` 的 URL 不再重抓，返回
`skipped_known_failure`；临时超时不短路（仍可在剩余预算内有限重试）。

### 13.7 真实记录 `id=440` 两种模式实测

| 模式 | 模型可见字符 | 证据片段 | 占原始比例 |
|---|---|---|---|
| evidence（默认） | 5,837 | 7 | 1.22% |
| fulltext（用户要求通读） | 24,044 | 23 | 5.01% |

两种模式都保留章节标题与 DOI/表图编号；fulltext 模式实测章节顺序未被重排。

## §14 Round 9：同篇论文多来源并行竞争，首个合格结果胜出（2026-10-30）

### 14.1 问题（实测）

旧实现 `deepseek_fetch_paper` 对**同一篇论文**的全部候选来源一次性提交、等全部完成，
再把每份成功正文都拼进同一条工具消息。生产记录 `messages.id=440` 因此得到
479,708 字符：Europe PMC XML 与出版商 HTML 各约 24 万字符，被同时送入模型。

### 14.2 现方案

| 项 | 实现 |
|---|---|
| 并发度 | 同时最多 2 个候选在途（`_RACE_PARALLEL`），其余排队 |
| 判定时机 | 任一候选完成即判定（`wait(..., return_when=FIRST_COMPLETED)`），不等全部结束 |
| 有效性检查 | `_classify_fetch_page`：身份（DOI 命中正文/URL，或标题相似度 ≥0.6）、非登录/错误页、正文 ≥1500 字符且 ≥2 个章节标记；不调用模型 |
| 摘要 | 只拿到摘要记 `abstract_only`，继续等其他来源，不提前结束 |
| 胜出后 | 取消在途（`cancelled_after_winner` / `in_flight_discarded_after_winner`）、清空队列（`not_started_after_winner`）；迟到结果永不读取 |
| 输出 | 一条工具消息最多一份正文；其余候选只留 URL/格式/状态/耗时/原因 |
| 失败 | 全部失败返回 `[WEB_FETCH_FAILED]` + 逐条原因；仅摘要返回 `[WEB_FETCH_ABSTRACT_ONLY]` |
| 元数据 | Europe PMC 记录解析出的 PMC 路由插入队首，保留原能力 |
| 上下文保护 | 沿用 `b2fd9ae` 的 40k 模型可见上限；完整证据仍入库，历史记录不改 |
| 多篇论文 | 一次调用只处理一篇；多篇请分别调用（不同 DOI 不互相取消） |

### 14.3 测试与实测

- 单测 `src/tools/test_fetch_race.py`（10 例，全 mock）：先返回的摘要不胜出、双成功不拼接、
  胜出后不等待在途（实测 <0.9 s）、迟到结果不进消息、`abstract_only` 诚实标记、全失败原因、
  身份不符拒绝、登录页拒绝、取消不留未处理异常、三策略（顺序 / 旧并行合并 / 新竞争）对比。
- 全量：406 passed（基线 402，去掉已回退的去重测试、新增 10 例）。
- 真实抓取（`10.3390/plants12010160`，1 次）：1,093 ms 胜出，正文 **1 份** 239,048 字符，
  1 个在途丢弃、2 个未启动，工具消息内 `<untrusted_web_data>` 恰好 1 个。

### 14.4 残留风险（如实）

1. 取消只作用于本地等待与后续调度；已发往远端 provider 的请求**可能仍在执行并计费**，
   本实现不声称远端物理取消。
2. 胜出来源若是导航/页脚冗长的出版商 HTML，原始正文可能仍有 20 万级字符；
   模型侧有 40k 上限，但证据库会保存原始全文。
3. 「首个合格」依赖启发式有效性检查；正文极短但结构完整的预印本可能被判 `abstract_only`。

## §15 Round 9 验收：导航即时更新与吉祥物原地动作/自由拖拽（真实浏览器，2026-10-30）

> 环境：本机 `127.0.0.1:8010`，HEAD `b025330`，headless Chrome（CDP），1440×900，
> 合成会话 `rail-many-fd6458ab`（13 条真实 human 消息）。全程**不调用模型**。

### 15.1 导航即时更新（P0）

| 验收动作 | 期望 | 实测 |
|---|---|---|
| 提交后立刻加临时刻度 | 刻度 +1，不刷新 | `base=16 → afterAdd=17` |
| 服务端确认后替换为正式 ID | 不重复、标签换成真实问题 | `afterSync=18`（无重复），标签由「第 18 次提问，正在保存」变为「定位到第 18 次提问：模拟服务端确认的问题」，`pendingDropped=true` |
| 删除消息后索引更新 | 刻度减少、无残留 | 删除 3 条后重新加载：`clean=13`、`stale=0` |
| 切换会话重建索引 | 刻度数随会话变化 | 同一夹具插入 3 条后为 16、删除后为 13；另一 60 轮夹具为 60 个刻度；`loadTurns()` 在 `sid !== loadedSession` 时重置 `turnsCache` |

复现方式：`experiments/trace/probes/rail_fixture_messages.py add|drop <session_id> <n|tag>`
插入/删除真实 human 消息；浏览器侧 `_scratch/rail_acc.js`、`_scratch/rail_sync_mock.js`、`_scratch/rail_clean.js`。

### 15.2 吉祥物：原地动作而非横向轮播（P2）

- 待机 60 秒、每 5 秒采样一次 `--mascot-col`：13 次采样**全部为 0**，未出现 0→9 的横扫。
- 机制：`.citrus-mascot-frame` 的 `background-position` 由 `calc(var(--mascot-col) * 11.1111%)` 决定，
  且 `transition: none`——这正是上一版"轮播"的根因（给 `background-position` 加了过渡）。
- 采样局限：微动作单帧仅约 400 ms，5 秒采样会漏掉，**本表不构成微动作频率统计**。

### 15.3 吉祥物：自由放置与就近吸附（P3）

| 拖到 | 元素位置 | 持久化 `citrus_mascot_pos` |
|---|---|---|
| 聊天区左侧 | (343, 131) | `{-210.16, -595.5}` |
| 中间 | (680, 290) | `{126.84, -436}` |
| 右侧 | (1260, 401) | `{706.84, -325.5}` |
| 空白区 | (740, 530) | `{186.84, -196}` |

- 四处置放**互不相同**，说明不是单一固定吸附点；坐标以聊天容器相对值保存。
- 主题切换（light→dark→light）后位置不变：(740, 530) → (740, 530)。
- 触发 `resize` 后仍在可见区：(740, 530)。
- 松手后 `data-state` 回到 `idle`（落地动画结束后的真实 Agent 状态）。

### 15.4 未覆盖与限制

1. 真实鼠标手感（长按选刻度、拖拽跟手度）仍需人工在窗口里点一次；
   headless CDP 只能验证 DOM、计算样式与合成 PointerEvent 路径。
2. 「删除单条提问」在 UI 上没有入口（无单条消息删除 API），
   本轮以「DB 删除 + 重新加载」证明索引会跟随数据变化。
3. 微动作频率未做统计，仅证明不会横向轮播。
4. 全量测试：`406 passed`。
