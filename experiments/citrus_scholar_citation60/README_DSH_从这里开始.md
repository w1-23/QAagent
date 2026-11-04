# CitrusScholar 论文实验执行总入口（交给 dsh）

**v4.2 当前状态：`AWAITING_EXTERNAL_GPT_REVIEW`。** 审查通过前不执行服务器实验、测试或模型调用。实验和所有数据处理必须在服务器独立实例 `/home/WZQ/citrus_experiment/` 完成；本地只编辑/审查文档和源码。GitHub 保存完整可复现实验包，但不含密钥、Cookie、个人认证信息或未授权材料。G01-G15 均 `PENDING`；正式实验尚未开始。

以 `00_protocol/DECISION_ADDENDUM_2026-11-04_JUDGE_MODELS_OPENCODE_GO.md`、`EXPERT_SCORING_PROTOCOL.md`、`SERVER_EXECUTION_PROTOCOL.md` 和 `EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-04.md` 为本阶段权威决策。每完成一个服务器工作单元，保存证据、服务器验收、推送 GitHub、报告 SHA 后暂停等待 GPT 审查。当前 SSH 28022 曾超时，须重新核验；不可达时 G01 和服务器工作均保持阻塞。

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
10. 核心问答由两位专家独立评分并对任一四维不一致项由第三专家仲裁；GLM/MiMo 自动独立评分，四维分歧时由 MiniMax 仲裁，作为单独自动测量。补充 citation60 只做确定性 target chunk 命中。评分口径见 `07_scoring/rubric.md` 和专家规程，不得报告 semantic support。
11. 按 `08_workflows/WF01...WF07` 做独立端到端案例。**WF07 是重头戏**：UCR 两品种资料 + D10 柑橘木虱采样论文 + 模拟柑橘叶片 qPCR 原始表 + DOI 核验 + 标注 Excel + 报告 / 可选 SVG；必须识别“木虱检测研究不能直接当叶片验证SOP、品种属性不等于抗病性、未检控污染不能判阳性”的边界。无成功产物则诚实记失败。
12. 全部专家/机器评分和仲裁到位后，在服务器运行 `python scripts/analyze_and_plot.py --strict`。只从**真实 raw run、两份专家原始评分、专家仲裁、LLM 原始响应和已记录仲裁**计算，写入 `09_analysis/tables/` 和 `10_figures/`；若数据缺失，脚本必须失败关闭。
13. 先按 `00_protocol/05_论文写作执行计划.md` 和 `11_manuscript/storyline_and_writing_plan.md` 写方法、系统、工作流、事实性结果，再填 `11_manuscript/manuscript_skeleton.md` 的 Abstract/Discussion；所有数字登记到 `claim_source_map_TEMPLATE.csv` 和 `table_figure_provenance_TEMPLATE.csv`。披露所有数据缺失与局限。发布前执行 `12_reproducibility/release_checklist.md`。

所有包检查、dry-run、测试和正式运行只在服务器独立实验实例执行。合成 dry-run 必须带
`SIMULATED_DRY_RUN` 标记，永远不进入论文 Results，也不能替代真实运行。每个服务器工作单元
完成后将证据/产物从服务器推送 GitHub，再暂停等待 GPT 审查。

## 注意三条硬边界

- 平台**不是 HAIant 的复刻**。只借鉴“研究痛点→架构→意图/能力验证→端到端生物科研案例”的论文叙事。HAIant DOI `10.1016/j.molp.2026.08.011`，具体数字及方法必须读其原文/补充材料才能引用。
- `B1 simple RAG` 对比必须公平；B0 在附件题缺少文档的结果只能作无资料诊断，不可据此证明平台算法优越。
- 正式问答主指标来自核心 QA60 中 52 道可答题、五层等权宏平均 EGTS；8 道不可答题单列，主评分为两位专家共识（两位独立评分，第三位专家仲裁分歧），LLM 判官作为独立对照。补充 citation60 的确定性 target chunk 命中是独立终点。

## 服务器执行命令（仅 GPT 审查通过、服务器实例验证后）

```bash
python scripts/check_package.py
python scripts/preflight.py --repo /path/to/QAagent --out 01_preflight/preflight_observed.json
python scripts/make_run_plan.py
python scripts/build_scoring_sheet.py
python scripts/protocol_lock.py --check
python scripts/download_sources.py --dry-run
python scripts/run_matrix.py --dry-run --kind qa
python scripts/analyze_and_plot.py --strict   # 在真实完成评分前应拒绝出结果图
```

所有脚本均以这个 ZIP 解压后的根目录为工作目录计算路径。`--help`可查看参数。联网下载以及正式运行**默认关闭，需要显式 `--execute`**。
