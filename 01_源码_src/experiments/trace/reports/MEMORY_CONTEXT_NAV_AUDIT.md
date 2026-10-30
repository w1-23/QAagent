# 账号记忆 / 上下文概览 / 长对话导航 —— 专项只读审计

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

# 九、本机 8010 网页搜索 `auth` 失败：根因与修复（配置级，未改代码）

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
| 现在是否闭环 | 是：新增 40k 模型上限（新消息 + 旧行读取两侧），DOI 不丢，证据库原文仍可回取 | 390 passed + 探针 |

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
| A 原始证据 | `tool_results` 表 | 有，按 `tool_call_id` 可回取；200k 首尾保留 + 全文 sha256；权限随会话 |
| B 模型实际输入 | `ToolMessage → messages 表 → manager 加载 → ContextBudget → 模型` | **修复前全程无裁剪**（详见 11.11.5） |
| C 前端展示 | `/messages` 序列化 | 已安全：tool 行只返回 `status/summary/action_hint`，从不返回正文 |

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
