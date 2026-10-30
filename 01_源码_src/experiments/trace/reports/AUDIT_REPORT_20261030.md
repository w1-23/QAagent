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
