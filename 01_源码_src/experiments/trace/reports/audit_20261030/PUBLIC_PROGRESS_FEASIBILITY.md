# 动态公开工作说明：可行性判断与最小改造方案

问题一句话：**Codex 式“分析”行，能不能在不动 Agent 决策、不加模型请求的前提下做出来？**

---

## A. 当前模型在工具调用前后能否返回公开自然语言文本？

**不能（实测，非推断）。**

- 调用形态：`general_agent_graph.py:932 _call_llm(...)` 非流式，无 `stream=True/astream`。
- 落库证据（`agent/state/sessions.db` → `messages`）：R4 一轮内 4 个 `ai` 行带 `tool_calls_json`，其 `content` 长度**全为 0**；`reasoning` 列**全为空**。R1 同样（`ai len=0 tc=1`）。
- 即：本 provider 在返回 `tool_calls` 时，`content` 为空串。这不是丢字段，是**没有内容**。

## B. 这些文本目前在哪里被读取、过滤、丢弃？

- 没有任何位置读取“工具调用前的 assistant 文本”——因为它不存在。
- `emit_text(answer)` 只在两处调用：`general_agent_graph.py:1327`（无工具调用的最终回答）与 `:1389`（无工具综合）。
- 私有推理是**主动封堵**的，不是遗漏：`progress_bus.py:210`（`"""Emit a safe progress label; never forward model-generated reasoning text."""`）、`app.js:2186`（`case 'reasoning': break;`）。
- 因此不存在“已经产生却浪费掉”的公开文本。想凭空接入，只能新造内容源。

## C. 能否不新增模型请求、不改工具逻辑，直接把已有公开文本作为独立事件推送？

**“已有公开文本”不存在，所以字面意义上不能。** 但有一类**真实、可审计、零泄露**的素材已经存在，足以支撑一版诚实的“分析”行：

| 素材 | 现有位置 | 可以直接说的事实 |
|---|---|---|
| 工具意图 | `progress_bus.tool_action_hint()` → `action_hint` | “准备检索：柑橘木虱 防治措施…” |
| 工具终态 | `emit_tool_result(status/reason_code/summary/elapsed_ms)` | “联网检索失败：鉴权不通过（auth）；本地检索失败：工具错误” |
| 证据增量 | `general_agent_graph.py:1339` `_evidence_fingerprints(results)` | “本轮新增 3 条证据” / “本轮无新增证据” |
| 停止原因 | `:1355 no_new_evidence`、`:1363` 最终综合、`turn_status` | “连续两批无新证据，转入综合” |
| 需补步骤 | `:1308-1319 required_tool_groups` | “用户明确要求的步骤尚未尝试” |

⇒ **可行路线：从执行事实生成公开说明（Option A）**，不是从模型生成。示例（模板 + 事实填充，不含任何用户/私有数据）：

```
分析 · 需要一个能核对来源的检索步骤，先查本地语料与公开来源
执行 · 本地检索（8 条候选 · 19.7s · 已完成）
分析 · 本地候选与问题相关，但缺少来源核验，继续联网核对 DOI
执行 · 文献核验（DOI 未查到 · 4.2s · 未完成 · 原因：核验服务不可达）
分析 · 来源无法核验，回答中将标注该限制，不声称证据完整
```

这些行的每个数字、每个状态都有真实来源，可被 `/messages` 回放复核。

## D. 如果做不到，具体受限在哪？

| 限制 | 事实 | 能否绕开 |
|---|---|---|
| Provider 在 `tool_calls` 时返回空 `content` | 实测 4/4 批次为空 | 不能从该字段取；换模型/换 provider 需重新实测，不得先假定 |
| 非流式调用 | `_call_llm` 一次性返回 | 改成流式是**执行链路改动**，风险高，且即使流式，空 content 仍为空 |
| `reasoning_content` 未拿到 | `llm_pool.py:24-61` 有补丁，但落库 `reasoning` 全空 | 即使拿到也不应展示（私有推理） |
| 无公开说明的渲染位 | `app.js:2189-2220` 只有工具行/答案两种实体 | 需新增第 3 类行（最小改动） |

## E. 如何避免泄露私有实验方法、文件正文、API Key 或工具原始参数？

- 只允许**结构化白名单字段**进入公开说明：工具 display_name、`action_hint` 的长度受限版本、status、reason_code、elapsed_ms、证据条数、步骤序号。
- **禁止**：`ToolMessage.content` 原文、检索命中片段、附件正文、DOI/路径以外的原始参数、任何 `sk-…` 形态串（本次实跑已出现 `Your api key: ****db23` 这类文本，说明错误串里可能带 key 片段 ⇒ 公开说明必须只取 `reason_code`，**不得透传工具错误原文**）。
- 生成侧不调用模型 ⇒ 不存在“模型把私有内容写进公开说明”的通路。

## F. 工作说明生成失败时，如何保证 Agent 正常执行？

- 生成放在**旁路函数**里，`try/except` 包裹，失败只丢这一行（与 `progress_bus.emit_progress` 的 `QueueFull → pass` 同语义）。
- 展示层绝不参与决策：`required_tool_groups`、`stagnant_batches`、`turn_status` 的判定输入必须是工具执行记录，不是事件是否送达（现状已满足：`turn_status` 由 `tool_status_records` 推导，`main.py:1215-1221`）。
- 验收方式：人为让 `emit_public_note()` 抛异常，验证工具仍执行、答案仍生成、`turn_status` 不变。

## G. 现有 SSE 与历史记录是否足够？

不够，缺口明确且很小：

| 缺什么 | 现状 | 最小增量 |
|---|---|---|
| 事件类型 | 无 `public_note` | 新增 1 类，payload `{turn_id, seq, text, source, tool_call_id?}` |
| 落库位 | `messages.reasoning` 语义是私有推理，不可复用 | `messages` 加 `public_note` 列（PRAGMA 守卫迁移，参照 `manager.py:196-243`）或单独表 |
| 前端行实体 | 只有工具行/答案 | `appendTerminal` 旁加 `appendNote`，`restoreSessionHistory` 同步重建 |
| 顺序保证 | 事件已带时间，但无显式 `seq` | 用 `turn_id + 自增序号`，前端按序插入即可 |

## 结论与建议（供决策）

1. **Option A（推荐，本期做）**：确定性公开说明，零模型调用、零泄露面、零执行耦合。缺点是需要打磨文案模板，避免“机械感”（用“事实 + 下一步意图”两段式，并随结果分支）。
2. **Option B（后期评估）**：提示模型在调用前先写一句公开计划。**当前实测 content 为空 ⇒ 不可靠**，且与“不得编造”冲突，暂不作为主路径。
3. **Option C（不建议）**：为每步单独发一次小模型请求写说明。增加延迟与成本、引入编造面与新的隐私出口。

一句话取舍：**先让用户看见真实发生的事（A），再谈让模型解释它为什么这么做（B）。**
