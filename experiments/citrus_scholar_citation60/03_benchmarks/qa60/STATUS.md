# qa60 core benchmark status

状态：`ACTIVE_CORE_CANDIDATE`  
更新日期：2026-11-04

本目录的 60 题分层 QA、52 answerable + 8 unanswerable、EGTS/Atomic-slot
与 Gold 设计继续作为核心 E01 实验保留，不是废弃或仅供历史参考的资产。

正式运行前必须完成：

1. 从 `qa60_CANDIDATE.csv` 生成不可覆盖的 `qa60_LOCKED.csv`；
2. 按 `Gold逆向生成与人工审核规程.md` 完成 Gold、证据定位、人工复核与仲裁；
3. 锁定 B0/B1/B2 运行计划、EGTS/Atomic-slot 评分表、失败/拒答规则与 SHA。

`03_benchmarks/citation60/` 是新增补充实验，不是本目录的替代品：

- 核心：`E01_QA60_EGTS`，评价分层 QA 的答案质量、证据/引用和拒答；
- 补充：`E01_SUPP_CHUNK_CITATION60`，评价最终引用能否定位到单一冻结 chunk。

两套题号、180 个 run、评分表和统计分母必须分开，不得把 EGTS 与
target-chunk locator hit 合并成一个“正确率”。
