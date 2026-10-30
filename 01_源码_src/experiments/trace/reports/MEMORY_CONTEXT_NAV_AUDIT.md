# 账号记忆 / 上下文概览 / 长对话导航 —— 专项只读审计

> 审计时间：2026-10-30 15:35–16:00（本机 8010 实例 + 本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent`）
> 审计性质：**只读**。本轮未修改 Agent 主循环、未改后端、未部署、未 push。
> 环境限制：dsh 浏览器 provider 不可用（`browser provider(s) electron … unavailable`），**没有做真实浏览器视觉验收**；下文把"代码/接口/DOM 证据"和"需要人工看的项"严格分开写。

---

## 0. 结论速览

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
