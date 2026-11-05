# CitrusScholar 论文实验执行入口（交给 dsh）

版本：`4.3.2`
状态：`AWAITING_EXTERNAL_GPT_REVIEW`
执行授权：`NONE`
门禁：`G01=BLOCKED`，`G02-G15=PENDING`，`PASS=0`

本文件是执行入口，不是结果报告，也不代表任何模型或服务器已经连通。

## 必须遵守的边界

1. 正式实验、数据库检查、模型探测、Pilot、评分、统计和出图只在服务器
   隔离实验实例执行；本地只编辑源码/协议、运行纯静态检查和 Git 操作。
2. 每次服务器工作从 GitHub 最新 `main` 的明确 commit 开始。工作树不干净、
   远端 parent 变化或 commit/tree 不一致时停止；禁止 `reset --hard`、force push
   或历史重写。
3. 服务器实验根目录由受控运维配置指定。脚本通过
   `CITRUS_EXPERIMENT_L1_ROOT` 定位受控 `L1`，不得把真实 SSH 地址、端口、
   凭据或私有路径写入公开仓库。
4. Agent 和判官凭据只放在 Git 工作树之外的受保护 `EnvironmentFile` 或等价
   环境中；目录权限 `700`、文件权限 `600`。密钥不进入命令行、日志、请求归档、
   packet、截图或 GitHub。
5. 所有执行入口要求 `status=READY`、`execution_authorized=true` 和已批准审查
   状态。静态测试通过不得替代门禁证据。
6. 每个服务器工作单元完成后：保存原始证据和 SHA、执行服务器验收、提交
   GitHub、报告 commit SHA、测试命令与结果、门禁状态、BLOCKED 原因，然后暂停
   等待 GPT 审查。

## 权威文件

1. `AUDIT_INDEX.md`
2. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_2.md`
3. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_2.md`
4. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
5. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
6. `07_scoring/rubric.md`
7. `07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`
8. `09_analysis/analysis_contract.md`
9. `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`
10. `12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`
11. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
12. `PUBLICATION_SCOPE.md`

## 执行顺序

### WU-00：等待第五轮外部审查

当前只允许审查协议、候选代码、模板、公开边界和离线测试。不得调用 Agent
或 Judge，不得运行 Pilot，不得创建正式评分或结果。

### WU-01：G01 服务器连通与隔离

重新核验 SSH、服务器身份、明确 commit/tree、独立服务用户、端口、数据库、
环境文件、文件权限、生产只读隔离、模型缓存和 GitHub 写入权限。保存命令、
原始输出、UTC、权限和 SHA。任何失败使 G01 保持 `BLOCKED`。

### WU-02：环境与来源锁定

记录实际部署 commit/tree、部署文件 SHA、`/health`、Agent 实际 model ID/版本、
索引 generation、17 工具注册表、语料 manifest、硬件运行时和来源许可。

### WU-03：模型连通性

只使用新轮换且最小权限的临时凭据。分别探测 Bailian Agent 和 OpenCode Go
三个 Judge；记录脱敏 request、完整 safe response、实际 `model`、HTTP 状态、
耗时、usage、attempt 和 SHA。连通只到 `PILOT_READY`，不能直接 `READY`。

### WU-04：离线 schema、盲法与人类评分材料测试

使用合成 fixture 测试 packet、J1/J2/J3、聚合、专家材料、评分导入、修订、
仲裁、共识、agreement 和 strict analysis。合成记录不得进入正式目录。

### WU-05：有效性与公平门禁

完成 120 题双人有效性认证与仲裁；核验 B0/B1/B2 同模型、同来源、同附件文本、
同权限和已审计检索预算。任何来源缺失不得锁定。

### WU-06：独立 Pilot

12 题 × B0/B1/B2 = 36 次；两位专家分别评分 36 份，J1/J2 独立评分，J3 只仲裁
四字段分歧。执行 challenge cases、一致性和 fatal-error 校准。Pilot 不进入正式
分母。

### WU-07：正式协议锁

Pilot 通过并完成 GPT 审查后，冻结模型、prompt、packet builder、评分 schema、
run plan、检索设置、盲映射、分析代码、来源/索引 manifest 和配置 SHA。

### WU-08 起：正式实验

按独立工作单元运行核心 QA60、citation60、Route192、Composite24、
可选 CitationRepeat72 和 WF01-WF07。每个实验单独保存 raw、失败、重试、产物、
SHA、评分和分析；核心与补充不共享分母。

## 评分与统计

- 两名专家独立评分每个语义可评估的核心 QA60 回答；第三名专家逐字段仲裁分歧。
- 专家不填写 PASS/FAIL，脚本按冻结公式计算。
- 唯一确认性比较是 B2 vs B1 的 `egts_system_outcome`，60 个计划槽位/条件。
- `human_consensus_egts` 单独作为语义质量终点，报告实际可评分覆盖率和基础设施
  失败数，不能伪装成完整 60 项语义分母。
- J1/J2 全量评分；J3 只仲裁四字段分歧。模型身份缺失/不匹配或 J3 失败时该 run
  保持 `BLOCKED`。
- citation60 只报告冻结 `target_chunk_id` 命中和失败构成，不报告 semantic
  support，不称为答案正确。

## 论文写作

按 `00_protocol/05_论文写作执行计划.md` 和
`11_manuscript/storyline_and_writing_plan.md` 执行。Methods 必须写清 Agent/Judge
提供方与模型、服务器环境、双专家全量评分、盲法、第三专家仲裁、LLM 自动对照、
agreement 和成本。Results 主表使用 `egts_system_outcome`，语义终点与专家一致性
单独报告。每个数字必须连接 analysis manifest 和 claim-source map。

## 当前停止点

- 仓库历史中旧 citation60 对象仍可从公开 GitHub 读取，等待 PI/仓库所有者决定。
- 服务器连接、隔离和 G01 证据未完成。
- 实际模型身份、来源许可、有效性和 Pilot 未完成。
- 本文件不授权调用模型、启动服务器实验或生成结果。
