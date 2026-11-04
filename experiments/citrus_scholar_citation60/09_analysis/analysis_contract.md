# 统计分析输入、严格触发和输出

1. 核心 `07_scoring/reviews/qa_scores_adjudicated.csv` 必须有核心 QA60×B0/B1/B2 的 180 行，并与对应真实 raw run 一一对应；按既有 EGTS/Atomic-slot、证据/引用、拒答与失败规则分析。它与补充 citation60 是两套独立分母。
2. 补充 `07_scoring/reviews/citation_hit_observations.csv` 必须有 180 行自动观察，并与真实运行 `06_runs/raw/{B0,B1,B2}/CC60_*_T1.json` 一一对应；`target_chunk_cited` 只依据最终 `citations`/`sources`。人工 `citation_hit_observations_rater1.csv`、`rater2.csv` 和 `citation_hit_adjudicated.csv` 另存，禁止用自动结果回填。
3. `07_scoring/reviews/intent_observations.csv`192条：`available_1_0`, `required_met_1_0`,`forbidden_violated_1_0`, `observed_primary_group`真实工具轨迹审核后填。不可用单列，不计路由正确率分母，但在运行成功率中必须报告。没有独立模型意图预测时，矩阵明确标“observed action routing”。
4. `07_scoring/reviews/composite_observations.csv`24条：`observed_groups`填`|`分隔的工具组；算法计算工具集合F1与约束违规。记录失败工具与环境不可用。
5. `07_scoring/reviews/workflow_acceptance.csv`40条：`observed_1_0_NA`填`1`,`0`,`NA`，每个案例至少说明失败关键环节。脚本不把NA算成功；关键失败则工作流不能计完全完成。
6. `09_analysis/tables/` 将保存核心 QA60/EGTS 表、补充 `citation_hit_rates.csv`、`citation_paired_effects.csv`、`citation_semantic_support.csv`、`intent_route_rates.csv`、`composite_route_summary.csv`、`workflow_step_acceptance.csv` 和 `analysis_manifest.json`；`10_figures/` 保存 Fig01 架构（允许无结果）、核心 QA60 结果图、补充 target chunk 引用命中图、补充成对效应图、Fig04 意图执行路由矩阵、Fig05 工具执行符合率、Fig06 工作流验收矩阵、SuppF1 时延、SuppF2 错误类型（只在数据存在时绘制）。

核心 QA60/EGTS 与补充 citation60 分别分析。补充实验以题目为成对比较单位，报告五语料 micro/macro、Wilson CI 和 B2−B1 题内配对差；差值区间使用语料分层成对 Bootstrap（固定 seed），并报告 McNemar exact 描述性 p 值与 2×2 转移表。60 个目标 chunk 是 60 个不同来源簇；重复试验不并入原始 180 项。人工 `semantic_support=1/0.5/0` 单独汇总，自动定位命中不得改名为语义正确率。两套 E01 结果不得池化。

`matplotlib` 为出图唯一额外依赖，使用英文轴标签与适当字号避免中文字体缺字。运行 `python scripts/analyze_and_plot.py --strict` 需要真实完整 citation 评分及 raw 记录；现有旧脚本只作起始模板，正式 E01 分析脚本必须在引文数据到位后按本契约实现并登记 SHA。不能提前生成虚构数据图。
