# 提示语清单（本轮"完成/未完成"话术总表）

生成时间：2026-10-30　代码基线：`src/core/turn_notices.py`（新增，**唯一来源**）

## 为什么之前"像是硬编码、不像思考链"

`src/graph/general_agent_graph.py` 里有 **26 处** `本轮未完成：…` 字面量（本文件改前统计），
分布在三类位置：

1. **上游错误翻译**（`_model_failure_notice` / `_tool_failure_notice`，约 14 条）；
2. **流程中止**（时限、工具轮次上限、无证据、输出截断，4 条）；
3. **必做步骤未达成**（`required_steps_failed` / `required_steps_missing`，2 条）。

它们**不是模型输出**，是代码在失败路径上直接拼的句子，所以：措辞改不了、口径不一致
（同样"网络超时"，回答里一句、trace 里一句、`turn_status` 又按第三套规则猜），
而且它会**覆盖**模型自己写的内容 —— 用户看到的"宣布结论"就是这么来的。

修复方式：全部收敛到 `src/core/turn_notices.py::NOTICES`（22 条），调用点改为
`notice("<code>", **fmt)`；`turn_status.py` 用同一个 `INCOMPLETE_PREFIX` 判断是否未完成；
`general_agent_graph.py` 现在 `grep -c 本轮未完成` = **0**。

## 清单（code → 触发条件 → 文案）

| code | 触发条件 | 现在的文案（前缀统一为「本轮未完成：」） |
|---|---|---|
| `model_quota` | HTTP 402 / 响应体含额度、余额、欠费字样 | 模型服务商返回额度或余额不足。请补充额度后重试。 |
| `model_rate_limit` | HTTP 429 | 模型服务商触发了请求频率限制（不是额度不足）。请稍后重试。 |
| `model_unavailable` | HTTP 500/502/503/504 | 模型服务商暂时不可用（HTTP {status}）；这不代表额度不足，请稍后重试。 |
| `model_auth` | HTTP 401/403 或 invalid api key | 模型服务商拒绝了认证。请检查当前所选服务商的 API Key。 |
| `model_timeout` | `TimeoutError` / 响应体含 timeout | 模型请求超时，服务商未返回额度不足信息。请稍后重试或缩小问题范围。 |
| `model_connection` | connection refused / DNS 解析失败 | 无法连接模型服务商，未收到任何额度不足响应。请检查网络或服务商状态。 |
| `model_unknown` | 其余服务商错误 | 模型请求失败，服务商未给出可判定的原因。请检查服务连接后重试。 |
| `tool_quota` | 工具结果含额度不足 | 检索或模型服务返回额度/余额不足。请补充额度后重试。 |
| `tool_not_configured` | 工具结果含 not configured | 联网 Search/Fetch 没有执行：服务器尚未配置该服务的 API Key。请管理员配置后重试。 |
| `tool_rate_limit` | 工具结果含 http 429 | 检索或模型服务触发了频率限制（不是额度不足）。请稍后重试。 |
| `tool_auth` | 工具结果含 http 401/403 | 检索或模型服务认证失败。请管理员核对对应服务的 API Key。 |
| `tool_unavailable` | 工具结果含 5xx | 检索或模型服务暂时不可用；服务错误不等于"没有检索结果"。请稍后重试。 |
| `tool_timeout` | 工具结果含 timeout | 检索或模型请求超时，未收到额度不足响应。请稍后重试或缩小范围。 |
| `local_search_failed` | 本地 BM25/dense/rerank 链路报错 | 本地检索链路报错；这不等于本地语料没有命中。请稍后重试。 |
| `web_tool_failed` | 联网检索/网页读取报错（兜底） | 联网检索或网页读取报错；这不等于没有相关网页结果。请稍后重试。 |
| `step_unavailable` | 模型调用了未绑定的工具（`[TOOL_UNAVAILABLE]`） | 当前步骤没有取得可用结果。已按已有材料继续作答。 |
| `deadline` | `stop_reason=request_deadline` | 本次请求已达时限，未能形成完整回答。请缩小问题范围后重试。 |
| `tool_turn_limit` | `stop_reason=tool_turn_limit` | 工具调用达到 {limit} 轮上限，未能形成完整回答。请缩小问题范围后重试。 |
| `no_evidence` | 工具跑过但没证据 | 工具已执行但没有取得可用证据，未能形成完整回答。{detail} |
| `output_truncated` | 命中 `completion_budget` | 输出在长度上限（{budget} tokens）处被截断，回答不完整。请缩小问题范围后重试。 |
| `required_steps_failed` | 明确要求的步骤调用了但失败 | 以下明确要求的步骤已尝试但未成功：{names}。{detail} |
| `required_steps_missing` | 明确要求的步骤一次都没调用 | 以下明确要求的步骤未实际调用：{names}。 |

## 改前的原文对照（只列被替换掉的差异，供审阅语气变化）

| 旧文案 | 问题 | 新文案 |
|---|---|---|
| 本轮请求超时，未检测到额度不足错误；请稍后重试或缩小问题范围。 | 没有统一前缀 → `turn_status` 认不出"未完成"，可能被判 completed | `model_timeout`（带前缀） |
| 本轮模型请求失败，服务商未返回明确的额度不足信息；请检查服务连接后重试。 | 同上 | `model_unknown`（带前缀） |
| 本轮未完成：…请缩小问题范围或在追问中指定下一步。 | 对"轮次上限"的解释过窄 | `tool_turn_limit`（说明是轮次上限） |
| 本轮未完成：…请换个更具体的问法，或指定数据来源。 | 与"模型截断"混在一起 | 拆成 `no_evidence` 与 `output_truncated` |
| `[TOOL_UNAVAILABLE] 当前步骤暂未完成。请根据已有材料继续作答…` | 一句话两种意思（工具不存在 vs 结果不可用） | `[TOOL_UNAVAILABLE] ` + `step_unavailable` |

## 仍未收敛的部分（下一步）

- `src/research/tools.py:1548`「公开补充材料获取未完成：{异常名}」——属工具级结果说明，
  已带"不把网络失败当文献不存在"的口径，暂留在原地；若要彻底单一来源，应迁到 `turn_notices`。
- 工具执行态的短标签（`complete / partial / no_results / failed / skipped / not_run`）在
  `progress_bus` 与前端 `_toolStates` 各有映射，属于"状态"而非"提示语"，本轮未动。
