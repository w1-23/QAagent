# CitrusScholar 真实 Agent 实验执行入口

版本：`4.4.0`
状态：`AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
本轮执行：`NONE`
正式实验：`NOT_STARTED`
门禁：`G01=BLOCKED`，`G02-G15=PENDING`，`PASS=0`

本文件是实验执行入口；本轮只更新准备内容，不启动服务器、不注册账号、不调用
Agent/Judge、不运行正式题，也不生成任何实验结果。

## 1. 权威原则

1. CitrusScholar 必须作为真实农业科研问答系统测试，包括真实语料检索、
   文献阅读、引用、工具调用、SSE、产物和长工作流。
2. 题目可作为正常输入直接发送给 Agent。
3. Agent 可通过平台正常访问完整语料；`chunk_id`、`chunk_text`、来源标题和
   检索摘录出现在正常检索结果中不是泄漏。
4. `CITRUS_EXPERIMENT_L1_ROOT` 不是 Agent 测试前置条件。L1 只用于未获再
   分发许可材料的服务器留存/发布分级。
5. 语料全文不复制到 GitHub；GitHub 保存代码、协议、来源登记、generation、
   SHA、脱敏证据、评分、分析和论文材料。
6. 独立题一题一会话；不同条件、重复运行使用不同会话；长工作流单独建会话并
   在会话内完成连续任务。
7. 真实请求、SSE、工具轨迹、检索命中、后台日志、回答、引用、产物、失败和
   重试均保留，并以 `request_id`/`session_id` 串联。
8. 模拟 fixture 只测试接口，不进入正式证据、分母、Results 或论文。
9. 聊天中暴露过的旧 Key 不得使用。新密钥只放在服务器 Git 工作树之外的
   `EnvironmentFile` 或等价安全环境。

## 2. 当前复核入口

1. `AUDIT_INDEX.md`
2. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4.md`
3. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
4. `00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md`
5. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
6. `01_preflight/SERVER_API_PROBE_PLAN.json`
7. `scripts/A00_execution_readiness/README.md`
8. `01_preflight/gates.csv`

## 3. 经审查后的执行顺序

### Stage 0：环境、语料和观测条件

- 锁定实际部署 Git commit/tree 和文件 SHA；
- 检查 `/health`、独立服务用户、数据库、会话、缓存、端口和日志；
- 确认真实注册语料、索引 generation 和来源 SHA；
- 确认测试账号能通过正常接口访问五类语料；
- 确认请求、SSE、检索、工具和后台日志的落盘位置。

### Stage 1：真实服务器和 Provider

- 注册专用测试账号并登录；
- 调用 `/api/v2/auth/me` 和 `/api/v2/config`；
- 真实调用阿里云百炼 Agent；
- 真实调用 OpenCode Go 的 GLM/MiMo/MiniMax；
- 保存实际模型、request ID、usage、cost、allowance、状态、耗时和 SHA。

### Stage 2：真实端到端题

- 新账号会话发送一个真实题目；
- 由 CitrusScholar 正常检索、读取原文、调用工具和生成回答；
- 捕获 SSE、检索结果、工具轨迹、后台日志、回答、引用和产物；
- 核对 Agent 原始答案、最终答案、历史记录和 `done.answer`；
- 运行 `20_audit_agent_execution_evidence.py`；
- 通过后再做多工具、长工作流、Pilot 和正式实验。

## 4. 服务器凭证

服务器环境文件至少使用：

```text
CITRUS_EXPERIMENT_BASE_URL
CITRUS_EXPERIMENT_EVIDENCE_ROOT
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
CITRUS_PROBE_USERNAME
CITRUS_PROBE_PASSWORD
CITRUS_EXPERIMENT_ENV_FILE
```

目录权限 `700`，文件权限 `600`。密钥、密码、Cookie 值和 Authorization
值不得出现在命令行参数、日志、JSON、CSV、截图、专家包或 Git。

## 5. 当前停止点

- 本轮只提交准备修订，等待外部 GPT 读取 GitHub 确认；
- 旧 Stage 0–2 报告保留为历史证据，但不能覆盖 v4.4.0；
- SSH/`8012` 最后一次实际尝试仍超时，必须在审查后重新实测；
- L1 root 缺失不再是阻塞；
- 旧 Git 历史中的 `chunks_FROZEN.jsonl` 已核实含 60 条真实 chunk 正文，
  只公开对象 SHA、长度、记录数和来源统计，不在新文件中重复正文；
- 未调用任何 Agent/Judge、未启动 Pilot/正式实验、未生成评分或结果。

审查完成后，从 GitHub 最新 `main` 在服务器检出明确 commit，按 Stage 0 →
Stage 1 → Stage 2 → 多工具/长工作流 → Pilot → 正式实验推进；每个工作单元
保存证据、提交 GitHub、回报 SHA，再暂停复核。
