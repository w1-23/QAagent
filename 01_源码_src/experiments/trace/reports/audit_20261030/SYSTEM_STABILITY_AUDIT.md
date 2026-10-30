# 系统稳定性审计：风险清单与证据

基线：本地仓库 HEAD `80bab4b`（Phase 2A 已冻结，**未部署到服务器**）。
服务器 `/home/WZQ/CitrusQA` 仍是 Phase 1 之后、2A 之前的代码与**重建后的新语料**；本地是 2A 代码 + **旧语料**。

标注：[已证实] 有代码路径与日志/实跑证据 · [潜在风险] 静态分析、未复现 · [已验证正常] 有测试或实跑 · [尚未验证] 证据不足。

---

## 1. Agent 自主性：`filter_visible` 是否真接入主链路？还有没有关键词控工具？

**接入是真接入。[已验证正常]**

- 绑定点：`01_源码_src/src/graph/general_agent_graph.py:1056-1060` → `_scoped_toolset(...)`
- 实现：`:393-431`，内部 `runtime_env(...)` + `available_tool_names(env)`（`src/graph/tool_availability.py`）；`save_research_note` 仅作**授权桥**保留在 `denied`（`:420-423`），不是能力猜测。
- 反向证据：`src/graph/test_tool_visibility.py`（12 例）断言“健康环境下 17 个工具里仅 `save_research_note` 被扣留”，且“12 字短问题不再丢两个检索工具”。

**但关键词仍影响可见性（不是按工具逐个开关，而是三处整体策略）：**

| 残留 | 位置 | 影响 |
|---|---|---|
| `resolve_tool_policy(query, scope, paper_only=...)` → `allow_retrieval` / `deny` | `src/graph/tool_policy.py:57-67`、调用点 `general_agent_graph.py:419`、`:1019` | 若文本被判为“无需检索”，`RETRIEVAL_TOOLS` 整组从可见集合消失 |
| `_source_scope(query)` | `general_agent_graph.py:1013` | 用户显式说“只查本地/只查联网”时收窄 —— 合理，但仍是文本判定 |
| `required_tool_groups`（`_explicit_*` 系列） | `:1002-1055`，`:1062-1063` 按可用性过滤 | 只用于“这轮是否算完成了用户明确要求的步骤”，**不决定可见性** |

[潜在风险] `allow_retrieval=False` 的触发边界未实测。若某类正常科研问题（例如“帮我写一段实验方案讨论”）被判成不需检索，检索工具会整组消失，用户看到的现象与“关键词控工具”一样。→ 建议补一条静态测试 + 5 个真实问题抽样实测。

## 2. 重复调用与超时：`auth` 失败为什么连调三次？

**没有发生执行器级重试循环。[已证实]**

- 三次网页搜索的**参数并不相同**（真实采集，`experiments/trace/runs/20261030/R4/trace.jsonl`）：
  `10.19415/j.cnki.1673-890x.2025.09.035` → `"1673-890X" 南方农业 2025 第9期 柑橘` → `南方农业 2025年第9期 1673-890X 目录`
  ⇒ 去重签名 `sha256([name, args])`（`:880-889`）不命中是**正确行为**，是模型在改换检索式。
- 实际生效的守卫：批上限 `_MAX_TOOL_CALLS_PER_BATCH = 8`（`:50`）、无进展熔断 `_NO_PROGRESS_BATCHES_TO_STOP = 2`（`:48`，R4 正好在第 3 批停下）、补齐上限 `_MAX_SOURCE_ENFORCEMENT_ATTEMPTS = 2`（`:51`）、请求 deadline（`:1010-1011`）。

**真正的缺陷在“失败语义不回灌”：[已证实缺陷 · 中]**

- `src/tools/outcome.py:109-125` 用**字符串嗅探**判定：`401/403/unauthorized/认证` → `auth`。这是展示标签。
- 该标签**没有任何下游消费者**：不进模型提示、不进重试预算、不进 `stopped` 决策。
- 后果：模型看到的是 `[WEB_TOOL_ERROR] HTTP 401 …`，会合理地换词再试；人类看到的却是“同一个搜索连失败三次”。
- 修法（提示词/文案级，不动算法）：工具终态里对 `auth / configuration_missing / quota` 附加一条人类可读的**终止性说明**（“该失败在本轮重试无效”），并让 `required_tool_groups` 把这类失败视为“已尝试”。预计 < 30 行。

**超时文案**：`:1403` 在截止时间到达时把整轮回答替换为固定文案；这是既有设计，本次未见误报（R1/R3/R4 的 `turn_status` 与真实工具状态一致）。

## 3. 执行与展示隔离：SSE 或前端异常能否影响执行？

**异常层已隔离，生命周期层未隔离。**

[已验证正常] 异常隔离：
- 工具调用点发布事件整段 `try/except Exception: pass`（`general_agent_graph.py:894-898`）
- 队列满即丢：`progress_bus.py:106-112`（`except asyncio.QueueFull: pass`），不抛不阻塞
- 请求级队列用 ContextVar（`progress_bus.py:22-50`），多请求不共享队列
- 轮次状态由工具执行记录推导，不看事件是否送达：`main.py:1215-1221` + `src/core/turn_status.py:29 derive_turn_status()`

