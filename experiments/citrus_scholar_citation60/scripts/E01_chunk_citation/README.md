# E01 补充单 chunk 锚定引用实验

本目录只处理 `E01_SUPP_CHUNK_CITATION60`：一个问题只对应一个冻结 chunk。它是核心 `E01_QA60_EGTS` 的补充证据定位实验，不替代核心 QA60，也不与核心 180 个 run 合并。

补充实验中的“目标”不是由多个证据片段合成的 Gold Answer，而是一个来源明确、内容冻结的 chunk；问题从该 chunk 逆向生成，随后冻结。

## 数据流

```text
active_index.json + corpus registry
  -> 五类公共语料各随机抽 12 个 chunk（固定 seed）
  -> chunks_FROZEN.jsonl + chunk_manifest.csv
  -> question_authoring_packet.jsonl
  -> questions_DRAFT.csv（人工/agent 阅读 chunk 后写题）
  -> questions_FROZEN.jsonl + question_manifest.csv
  -> citation_run_plan.csv（问题进入运行时，不包含 target chunk）
  -> B0/B1/B2 runs
  -> citation_hit_observations.csv
  -> citation_hit_rates.csv
```

## 脚本

```text
00_sample_freeze_chunks.py
  从 active index 的五类公共语料各抽固定数量 chunk，冻结正文、来源、hash 和抽样参数。

10_build_question_authoring_packet.py
  生成只供出题者阅读的 authoring packet；运行器不得读取此文件。

20_freeze_questions.py
  读取 questions_DRAFT.csv，验证一题一 chunk、题目唯一、来源元数据一致，
  生成冻结问题文件和 hash manifest。

30_make_run_plan.py
  生成 60 题 × B0/B1/B2 的运行计划。run prompt 不含 target chunk id、
  target source file、Gold 答案或出题说明。

35_run_citation.py
  在 protocol lock 与 model_config=READY 后，按本实验适配器执行并写不可变 raw JSON。

40_verify_citation_hits.py
  从实际 run JSON 和工具轨迹中提取引用，自动判断是否命中目标 chunk、
  目标文档或允许语料，生成可人工复核的观察表，并按 corpus 和 condition
  汇总引用命中率及 Wilson 95% CI。

45_freeze_semantic_audit_sample.py
  在查看结果前冻结可选的语义支持审计样本：每类语料 4 题、共 20 题
  ×3 条件=60 个 run；该样本不是自动主终点的替代。

50_verify_freeze.py
  校验冻结 chunk、冻结问题、运行计划和运行时泄漏边界。
```

## 固定约束

1. 每个 `question_id` 恰好绑定一个 `target_chunk_id`。
2. 抽样 seed、语料 generation、registry/chunk 文件 SHA 都进入 manifest。
3. 冻结文件不得覆盖；重新抽样必须创建新版本目录/新 manifest。
4. 正式 run prompt 不得包含目标 chunk、标题中的证据句、Gold 或审查备注。
5. 目标 chunk 只供离线评分器、引用核验器和审计者读取。
6. 自动命中是证据定位初筛，不等同于论证正确；最终是否真正支持问题仍需人工核验。

