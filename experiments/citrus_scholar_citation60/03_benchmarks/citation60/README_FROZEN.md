# citation60 冻结说明

状态：`FROZEN_FOR_REVIEW`  
冻结日期：2026-11-04  
正式运行：`NOT_STARTED`

本目录是一个独立正式实验，与核心 QA60 并列。它把“一个问题对应一个冻结 chunk”作为唯一证据单元，不替代核心 QA60，也不与核心评分合并。每个问题来自
对相应 chunk 的离线阅读与逆向生成；运行器只接收问题、允许语料名和引用要求，
不接收 chunk 正文、chunk ID、paper ID、源文件路径或出题定位说明。

## 抽样

- 索引 generation：`g20261028T165637Z_851926a9`
- 公共语料：5 类，每类 12 个 chunk
- 总问题数：60
- 每题目标 chunk 数：恰好 1
- 抽样 seed：`20261104`
- 合格正文长度：300–3500 字符
- 来源与许可证仍需按 G05 人工核验；冻结不等于授权已确认

## 冻结文件

| 文件 | 字节 | SHA-256 |
|---|---:|---|
| `chunks_FROZEN.jsonl` | 184718 | `87a207010e63bd607944e38f71df08d0c09d7c9ca86b62f2e62949c89eaef2f3` |
| `chunk_manifest.csv` | 30157 | `144a34137f1f1f0162b9d1afc0f46abfbdb1496d13223e50180bc6080b440a80` |
| `sampling_config.json` | 1944 | `8522cdf8a07757a7f9561ef794946490270083491aab6c9851dba850f3503c97` |
| `question_authoring_packet.jsonl` | 120587 | `f032c13982a18c7657045fdb661ba46485a65696b7461003359ae32ae1a578b9` |
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

- `question_authoring_packet.jsonl` 与 `chunks_FROZEN.jsonl` 完整保存在实验库中，供复现、评分和审计。
- `citation_run_plan.csv` 是运行器唯一输入；不得附加目标定位字段。
- B0 不接收本地语料；B1/B2 只能检索该题声明的允许语料。
- 自动引用检查只证明最终引用包含冻结 chunk ID；不报告 semantic support，
  不将其改写为答案正确率。

## 重跑与变更

现有冻结文件和运行计划均写一次。任何抽样、题干、目标 chunk、语料 generation
或 seed 变化都必须进入新的版本目录并登记新的 SHA；不得覆盖本目录。

校验命令：

```powershell
python scripts/E01_chunk_citation/50_verify_freeze.py
python -m pytest -q scripts/E01_chunk_citation/tests
```
