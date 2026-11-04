# CitrusScholar 论文实验执行总入口（交给 dsh）

**v4.3 当前状态：`AWAITING_EXTERNAL_GPT_REVIEW`。** 审查通过前不执行服务器
实验、测试或模型调用。实验和所有数据处理必须在服务器独立实例
`/home/WZQ/citrus_experiment/` 完成；本地只编辑/审查文档和源码。GitHub 只保存
通过 release 检查的 `L2` 材料；`L0` 永不进入任何包，`L1` 留在服务器受控存储并
公开 SHA/定位/redaction lineage。G01 为 `BLOCKED`，G02-G15 为 `PENDING`；
正式实验尚未开始。

以 `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`、
`EXPERT_SCORING_PROTOCOL.md`、`07_scoring/rubric.md`、
`09_analysis/analysis_contract.md`、`PUBLICATION_SCOPE.md` 和
`SERVER_EXECUTION_PROTOCOL.md` 为本阶段权威决策。现有判官 Python
脚本、旧 prompt 行为和 `UNCONFIGURED` 配置均禁止执行。每完成一个服务器工作单元，
保存证据、服务器验收、推送 GitHub、报告 SHA 后暂停等待 GPT 审查。当前 SSH
28022 曾超时，须重新核验；不可达时 G01 和服务器工作均保持阻塞。

## dsh 的职责及工作顺序

1. 先读 `00_protocol/00_研究定位和工作包路线.md`、`01_preflight/preflight_protocol.md`、`11_manuscript/storyline_and_writing_plan.md`。**严禁直接跳到运行正式题**。
2. 外部审查通过后，先在服务器验证 SSH、工作树干净状态和明确 commit，再创建/核验独立实验实例；不得对生产实例写入。服务器执行 `python scripts/preflight.py --repo /path/to/QAagent --out 01_preflight/preflight_observed.json`；不能用脚本 PASS 冒充真人端到端检验。SSH 不可达或目录/提交不一致即停止，不 reset、不切换到本地。
3. 检查上一轮审查记录的 SSE 最终回答/历史保存不一致风险。**只有复现后才判问题存在；修复必须经单独批准、再留存回归测试；不能未经授权直接修改服务器代码、服务配置或登录方式。** 不允许在生产库直接进行压力测试。
4. 在服务器检查每道题所需资料。运行 `python scripts/download_sources.py --dry-run` 检查候选网址；下载时按许可和明确 URL 执行，验证 `%PDF`。来源记录时间、许可和 SHA；不得绕过技术访问控制。
5. 运行有效性包构建后，由两名研究生/领域评审独立认证核心 60 题和补充 60 题的有效性。另按 `EXPERT_SCORING_PROTOCOL.md` 生成独立回答评分专家包：两位专家分别评分 Pilot 36 份和正式 180 份回答，第三位专家仲裁四维字段分歧；主结果是专家共识，不制作 Gold Answer。有效性未锁定前不得调正式题 prompt/工具。
6. 先把 `03_benchmarks/intent/intent192_CANDIDATE.csv` 作为路由/动作评测使用；不要声称这是专用“分类器的意图识别准确率”，除非实际存在独立分类器及输出。分类意图与真实工具轨迹分别记录。融合24题作为多意图任务合规度，不能并入单标签准确率。
7. 建立 B0/B1/B2 适配器（参见 `05_baselines/baseline_contract.md`），确保同模型、同文献快照、同附件解析器和 B1/B2 同等检索权限。脚本模板默认拒绝运行，须真实接入可验证模型返回后启用。绝对不要写假结果。
8. 在服务器完成 `01_preflight/gates.csv`，用 `python scripts/protocol_lock.py --check` 查看；仅真实全部 PASS 且负责人批准时才冻结题目、有效性、模型/判官配置、来源哈希和分析口径。脚本不得自行批准。
9. 锁题后在服务器用 `python scripts/make_run_plan.py --locked` 新建随机顺序计划版本，不覆盖历史输入。核心/补充各 180 次，route 192，multi 24；执行器产生不可覆盖逐轮 JSON，失败/超时照实记录。
10. 核心问答由两位专家独立评分，四维字段是 `answerability_correct`、
    `answer_correct`、`evidence_traceable`、`fatal_error`；任一字段不一致由第三
    专家逐字段仲裁。GLM/MiMo 自动独立评分，四维分歧时由 MiniMax 仅仲裁分歧字段，
    作为单独自动测量；模型不得生成最终 decision。补充 citation60 只做确定性
    target chunk 命中。不得报告 semantic support。
11. 按 `08_workflows/WF01...WF07` 做独立端到端案例。**WF07 是重头戏**：UCR 两品种资料 + D10 柑橘木虱采样论文 + 模拟柑橘叶片 qPCR 原始表 + DOI 核验 + 标注 Excel + 报告 / 可选 SVG；必须识别“木虱检测研究不能直接当叶片验证SOP、品种属性不等于抗病性、未检控污染不能判阳性”的边界。无成功产物则诚实记失败。
12. 分开运行 `strict-human`、`strict-judge`、`strict-citation` 和
    `strict-all`。人类主结果可在机器判官故障时独立完成，但 `strict-all` 必须失败
    并披露缺失模块。只从真实 raw run、两份专家原始评分、专家仲裁、LLM 原始响应和
    已记录仲裁计算；缺失时不得填 0 或生成虚假结果。
13. 先按 `00_protocol/05_论文写作执行计划.md` 和 `11_manuscript/storyline_and_writing_plan.md` 写方法、系统、工作流、事实性结果，再填 `11_manuscript/manuscript_skeleton.md` 的 Abstract/Discussion；所有数字登记到 `claim_source_map_TEMPLATE.csv` 和 `table_figure_provenance_TEMPLATE.csv`。披露所有数据缺失与局限。发布前执行 `12_reproducibility/release_checklist.md`。

所有包检查、dry-run、测试和正式运行只在服务器独立实验实例执行。合成 dry-run 必须带
`SIMULATED_DRY_RUN` 标记，永远不进入论文 Results，也不能替代真实运行。每个服务器工作单元
完成后将证据/产物从服务器推送 GitHub，再暂停等待 GPT 审查。

## 注意三条硬边界

- 平台**不是 HAIant 的复刻**。只借鉴“研究痛点→架构→意图/能力验证→端到端生物科研案例”的论文叙事。HAIant DOI `10.1016/j.molp.2026.08.011`，具体数字及方法必须读其原文/补充材料才能引用。
- `B1 simple RAG` 对比必须公平；B0 在附件题缺少文档的结果只能作无资料诊断，不可据此证明平台算法优越。
- 核心 QA60 的唯一主要确认性比较是 B2 vs B1 的 human-consensus EGTS
  （60 题分母），并报告 answerability/60、answer correctness/52、
  evidence traceability/52、fatal error/60；52 可答与 8 不可答分层另报。
  人类主评分来自两位专家独立评分和第三专家字段仲裁，LLM 判官是独立自动对照。
  补充 citation60 的确定性 target chunk 命中是独立终点。

## 服务器执行命令

本 v4.3 审查工作单元没有实现新的 judge/packet/aggregation 或四个 strict
入口，现有脚本仍是 legacy。外部 GPT 批准协议后，必须先提交并审查单独的 v4.3
实现工作单元，再在服务器上使用该实现冻结的命令和 SHA。当前不得用旧命令代替，
也不得因为命令名此前存在就声称功能可用。
