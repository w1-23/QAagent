# S2 交付：动态公开工作说明（assistant_progress）

日期：2026-10-30　阶段：S2（在 S1 取消生命周期之后）　生产基线：`ff58b1f`（本阶段未部署）

## 1. 修改目的

让模型在**执行多步骤任务的过程中**用一到三句自然语言说明"现在在做什么、为什么、发现了什么"，
并以独立事件 `assistant_progress` 送到前端时间线；它与最终回答严格分离，不进入 `text` 流、不经
`reasoning_content`、不增加模型调用。目标是把执行过程从"只有工具名和耗时"升级为"工具名 + 意图"。

## 2. 实际修改文件与行号

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `src/core/public_progress.py` | 新文件，127 行 | `public_progress_text()` 过滤+截断；`emit_public_progress()` 发事件；`reset_emitted()` 逐请求去重；`_ENV_FLAG = "CITRUS_PUBLIC_PROGRESS"` 总开关；`MAX_CHARS = 400` |
| `src/graph/general_agent_graph.py` | `:32-33` | 导入 `emit_public_progress / public_progress_text / reset_emitted` |
| 同上 | `:1166` | 每请求开始时 `reset_emitted()`（去重状态不跨请求） |
| 同上 | `:1250-1253` | 每轮缓冲 `_round_text_chunks`，第 4 个参数 `on_text=_round_text_chunks.append` 接住流式增量 |
| 同上 | `:1347` | 该轮结束后统一分类：`emit_public_progress(public_progress_text(chunks, bool(calls)))` —— **有工具调用的轮次**才是工作说明，最后一轮（纯回答）不发 |
| `src/api/main.py` | `:1200, :1211, :1368, :1459-1462, :1773` | 删除 `process_graph` 内层嵌套 `import`（它把 `get_request_journal` 变成局部名，导致 `:1368` 抛 `UnboundLocalError`，首次 `await` 之后立即触发） |
| `web/assets/app.js` | `:2111-2125` | 新增 `_appendProgressLine()`：复用已有时间线行，`className='term-line is-progress'`，按文本去重 |
| 同上 | `:2295` | SSE 分发新增 `case 'assistant_progress'` |
| `web/assets/app.css` | `:74-84` | `.term-line.is-progress` 与 `.term-tag` 样式（与工具行区分、弱化） |
| `src/prompts/source/16_tool_usage_file_rules.md` | 新增「## 工作说明（公开进度）」段 | 约束：一到两句、必须对应当前任务与已取得事实、不得预告结果、不得复述隐藏推理、简单问题不必输出；不出工具协议/DSML/凭据 |
| `src/core/test_public_progress.py` | 新文件，149 行 | 12 例 |
| `src/core/test_shadowed_imports.py` | 新文件，78 行 | 1 例，防同类"函数内 import 遮蔽外层名"缺陷复发 |

## 3. 变更前后行为

| 场景 | 变更前 | 变更后 |
| --- | --- | --- |
| 多步骤任务进行中 | 只有工具行（`网页搜索 · 651 ms · 获得 8 条网页资料`），用户不知道"为什么搜、搜完发现了什么" | 工具行之间插入一行工作说明，例如"先查公开来源里黄龙病传播途径的证据，同时检索本地文献中关于传播途径的记录，然后逐条对照。" |
| 工具失败后 | 只有失败标签 | 模型说明"公开检索通道报鉴权失败，本地文献类别也没有加载成功。我会重试一次"——与实际失败一致，不伪造成功 |
| 最终回答 | 正常 | **逐字节不变**：工作说明走独立事件，`text` 流与 `answer` 均未受影响（实测 `answer_not_polluted=True`、`stream_not_polluted=True`） |
| 简单问答 / 单轮无工具 | — | 不发工作说明（`has_tool_calls=False` → 空串），避免寒暄也刷进度 |
| 模型隐藏推理 | 不外泄 | 仍然不外泄：只取样例的公开文本增量，`reasoning_content` 不进入事件 |
| 刷新页面后 | — | 工作说明为**实时事件，当前不持久化**（见 §6 风险）：刷新后历史里只保留工具行，直到 S5 会话恢复一并处理 |

## 4. 测试

- 新增 `src/core/test_public_progress.py`：12 例（发送与字段、无工具调用不发、空/纯空白不发、去重、超长截断、`[TOOL_...]`/协议片段被丢弃、关闭开关不发、无请求上下文时不抛异常等）。全部通过。
- 新增 `src/core/test_shadowed_imports.py`：1 例，扫描 `process_graph` 是否存在内层 import 遮蔽外层符号（防止本次 `UnboundLocalError` 复发）。
- **全量套件：208 passed / 0 failed**（S1 结束时 195，本阶段 +13；基线 `ff58b1f` 为 179）。

