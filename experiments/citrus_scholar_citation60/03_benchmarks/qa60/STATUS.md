# Core QA60 status

状态：`ACTIVE_CORE_CANDIDATE`
实验编号：`E01_QA60_EGTS`
更新日期：2026-11-04

核心 60 题（`E01_QA60_EGTS`）与补充 citation60 的 60 题
（`E01_SUPP_CHUNK_CITATION60`）都是正式实验输入。两套题分别运行、分别分析，
不得池化，也不得共用分母。

正式运行前必须完成：

1. 从 `qa60_CANDIDATE.csv` 生成不可覆盖的 `qa60_LOCKED.csv`；
2. 两名研究生/领域评审对全部 120 题独立认证有效性并仲裁分歧；
3. 生成 `07_scoring/validity/question_validity_LOCKED.csv`；
4. 冻结 B0/B1/B2 运行计划、双专家评分材料、机器判官 candidate 配置、失败/拒答规则和 SHA。

不建立 Gold Answer。QA60 回答主结果由两位专家对全部 180 份回答独立评分，
第三位专家对 `answerability_correct`、`answer_correct`、
`evidence_traceable`、`fatal_error` 任一字段分歧逐字段仲裁；同一 OpenCode Go
入口的 GLM/MiMo/MiniMax 三模型家族用于独立自动评分和一致性分析，其中 MiniMax
只仲裁机器分歧。所有原始证据保留，公开按 `L0/L1/L2` release 规则处理。
详情见 `00_protocol/EXPERT_SCORING_PROTOCOL.md` 与
`00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`。
补充 citation60 只评价最终引用是否命中冻结 target chunk；
semantic support is not reported，不另设人工语义支持率。