[已证实缺陷 · **高**] 生命周期耦合：
- `main.py:1456-1467`：客户端断开 → 请求任务被取消 → `jobs.update_job(status="cancelled", error="client_disconnected_while_queued")` → `raise`；设计注释在 `main.py:293`。
- 意味着：**关页面/刷新/网络抖动会终止一整轮科研任务**，落库为 `cancelled`，用户重进看到的是“已取消”，而不是“跑完等你回来”。
- 对一个会跑 10–30 分钟的实验设计或数据审查任务，这是可观测的损失，且用户很难归因。
- 修法需要产品决策：断开后任务继续（detached run）+ 重连时补推历史，工作量中等，且要新增“运行中任务”入口。**本轮建议先把它当成已知限制写进文档，不在 2B 之前动。**

## 4. 数据与权限：Phase 1 的文件关联、请求上下文、迁移是否安全？

[已验证正常]
- 请求上下文：`client_request_id` 缺省自动生成（`main.py:967-968`）并写入 contextvar（`:970-974`），幂等键 `compute_idempotency_key`（`:977`）。
- 产物与轮次绑定：**登记时立即绑定**，不等轮末——`src/research/tools.py:334-339` 传 `get_client_request_id()`，`src/session/files.py:387-395` 写 `session_file_turn_refs`。
- 迁移安全：全部 PRAGMA 守卫、可重入——`files.py:111-124`（`client_request_id` 列 + `session_file_turn_refs` 表，PK `(session_id, client_request_id, file_id)`）、`src/session/manager.py:196-243`（`messages.execution_json`、`session_evidence`、`sessions` 三处）。
- 悬空引用：`46849c6` 已修“文件删除后 turn ref 仍存活”。

[技术债 · 低] `files.link_turn_files()`（`files.py:703`）**只有测试调用**（`src/session/test_file_turn_refs.py:177`），生产链路零调用。不是 bug（登记时已绑定），但它是一个“看起来在工作、实际没人用”的函数，容易误导后续改动。建议删除或标注为迁移工具。

[尚未验证] 并发：两个账号同时上传同名文件、同一会话两轮并发请求时的 `turn_refs` 隔离，没有对应测试；`daily_usage` 与 `release_chat_request` 的配额并发也只做了静态审查。

## 5. 动态公开工作说明

见 `PUBLIC_PROGRESS_FEASIBILITY.md`。一句话：**当前没有公开文本可复用（实测 content 为空）**，建议走“从执行事实生成”的确定性路线。

## 6. 其他维度速查

| 维度 | 结论 | 证据 |
|---|---|---|
| 权限与安全 | [已验证正常] 带 cookie 的请求强制同源 `Origin`（`main.py:247-252`），否则 403；会话归属在校验中间件集中处理（`:269`） | 本次 probe 不带 `Origin` 时确实 403，加上后 200 |
| 隐私 | [潜在风险] 工具错误原文含 key 片段（实测出现 `Your api key: ****db23 is invalid`）。当前是掩码，但**公开说明与前端摘要都不得透传原始错误串** | `R1/trace.jsonl` tool row |
| 日志 | [尚未验证] `logger` 是否会把完整请求头/Key 落盘，未逐条核对 | — |
| 版本一致性 | [已证实缺陷 · 中] 本地 2A 代码 + 旧语料，服务器 1.B 代码 + 新语料 ⇒ 本地跑出的检索结论不可迁移到生产，反之亦然 | 本地 11 批次全部 `quarantined`（缺 `source/source_type/source_file`、`unknown topic/category: 'laboratory protocol'`） |
| 部署运维 | [已验证正常] 服务器无 systemd 单元，手工重启约 17s 起 200；`rw_exec` 超时会连带杀子进程，需 `setsid nohup … & disown` | 见 `NOTICES.md` |
| 测试覆盖 | 见 `TEST_COVERAGE_AND_GAPS.md` | 173 passed / 0 failed |

## 7. 建议修复顺序（按“会不会毁任务”排序）

| 优先级 | 事项 | 类型 | 预估 |
|---|---|---|---|
| **P0** | 断线即取消长任务（生命周期耦合）——先出产品决策，再动代码 | 行为变更 | 中 |
| **P0** | 本地/服务器代码与语料版本不一致，导致任何检索结论不可互相迁移 | 流程 | 小（对齐 + 记录） |
| **P1** | `auth` 类失败附加“本轮重试无效”语义，并计入“已尝试” | 文案/提示词 | < 30 行 |
| **P2** | `allow_retrieval=False` 触发边界抽样实测 + 静态测试 | 测试 | 小 |
| **P2** | `link_turn_files` 死代码清理或标为迁移工具 | 技术债 | 极小 |
| **P3** | 动态公开说明（Option A） | 功能 | 中 |
| **P3** | 并发隔离测试（同名文件、同会话并发轮） | 测试 | 小 |

**不要在本轮动的**：Agent 主循环结构、`MAX_TOOL_ROUNDS`、去重签名算法、SSE 转发桥。它们都有实跑证据支撑现状，改动收益低于回归风险。