## 5. 真实输入与结果

探针：`experiments/trace/probes/probe_public_progress_e2e.py`（本地实例 `127.0.0.1:8010`，真实模型）
输入：`柑橘黄龙病的传播途径和防控要点`。产物：`experiments/trace/runs/20261030/probe_public_progress_on.json`。

实测事件序列（节选）：
```
job → assistant_progress → tool_call_start(网页搜索) → tool_call_start(本地检索)
    → tool_result → tool_result → assistant_progress
    → tool_call_start(网页搜索) → tool_call_start(本地检索) → tool_result ×2
    → tool_call_start(本地检索) → tool_result → tool_call_start(读取网页) → tool_result
    → heartbeat → tool_call_start(读取网页) → tool_result → heartbeat
    → tool_call_start(导出语料清单) → tool_result → text → done
```
两条工作说明原文：
1. `先查公开来源里黄龙病传播途径的证据，同时检索本地柑橘文献中关于传播途径的记录，然后逐条对照。`（46 字符）
2. `公开检索通道报鉴权失败，本地文献类别也没有加载成功。我会重试一次，看是否有可用通道。`

六项硬断言全部为真：
```
answer_not_polluted: true          stream_not_polluted: true
progress_precedes_following_tool: true  tools_actually_ran: true
turn_completed: true               progress_has_no_protocol: true
```
即：工作说明**先于**它描述的那批工具出现；最终回答与流式文本未被污染；说明里没有协议片段。

### 成本对比（诚实口径）

同一问句两次运行（A=关闭工作说明，B=开启）：

| | 模型调用 | 输入 token | 输出 token | 总耗时 | 工作说明条数 | 回答长度 | 终态 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A 关闭 | 6 | 51,102 | 3,835 | 34.0 s | 0 | 2,128 字符 | partial |
| B 开启 | 4 | 30,640 | 2,154 | 19.3 s | 1 | 1,287 字符 | partial |

**这两组数字不能用于证明"开工作说明更省"**：两次运行的失败路径不同（A 那次通道鉴权失败更早、重试更多），
调用次数与 token 差异来自运行波动，不来自本功能。可证明的是**结构上零额外调用**：
`src/core/public_progress.py` 内 `chat(` / `stream_llm_response` / `requests.` / `async_client` / `llm`
的出现次数均为 **0**；它只消费本轮模型已经产出的文本增量，再按字符上限截断转发。

## 6. 风险与限制

1. **历史不持久化**：工作说明是实时事件，刷新/重进会话后不显示。若认为这是缺陷，需在 S5（会话恢复）里把
   `assistant_progress` 一并落库，本阶段按"初期可不持久化"处理。
2. **模型可能不输出**：提示词是软约束，实测本轮第二条说明出现在错误发生后（符合预期），但也会出现整轮不说明的情况；
   前端与后端都不会因此报错（无说明就是没有这一行）。
3. **文案质量取决于模型**：过滤只能拦截协议片段、`[TOOL_...]`、空串与超长，不能保证每句话都"有价值"。
4. **与 `text` 的边界靠轮次分类**：分类依据是"该轮是否产生工具调用"。若模型在**同一轮**既说"我已完成"又继续调工具，
   这段文字会被当作工作说明并留在时间线上（不会进最终回答），属于可接受的口径。

## 7. Git

本阶段提交见 `git log`（提交信息：`S2: public working notes as a separate assistant_progress event`），
父提交为 S1 的 `1c548cc`。**未 push、未部署**。

## 8. 部署建议

1. `CITRUS_PUBLIC_PROGRESS=0` 可一键关闭，无需回滚代码。
2. 部署顺序：先 `pytest src -q`（应 208 passed）→ 重启服务 → 用探针跑一次 → 人工确认前端时间线出现工作说明行。
3. 生产仍为 `ff58b1f`，本次改动需与其他阶段一起做一次隔离实例复测后再上线。

## 9. 回滚方法

- 代码：`git revert <S2 commit>`；或仅设 `CITRUS_PUBLIC_PROGRESS=0`（前端遇到未知事件已忽略，旧前端不会报错）。
- 数据：无表结构、无迁移、无持久化字段，无需回滚数据库。

## 10. 下一阶段

**S3：文件与会话跳转闭环**（真实持久化关联键、跳转到具体消息、历史分页、刷新/重连一致性），
按用户要求先给函数级最小改动清单与测试清单，再实施。
