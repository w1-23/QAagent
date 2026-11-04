# 统计分析输入、严格触发和输出

1. 核心 `07_scoring/expert/consensus/<batch>.csv` 必须有核心 QA60×B0/B1/B2
   的 180 行，且与真实 raw run、两份独立专家评分和必要的第三专家仲裁一一对应。
   人类共识是 QA60 主测量；`07_scoring/judge/qa_judge_aggregated.csv` 的 180 行是
   独立机器测量，须与 J1/J2 原始响应及必要 J3 响应一一对应。
2. 补充 `07_scoring/reviews/citation_hit_observations.csv` 必须有 180 行确定性
   观察，并与真实运行一一对应。`target_chunk_cited` 只依据最终
   `citations`/`sources` 中的冻结 chunk ID。
3. 不生成 semantic-support、human-supported 或 `1/0.5/0` 分数；不得把 locator
   hit 改名为答案正确或引用正确。
4. `intent_observations.csv` 192 条先由确定性工具轨迹规则填写；规则无法判断的
   定性项目才允许冻结 LLM judge 给出结构化结果。不可用样本单列，不进入路由分母。
5. `composite_observations.csv` 24 条用 `|` 分隔 observed tool groups，由集合算法
   计算 precision/recall/F1 和约束违规。
6. `workflow_acceptance.csv` 40 条先检查文件存在、SHA、工具状态和历史回读；
   只有剩余语义 criterion 使用冻结 LLM judge。NA 不算成功，关键失败不能抵消。
7. `09_analysis/tables/` 保存核心 QA 人类共识主结果表、独立机器判官对照表、评分一致性表和补充
   `citation_hit_rates.csv`、`citation_paired_effects.csv`、路由、复合、工作流
   表和 `analysis_manifest.json`。不得生成 `citation_semantic_support.csv`。
8. 核心每个条件报告人类共识主指标比例及 95% Wilson CI；可答/不可答子维度分母
   分别为 52/8，配对比较按同一题的 B0/B1/B2 记录。核心与补充分别分析。补充实验以题目为成对比较单位，报告五语料 micro/macro、
   Wilson CI 和 B2−B1 题内配对差；差值区间使用语料分层成对 Bootstrap，
   固定 seed `20261104`，至少 5000 次，并报告 McNemar exact 描述性 p 值。
9. 人类专家原始评分、第三专家仲裁、原始文件 SHA、协议确认和更正链均进入分析
   manifest；报告人际一致率、第三专家仲裁率、J1/J2 一致率、J3 调用率及 LLM 与
   人类共识的一致率。不得覆盖专家原始提交。
10. Judge 原始请求、响应、模型版本、prompt SHA、错误、token、cost/list_cost/
    allowance 和重试必须进入分析 manifest。
11. 任一必要专家评分、仲裁、判官结果缺失或 schema 无效时，`--strict` 非零退出，
    不得将缺失当作 0 或从分母剔除。
12. `matplotlib` 为出图唯一额外依赖。不得提前生成虚构数据图。
