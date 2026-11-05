# citation60 冻结说明

状态：`FROZEN_FOR_REVIEW / L1_SOURCE_TEXT_PROTECTED`
冻结日期：2026-11-04  
正式运行：`NOT_STARTED`

本目录是一个独立正式实验，与核心 QA60 并列。它把“一个问题对应一个冻结 chunk”作为唯一证据单元，不替代核心 QA60，也不与核心评分合并。每个问题来自
对相应 chunk 的离线阅读与逆向生成；运行器只接收问题、允许语料名和引用要求，
不接收 chunk 正文、chunk ID、paper ID、源文件路径或出题定位说明。

## 抽样

- 索引 generation：`g20261028T165637Z_851926a9`
- 语料来源：5 类，每类 12 个 chunk。它们是五类语料来源，不是“五类公开柑橘语料”，
  也不构成全部柑橘科研文献的代表性随机样本。
- 总问题数：60
- 每题目标 chunk 数：恰好 1
- 抽样 seed：`20261104`
- 合格正文长度：300–3500 字符
- 来源与许可证仍需按 G05 人工核验；冻结不等于授权已确认

## 公开元数据

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `chunk_manifest.csv` | 30157 | `144a34137f1f1f0162b9d1afc0f46abfbdb1496d13223e50180bc6080b440a80` |
| `sampling_config.json` | 1944 | `8522cdf8a07757a7f9561ef794946490270083491aab6c9851dba850f3503c97` |
| `questions_DRAFT.csv` | 20431 | `c845c58524e9f90308deee844b307cc2bd40c776d63e4373067201a434a89591` |
| `questions_FROZEN.jsonl` | 83465 | `363e6d89a4384e2f7b7f61b56be56c81b0b96371e7d9fec40278ed514b4af532` |
| `question_manifest.csv` | 31078 | `de18f538586fdef2d279fa9991b527a9a07cd4ea371f5702cf86933789e2baa7` |
| `questions_FROZEN.manifest.json` | 541 | `c3595a6290cd82e37e307a4e0916c221f0a1c02c75258442e1c5a0b7d04a5e90` |

运行计划另见：

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `06_runs/manifests/citation_run_plan.csv` | 81306 | `fb393b50a7606bedbd28b5737dbd8a2f21fe16da6be97d7102fa3f72a99f05d3` |
| `06_runs/manifests/citation_run_plan_meta.json` | 510 | `8fb51c64ab10b8b3c6f56fd3fc34cfe0cf7b4a78c839c5bdb504a2c2b03efe11` |
| `06_runs/manifests/citation_semantic_audit_plan.csv` | 22578 | `2d48b4171b3d08f29c6d62b0fd402085a4eea5e1126670b72a0b8677a4d5a063` |
| `06_runs/manifests/citation_semantic_audit_plan_meta.json` | 831 | `f32d9d43cbb859f10017ea6ce218d239d04ae383006fc6f1c03e73202addea1d` |

历史 semantic-audit 文件只保留为已停用的来源记录，状态必须为
`DISABLED_BY_DESIGN`。当前方案不报告 semantic support，不运行该抽样，
唯一正式终点为 180 个 run 的确定性 target chunk 引用命中。

## 数据与运行边界

- `chunks_FROZEN.jsonl` 与 `question_authoring_packet.jsonl` 含完整
  `chunk_text`，属于 `L1`。它们只允许保存到
  `/home/WZQ/citrus_experiment/protected/L1/03_benchmarks/citation60/`，
  绝不进入公开 GitHub 树。
- 公开树保留 `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json` 中登记的
  SHA、字节数、来源定位和允许公开的元数据。公开替代物不得包含完整正文。
- `questions_DRAFT.csv`、`question_manifest.csv`、`questions_FROZEN.jsonl`
  和 `chunk_manifest.csv` 已经逐文件复核：不含 `chunk_text`。其中
  `questions_DRAFT.csv` 与 `questions_FROZEN.jsonl` 另含由来源事实压缩成的
  `anchor_for_audit`；60 题的最长连续原文重合为 46 个原始字符，归一化后
  最长 49 个字符，没有 `>=50` 的连续块，也不是整句或整段复制。方法、阈值
  与结果见 `12_reproducibility/CITATION60_ANCHOR_RELEASE_AUDIT.md`。这些文件
  仍只是 `L2` 候选，发布状态统一为 `PENDING_RELEASE_REVIEW`，G12 继续负责
  来源许可和再分发确认。
- `citation_run_plan.csv` 是运行器唯一输入；不得附加目标定位字段。
- B0 不接收本地语料；B1/B2 只能检索该题声明的允许语料。
- 自动引用检查只证明最终引用包含冻结 chunk ID；不报告 semantic support，
  不将其改写为答案正确率。

## 五类来源的实际披露

| corpus ID | 类别 | 当前公开描述 | 许可/访问 |
|---|---|---|---|
| `cqa_deng_book` | 中文柑橘品种资料 | 品种参考材料 | G12 待核验 |
| `cqa_literature` | 同行评议文献 | 混合领域，含非柑橘材料 | 逐来源核验 |
| `cqa_methods` | 实验方法材料 | 方法资料 | G12 待核验 |
| `cqa_translated` | 翻译材料 | 含非柑橘来源 | 翻译与来源权利待核验 |
| `cqa_ucr_varieties` | UCR 品种记录 | 品种记录 | 记录访问/再分发待核验 |

论文与 README 禁止将这些来源合并称为“五类公开柑橘语料”。
机器可读的领域、许可和访问边界见 `CORPUS_SCOPE_METADATA.json`；该文件不复制
任何源文正文，也不改变已冻结的 `sampling_config.json`。

## 重跑与变更

现有冻结文件和运行计划均写一次。任何抽样、题干、目标 chunk、语料 generation
或 seed 变化都必须进入新的版本目录并登记新的 SHA；不得覆盖本目录。

Git 历史中的旧公开对象不会因最新分支删除而自动消失。仓库管理员/PI 必须单独
决定是否临时限制访问、联系 GitHub 移除历史对象或谨慎执行历史修订；未经明确
授权不得 force push。

校验命令：

```powershell
python scripts/E01_chunk_citation/50_verify_freeze.py
python -m pytest -q scripts/E01_chunk_citation/tests
```
