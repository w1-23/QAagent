# CitrusQA Agent 执行流程与流式可行性 · 专项审计报告（合并版）

- 审计日期：2026-10-30
- 审计对象：**本地仓库 HEAD**（代码在 `01_源码_src/`），提交 `80bab4b` + 本报告提交
- 审计方式：只读。不改生产代码、不部署、不推送；一切结论给到 `文件:行号`，可复核
- 合并说明：本文件合并并取代 `audit_20261030/` 下原有 5 份（`CURRENT_AGENT_EXECUTION_FLOW.md`、`CURRENT_STREAMING_AND_UI.md`、`PROGRESSIVE_REASONING_FEASIBILITY.md`、`SYSTEM_STABILITY_AUDIT.md`、`TEST_COVERAGE_AND_GAPS.md`），并补入第二轮审计的新发现与对第一轮的错误更正

---

## 0. 结论速览

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

# 第二编　前端交互、文件预览与任务生命周期（差异审计）

> 审计对象：本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent\01_源码_src`（HEAD `61a8f5b` 之前的 `ee23a42`）。
> 审计性质：只读。未改代码、未部署、未重启服务、未 push。
> 与第一编的关系：第一编回答“Agent 怎么跑、为什么没有过程”；本编只回答新增的五项交互需求差在哪，不重复全系统审计。
> **前置警告**：服务器 `/home/WZQ/CitrusQA` 运行的 `main.py / general_agent_graph.py / app.js` 与本地 HEAD 不是同一版本（第一编已列哈希），本编所有行号以**本地**为准，实施前必须先对齐基线。

## 0. 结论速览

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

# K. 更正记录（2026-10-30 第二轮，S0 基线核对后）

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
| **S0** | 发布基线、回滚包、隔离测试实例配方 | ✅ 完成（2026-10-30，本地提交 `8a7ddef`） |
| **S1** | 取消生命周期（状态机/job 级取消/协作式取消/原子产物/前端三段式） | ✅ 完成（2026-10-30，见 `S1_CANCEL_LIFECYCLE.md` 与本报告 §L） |
| **S2** | 动态公开工作说明（`assistant_progress`） | 未开始 |
| **S3** | PDF 预览与文件安全 | 未开始 |
| **S4** | 文件↔会话↔消息跳转 | 未开始 |
| **S5** | 六页指南、视觉验收、发布演练 | 未开始 |

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

# L. S1 执行后复测（2026-10-30，第三轮）

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

# M. S2 执行后复测（2026-10-30，第四轮）

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

# N. S3 执行后复测（文件 ↔ 会话 / 轮次跳转）

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

# O. S4 执行后复测（文件预览）

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

# P. S5 执行后复测（分页式使用指南）

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

# O. 复测（Round 4：验收缺口补齐 + 服务器隔离实例）

本节只记录"执行后再次审计"得到的**新事实**，结论以隔离实例为证据，生产未动。

## O.1 首轮审计的两处误判（自我更正）

1. **`no_files_committed` / `has_cancelled_row` 是假通过**：`probe_cancel_e2e.py` 读的字段名与接口返回不一致（`files`/`items` vs `uploaded`/`generated`），且"必须有 cancelled 行"在"停止落在答案生成阶段"时本就不该成立。已改为等价断言。
2. **"本地检索不可用"不能由单次实例推断**：隔离实例（同代码同语料，软链嵌入缓存）上 `本地检索` 真实返回结果，成本轮 S2 探针可见。

## O.2 新发现的问题

| 编号 | 问题 | 证据 | 处置 |
|---|---|---|---|
| O-1 | `GET /jobs/{id}`、`GET /sessions/{id}/jobs` 无归属校验，知道 id 即可读他人任务状态/停止原因/进度 | `main.py` 原实现只 `get_job(job_id)` | 本轮已修（`require_owned_session`） |
| O-2 | 重复点停止会把 `cancel_requested` 再写一次时间戳，返回值误导调用方 | `jobs.py` WHERE 含 `cancel_requested` | 本轮已修（只从 running/detached 迁移） |
| O-3 | 进度只在运行期，刷新后看不到"上一轮跑到哪" | `public_progress` 仅 `emit_progress` | 本轮已修（最后一条写 `task_jobs.progress_summary`） |
| O-4 | PDF 失败只有一句泛化"获取失败"，403/超大体量/断网无法区分 | `session_files.js` catch 全走同一句 | 本轮已修（六类分流 + 40 MB 上限） |
| O-5 | `/api/v2/files?scope=account` 返回**其它会话**的文件 | 隔离实例实测拿到上一探针会话的 csv | 未改；前端必须按 `session_id` 过滤，或后端加会话过滤（待定） |

## O.3 复测结论

- 本地套件 `259 passed / 0 failed`（基线 179，本轮 +80）。
- 隔离实例 8011：S1 全部 9 项 ✓、S2 三条进度 + 7 次工具真实执行 ✓、S3 六项 ✓、S4 五项 ✓。
- 生产 `/home/WZQ/CitrusQA` 仍是 `ff58b1f` 字节，本轮未部署。
- 仍未覆盖：真实浏览器视觉验收（本机无 Node、无浏览器 provider）、GPU 生产表现、跨账号文件列表语义。

## P. 补测：文件库的会话作用域（O-5 关闭）

### P.1 问题

`GET /api/v2/session/{session_id}/files` 的路径声称是"这个会话的文件"，实现却返回**整个账号库**，并用 `scope="account"` 自陈。同一份数据在两条路由上语义不同：调用方信任 URL 就会拿到别的对话的文件名。这不是越权（同账号），但属于"契约说谎"，与审计中反对的"用近似值冒充精确"是同一类问题。

### P.2 改法（最小）

- `src/session/files.py`：`list_account_files(owner_user_id, *, session_id="", touch=True)` —— 传入 `session_id` 时在 SQL 里过滤，不传时保持整库（默认行为不变）。
- `src/api/main.py`：`/api/v2/session/{id}/files` 改为按会话过滤并回 `scope="session"`；`/api/v2/files` 新增可选 `?session_id=`，回 `scope="session"`，不传仍为 `"account"`；过滤分支同样调用 `require_owned_session`。
- 前端未改：文件库面板调的是 `/api/v2/files`（不带参数），行为与之前一致。

### P.3 验证

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

### P.4 顺带修掉一个"假通过"

`src/core/test_cancel_acceptance.py` 的 `CrossUserJobGuard` 里，`current_user` 的替身写成了 `async def`，而 `current_user` 是同步调用：路由拿到的是一个 coroutine 对象（恒真），404 全由归属检查桩决定。**即使身份读错，测试也会过。** 已改为同步函数，并补一例正向对照 `test_identity_reaches_the_route`（归属放行时路由必须看到 `user_id=2` 并成功返回）。改后该类 10 passed，全量测试不再出现 `coroutine was never awaited` 警告。

### P.5 尚存

- 文件库面板仍是**整库**视图（这是产品决定，不是缺陷）：用户能看到自己所有会话的产物，并可从每行"↗"跳到产出它的那轮对话。若希望侧栏只显示当前会话，把前端 `session_files.js` 的 `/api/v2/files` 改成带 `session_id` 即可，后端已经支持。
- 旧数据里没有 `turn_request_id` 的文件仍不显示跳转按钮（不猜）。