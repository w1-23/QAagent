# CitrusScholar 论文实验执行总入口（交给 dsh）

**本目录名严格保留：`citrusScolarExperiment`。** 公开 GitHub 只同步可发布的协议、题面、脚本、模板和哈希；受限全文、真实原始运行、判官响应、评分、图表和论文过程数据留在受限实验存储。当前打包的是**实验设计/输入模板**，不是实验结果。

## dsh 的职责及工作顺序

1. 先读 `00_protocol/00_研究定位和工作包路线.md`、`01_preflight/preflight_protocol.md`、`11_manuscript/storyline_and_writing_plan.md`。**严禁直接跳到运行正式题**。
2. 确认仓库路径、生产或独立实验环境、候选基线 Git SHA、单 Agent、17 工具和当前用户权限/资源作用域。执行 `python scripts/preflight.py --repo /path/to/QAagent --out 01_preflight/preflight_observed.json`；脚本只读；随后完成 `01_preflight/gates.csv` 中所有必须项及真实附件证据，不能用脚本的 PASS 冒充真人端到端检验。
3. 检查上一轮审查记录的 SSE 最终回答/历史保存不一致风险。**只有复现后才判问题存在；修复必须经单独批准、再留存回归测试；不能未经授权直接修改服务器代码、服务配置或登录方式。** 不允许在生产库直接进行压力测试。
4. 确认每道题需要的目标资料确实收录/可获取。运行 `python scripts/download_sources.py --dry-run` 检查候选网址；需下载时按 `--execute` 使用官方开放 PDF 链接，自动验证 `%PDF`。无法合法下载就记录 `blocked`，而非绕过付费墙。补充资料、UCR 记录快照与私有中文条目只在权限范围内留存。所有正式 PDF 由 dsh 根据清单检查、下载及验 SHA，当前包没有声称已下载公开 PDF。
5. 运行 `python scripts/build_validity_review_packet.py`，由两名研究生/领域评审独立认证核心 60 题和补充 60 题的有效性，仅对分歧仲裁；不制作 Gold Answer。正式题未完成有效性锁定前，不得用于调 prompt/工具。
6. 先把 `03_benchmarks/intent/intent192_CANDIDATE.csv` 作为路由/动作评测使用；不要声称这是专用“分类器的意图识别准确率”，除非实际存在独立分类器及输出。分类意图与真实工具轨迹分别记录。融合24题作为多意图任务合规度，不能并入单标签准确率。
7. 建立 B0/B1/B2 适配器（参见 `05_baselines/baseline_contract.md`），确保同模型、同文献快照、同附件解析器和 B1/B2 同等检索权限。脚本模板默认拒绝运行，须真实接入可验证模型返回后启用。绝对不要写假结果。
8. 完成 `01_preflight/gates.csv`，用 `python scripts/protocol_lock.py --check` 查看；仅真实全部 PASS 时 `--lock` 冻结问题、有效性、模型/判官配置、来源哈希和分析口径。脚本不得自行替人批准。
9. 锁题后用 `python scripts/make_run_plan.py --locked` 重新生成随机顺序计划（覆盖此前仅用于预览的候选运行计划）（包括 B0/B1/B2 共180轮，B2意图192轮，B2多意图24轮；可选预定重复）。运行前 `python scripts/run_matrix.py --dry-run --kind qa`。实际执行按真实 adapter 和 `--execute`，并生成原始不可覆写的逐轮 JSON，失败/超时照实记录。
10. 核心问答由两套冻结 LLM judge 独立评分，分歧交冻结 LLM adjudicator；补充 citation60 只做确定性 target chunk 命中。评分口径见 `07_scoring/rubric.md`，不得伪称人工 Gold 或 semantic support。
11. 按 `08_workflows/WF01...WF07` 做独立端到端案例。**WF07 是重头戏**：UCR 两品种资料 + D10 柑橘木虱采样论文 + 模拟柑橘叶片 qPCR 原始表 + DOI 核验 + 标注 Excel + 报告 / 可选 SVG；必须识别“木虱检测研究不能直接当叶片验证SOP、品种属性不等于抗病性、未检控污染不能判阳性”的边界。无成功产物则诚实记失败。
12. 全部测量和机器判官聚合到位后，运行 `python scripts/analyze_and_plot.py --strict`。只从**真实 raw run、判官原始响应和已记录的仲裁**计算，自动写 `09_analysis/tables/` 和 `10_figures/`；预期图见 `10_figures/figure_plan.md`。若数据缺失，脚本拒绝制造漂亮数字。
13. 先按 `11_manuscript/storyline_and_writing_plan.md` 写方法、系统、工作流、事实性结果，再填 Abstract/Discussion；披露所有数据缺失与局限。公开版本使用 `12_reproducibility/release_checklist.md` 控制脱敏和授权。

## 注意三条硬边界

- 平台**不是 HAIant 的复刻**。只借鉴“研究痛点→架构→意图/能力验证→端到端生物科研案例”的论文叙事。HAIant DOI `10.1016/j.molp.2026.08.011`，具体数字及方法必须读其原文/补充材料才能引用。
- `B1 simple RAG` 对比必须公平；B0 在附件题缺少文档的结果只能作无资料诊断，不可据此证明平台算法优越。
- 正式问答主指标来自核心 QA60 中 52 道可答题、五层等权宏平均 EGTS；8 道不可答题单列，评分来自两套冻结 LLM 判官和必要仲裁。补充 citation60 的确定性 target chunk 命中是独立终点，额外的意图测试不能改写任一主终点。

## 可直接执行的离线命令

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
