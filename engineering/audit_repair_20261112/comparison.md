# Source-only audit repair evidence replay

This records observed pure predicate output. No benchmark, experiment, harness, service, network, model, gold, scorer, attachment body, generated artifact, or historical answer was executed/evaluated. The 40 formal cases are prompt-field replays only, including FW20; they are not reruns of those tasks.

Baseline: `318b935266f2f3c56e30f7a37c404428975acb7f` loaded only with `git show` in the trusted checkout. Current: the worktree bytes whose exact hashes and pure source slices are embedded in the JSON.

Exit code: **0**. Failures: **0**. Warnings: **0**.

## Reproduction command

```bash
'E:\anaconda\python.exe' -I -X utf8 'E:\codex_WORKSPACES\CitrusQA_Local\engineering\audit_repair_20261112\replay.py' --repo E:/codex_WORKSPACES/CitrusQA_Local --audit-root E:/codex_WORKSPACES/v8_work --formal-root E:/codex_WORKSPACES/v8_work/02_P4/formal --selected-jsonl 'E:/codex_WORKSPACES/QAfinalexperiment/CitrusScalorexperimentdata/experimentdata_active/09_新增实验/01_工具与来源决策/工具决策240题.jsonl' --output-dir E:/codex_WORKSPACES/CitrusQA_Local/engineering/audit_repair_20261112
```

Run from the trusted worktree. Python isolation and UTF-8 mode are checked before reading external data. All output paths are restricted to this new audit directory. Reproduction uses current worktree source; the recorded hashes identify the particular observed source state and can be checked before rerunning.

## Observed totals

| Measure | Baseline | Current |
|---|---:|---:|
| Formal prompts evaluated | 40 | 40 |
| Requirements | 39 | 32 |
| Cases without requirements | 15 | 15 |
| Requirements without kinds | 0 | 0 |
| Requirements without formats | 9 | 2 |

Full contract output changed in 22/40 cases. Ignoring only the newly introduced body_scope field, output changed in 10/40 cases; requirement counts changed in 6/40 cases.

Kind/format totals count membership in each requirement; a requirement can have multiple kinds/formats. An absent field in baseline output is preserved as absent, never interpreted as an observed baseline scope.

| Kind | Baseline memberships | Current memberships |
|---|---:|---:|
| annotation_table | 5 | 8 |
| generic_output | 9 | 2 |
| report_html | 21 | 21 |
| table_file | 3 | 0 |
| verification_sheet | 1 | 1 |

| Format | Baseline memberships | Current memberships |
|---|---:|---:|
| html | 21 | 21 |
| xlsx | 9 | 9 |

## Annotation and body-scope focus

### FW11

| Version | Observed products |
|---|---|
| Baseline | p4_0 · table_file / xlsx · 可直接下载的 XLSX<br>p6_0 · report_html / html · 另外写一份 HTML 报告<br>p7_0 · generic_output / (no format) · 把复核清单 and 不能替作者补写的内容写进去 |
| Current | p4_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p6_0 · report_html / html · body=shared · 一份 HTML 报告 |

### FW12

| Version | Observed products |
|---|---|
| Baseline | p6_0 · table_file / xlsx · 可直接下载的 XLSX<br>p8_0 · report_html / html · 另外写一份 HTML 报告 |
| Current | p6_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p8_0 · report_html / html · body=shared · 一份 HTML 报告 |

### FW20

| Version | Observed products |
|---|---|
| Baseline | p7_0 · table_file / xlsx · 可直接下载的 XLSX<br>p9_0 · report_html / html · 另外写一份 HTML 报告 |
| Current | p7_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p9_0 · report_html / html · body=shared · 一份 HTML 报告 |

### FW18

| Version | Observed products |
|---|---|
| Baseline | p3_0 · generic_output / (no format) · 内部方法选择前的核查清单<br>p6_0 · generic_output / (no format) · 所有结论必须能追溯到具体文献中的句子或表格<br>p7_0 · report_html / html · 一份可直接下载的 HTML 报告<br>p8_0 · report_html / html · 一份 HTML 简报 |
| Current | p7_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告<br>p8_0 · report_html / html · body=distinct · 一份 HTML 简报 |

FW18 baseline has no body_scope field or report_body_scope predicate. The current scope values above are actual per-product parser output. Removing accidental requirements and separating product bodies does not establish that the model will produce a correct report or annotation.

## Formal comparison by case

Exact prompt strings, original run.json paths, full-file SHA256, exact UTF-8 prompt SHA256, stored historical prompt digests, and full product dictionaries are preserved in comparison.json.

| Case | Count baseline → current | Baseline kinds / formats / product IDs / labels | Current kinds / formats / product IDs / labels |
|---|---:|---|---|
| FW01 | 1 → 1 | p7_0 · report_html / html · 一份可直接下载的 HTML 报告：三篇文献逐篇对照 | p7_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告：三篇文献逐篇对照 |
| FW02 | 1 → 1 | p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW03 | 1 → 1 | p5_0 · report_html / html · 一份可直接下载的 HTML 报告 | p5_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW04 | 1 → 1 | p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW05 | 1 → 1 | p4_0 · report_html / html · 一份可直接下载的 HTML 报告 | p4_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW06 | 1 → 1 | p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW07 | 1 → 1 | p6_0 · report_html / html · 一份可直接下载的 HTML 报告 | p6_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW08 | 2 → 1 | p2_0 · generic_output / (no format) · 有出处的取舍清单及现场待验证事项<br>p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW09 | 1 → 1 | p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW10 | 1 → 1 | p4_0 · report_html / html · 一份可直接下载的 HTML 报告 | p4_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW11 | 3 → 2 | p4_0 · table_file / xlsx · 可直接下载的 XLSX<br>p6_0 · report_html / html · 另外写一份 HTML 报告<br>p7_0 · generic_output / (no format) · 把复核清单 and 不能替作者补写的内容写进去 | p4_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p6_0 · report_html / html · body=shared · 一份 HTML 报告 |
| FW12 | 2 → 2 | p6_0 · table_file / xlsx · 可直接下载的 XLSX<br>p8_0 · report_html / html · 另外写一份 HTML 报告 | p6_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p8_0 · report_html / html · body=shared · 一份 HTML 报告 |
| FW13 | 2 → 2 | p5_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p7_0 · report_html / html · 另外写一份 HTML 报告 | p5_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p7_0 · report_html / html · body=shared · 一份 HTML 报告 |
| FW14 | 2 → 2 | p6_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p8_0 · report_html / html · 另外写一份 HTML 报告 | p6_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p8_0 · report_html / html · body=shared · 一份 HTML 报告 |
| FW15 | 3 → 2 | p3_0 · generic_output / (no format) · 一份带位置（工作表/行/列）的核验清单 and 结论<br>p9_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p10_0 · report_html / html · 另外写一份 HTML 报告 | p9_0 · annotation_table / xlsx · 可直接下载的标注版 XLSX<br>p10_0 · report_html / html · body=shared · 一份 HTML 报告 |
| FW16 | 1 → 1 | p7_0 · report_html / html · 一份可直接下载的 HTML 报告 | p7_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW17 | 1 → 1 | p3_0 · report_html / html · 一份可直接下载的 HTML 报告 | p3_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW18 | 4 → 2 | p3_0 · generic_output / (no format) · 内部方法选择前的核查清单<br>p6_0 · generic_output / (no format) · 所有结论必须能追溯到具体文献中的句子或表格<br>p7_0 · report_html / html · 一份可直接下载的 HTML 报告<br>p8_0 · report_html / html · 一份 HTML 简报 | p7_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告<br>p8_0 · report_html / html · body=distinct · 一份 HTML 简报 |
| FW19 | 1 → 1 | p4_0 · report_html / html · 一份可直接下载的 HTML 报告 | p4_0 · report_html / html · body=shared · 一份可直接下载的 HTML 报告 |
| FW20 | 2 → 2 | p7_0 · table_file / xlsx · 可直接下载的 XLSX<br>p9_0 · report_html / html · 另外写一份 HTML 报告 | p7_0 · annotation_table / xlsx · 可直接下载的 XLSX<br>p9_0 · report_html / html · body=shared · 一份 HTML 报告 |
| L-N01 | 0 → 0 | (none) | (none) |
| L-N02 | 0 → 0 | (none) | (none) |
| L-N03 | 0 → 0 | (none) | (none) |
| L-N04 | 1 → 1 | p3_0 · generic_output / (no format) · 可直接交接给综述作者的清单（含 DOI、相关性理由、排除理由） | p3_0 · generic_output / (no format) · 可直接交接给综述作者的清单（含 DOI、相关性理由、排除理由） |
| L-N05 | 1 → 1 | p2_0 · verification_sheet / xlsx · 核验表（可解析性、题名一致性、期刊/年份） | p2_0 · verification_sheet / xlsx · 核验表（可解析性、题名一致性、期刊/年份） |
| L-N06 | 0 → 0 | (none) | (none) |
| L-N07 | 1 → 1 | p3_0 · generic_output / (no format) · 修正后的条目清单 and 修改说明 | p3_0 · generic_output / (no format) · 修正后的条目清单 and 修改说明 |
| L-N08 | 0 → 0 | (none) | (none) |
| R01 | 2 → 1 | p2_0 · annotation_table / xlsx · 标注副本<br>p2_1 · generic_output / (no format) · 问题清单 | p2_0 · annotation_table / xlsx · 标注副本 |
| R02 | 0 → 0 | (none) | (none) |
| R03 | 2 → 1 | p2_0 · annotation_table / xlsx · 标注副本<br>p2_1 · generic_output / (no format) · 问题清单 | p2_0 · annotation_table / xlsx · 标注副本 |
| R04 | 0 → 0 | (none) | (none) |
| W01 | 0 → 0 | (none) | (none) |
| W02 | 0 → 0 | (none) | (none) |
| W03 | 0 → 0 | (none) | (none) |
| W04 | 0 → 0 | (none) | (none) |
| W05 | 0 → 0 | (none) | (none) |
| W06 | 0 → 0 | (none) | (none) |
| W07 | 0 → 0 | (none) | (none) |
| W08 | 0 → 0 | (none) | (none) |

## Selected local-only prompt predicates

Only the 14 requested task IDs' prompt strings were decoded and passed to source predicates. Other prompts and all capability/gold/scoring fields were not decoded or inspected. The JSONL bytes were streamed for a full-file checksum and task-ID selection. No benchmark result or score was calculated.

The binding parser is the historical general_agent_graph._source_scope versus the current imported retrieval_router.source_scope alias. Constraints are called with both auto and the binding scope. Tool policy means resolve_tool_policy on that text/scope only; it is not a runtime tool exposure check.

| Measure | Baseline | Current |
|---|---:|---:|
| Prompts evaluated | 14 | 14 |
| Binding scope local | 0 | 14 |
| Auto constraints require local | 0 | 14 |
| Binding-scope constraints require local | 0 | 14 |
| Policy allows local and denies named web tools | 0 | 14 |

| ID | Baseline binding / auto constraint | Current binding / auto constraint | Selected original prompt |
|---|---|---|---|
| TD044 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 请在库内检索影响柑橘果皮类胡萝卜素积累的研究，回答时注明文献标题。 |
| TD045 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。 |
| TD046 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。 |
| TD047 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。 |
| TD052 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 请你作为柑橘实验室科研助手，请在库内检索影响柑橘果皮类胡萝卜素积累的研究，回答时注明文献标题。 |
| TD053 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 请你作为柑橘实验室科研助手，本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。 |
| TD054 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 请你作为柑橘实验室科研助手，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。 |
| TD061 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我正在整理实验记录，本地资料里有没有柑橘砧木盐胁迫与叶片Na+积累的实验？请定位到文献。 |
| TD062 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我正在整理实验记录，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。 |
| TD063 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我正在整理实验记录，为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。 |
| TD070 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我只需要可以核查的结果，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。 |
| TD071 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我只需要可以核查的结果，为我找出两篇库内关于柑橘采后酸度变化的论文，并说明各自处理条件。 |
| TD072 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我只需要可以核查的结果，不需要即时新闻；从本地柑橘研究语料中找出RT-qPCR内参基因筛选的实验证据。 |
| TD078 | auto / advisory:auto (automatic_advice) | local / required:local (explicit_local) | 我希望结果便于写论文，只使用当前索引中的中国柑橘品种资料，核对某品种的分类及适用来源。 |

## Provenance and boundaries

| Input | SHA256 |
|---|---|
| E:/codex_WORKSPACES/v8_work/REVIEW_GUIDE.md | e36aff6e9826d989136c108232ca0c7d51f5dfd45fc2f3ad7072515bd8e0205d |
| E:/codex_WORKSPACES/v8_work/RUN_LOG.md | 18d3bee8c3f17f504cad60eea0ab071950dc98f262e0f965233db146002f8c66 |
| E:/codex_WORKSPACES/v8_work/INVENTORY.csv | 1c0d1bfa1df56501e2e1ea4e05b1224e13882158ff6a55a1a73e4f0c2ebf18f9 |
| E:/codex_WORKSPACES/QAfinalexperiment/CitrusScalorexperimentdata/experimentdata_active/09_新增实验/01_工具与来源决策/工具决策240题.jsonl | b7cc70410e7abe8160386b8a84360056e5f7d2d1f91af49ea370b81b02f281d5 |
| baseline: 01_src/src/graph/intent_text.py | ffe1034f7a6add973643dc9d36b40880cea798ff48c6bb6e2306a2be43e959fb |
| baseline: 01_src/src/graph/export_contract.py | 67682f66f501e8317eee12fb33b0bb87fe2e0d83762f47f6ac11aa9a79be60ed |
| baseline: 01_src/src/graph/retrieval_router.py | 704442d378cf1deeb4cd81a53a11100f3fd517e08a093df7e6141e7c13489768 |
| baseline: 01_src/src/graph/general_agent_graph.py | 51a82584febde187efdc046271e178ae5897de0a4644bac3215b830c6ae0bde8 |
| baseline: 01_src/src/graph/tool_policy.py | 8518462638ad575a41e14c2c0ee1064e15f2cfe433e15c51029741d206c07390 |
| current: 01_src/src/graph/intent_text.py | ffe1034f7a6add973643dc9d36b40880cea798ff48c6bb6e2306a2be43e959fb |
| current: 01_src/src/graph/export_contract.py | 5bf939571240b2bf81fd218826f30fb00a3fb31114a0ee314989984121f4fab0 |
| current: 01_src/src/graph/retrieval_router.py | 7a101d23a7cc76d5476d233f9011fa943002382751aa90276f45ced147f4f543 |
| current: 01_src/src/graph/general_agent_graph.py | f850ed9bad10d79ef2b6cf3bb187d791a4a034598f1290dbea046772ba1814f1 |
| current: 01_src/src/graph/tool_policy.py | dbbb6596031c034704c343c5e85ff3b2d70ad7ecfebe0df08a3dec3920639ed1 |

Formal run.json inventory hashes matched: 40/40. All external inputs and current source bytes were rehashed before writing results; observed input/source drift is reported as a failure. Git-index hashes were recorded before/after (the script never invokes an index-writing command).

Historical metadata describes a frozen earlier runtime; the trusted commit selected here is an engineering comparison baseline, not a claim that it produced those historical results. This replay does not validate attachment processing, merged cells, output artifacts, model behavior, delivery success, or score improvements. Existing reports, logs, tests, runner, production sources, historical files, gold/scorer data, and git index receive no writes.

## Failures

None observed.

## Warnings and expectation differences

None observed.

## Replay expectations

| Check | Observed | Result |
|---|---|---|
| Trusted baseline commit resolves exactly | 318b935266f2f3c56e30f7a37c404428975acb7f | PASS |
| Formal record count is 40 | 40 | PASS |
| FW01: directory agrees with task_id | FW01 | PASS |
| FW01: run.json SHA256 matches inventory | fa5a2a2cc1a0ce3ca06144f91446dc12f3812fb91c551607e21de5eaa58669ec | PASS |
| FW02: directory agrees with task_id | FW02 | PASS |
| FW02: run.json SHA256 matches inventory | db34b77f9e41c278f3f2e95d0934e3e083d7720ebb298836463b81e5ec04e8e8 | PASS |
| FW03: directory agrees with task_id | FW03 | PASS |
| FW03: run.json SHA256 matches inventory | 96fd62ed72039b3e770b9cfb30424109f561b9dc44fa64c19ef689fb7ebba1fc | PASS |
| FW04: directory agrees with task_id | FW04 | PASS |
| FW04: run.json SHA256 matches inventory | ad8cc41acefaed8eb67c77c9cf734a2748c7b9c416edc1daab78652f29c5ed5a | PASS |
| FW05: directory agrees with task_id | FW05 | PASS |
| FW05: run.json SHA256 matches inventory | 11abb13642a4b14d6252e327d5cf4c2bb8764695c8556b996cc25251b1e66fc9 | PASS |
| FW06: directory agrees with task_id | FW06 | PASS |
| FW06: run.json SHA256 matches inventory | ebe3417fba932bfcec109c85598cbc11e219a5c89cfe9e592c35a627b511807d | PASS |
| FW07: directory agrees with task_id | FW07 | PASS |
| FW07: run.json SHA256 matches inventory | 772e80b67dc1fec66acb3bb64748cac308885270f60b4cb0cd17a1ea4498a0ec | PASS |
| FW08: directory agrees with task_id | FW08 | PASS |
| FW08: run.json SHA256 matches inventory | 5a8e7d21d5adc9100a0aa13062b4ce9370f7fffaa9bdddc2b1cf16bd7b53df5c | PASS |
| FW09: directory agrees with task_id | FW09 | PASS |
| FW09: run.json SHA256 matches inventory | 98b84b49efb0315a9e2f995e57d76857f96bf356c99b998f3470de27c26e6f51 | PASS |
| FW10: directory agrees with task_id | FW10 | PASS |
| FW10: run.json SHA256 matches inventory | 1375cb8146e8bde0648b8931103a596f95060fdd2a3d80b8f73b3ccc1ebfa93b | PASS |
| FW11: directory agrees with task_id | FW11 | PASS |
| FW11: run.json SHA256 matches inventory | 81068153aac999eecd29f7d27b2e5ec1d52b358430822bfe54e3ec931b687d01 | PASS |
| FW12: directory agrees with task_id | FW12 | PASS |
| FW12: run.json SHA256 matches inventory | cc479ffc4c78d4ebbc346923a5f8fde17ba4c028c4d634358f4088bb10f825c3 | PASS |
| FW13: directory agrees with task_id | FW13 | PASS |
| FW13: run.json SHA256 matches inventory | ad4c9ac2ffbf5dac6bb0220efee5a01d3930ef1c2c46091bb4e1c34b812bc787 | PASS |
| FW14: directory agrees with task_id | FW14 | PASS |
| FW14: run.json SHA256 matches inventory | 8a55e8c4e2301d0b3716a723dd5be56d6b56b3d7b5735e6d7cc16461382a6fd6 | PASS |
| FW15: directory agrees with task_id | FW15 | PASS |
| FW15: run.json SHA256 matches inventory | b53e27f52e75aa6cd1b462d6cf333592e3d863ddc5506e27f90fd59c0bcb471e | PASS |
| FW16: directory agrees with task_id | FW16 | PASS |
| FW16: run.json SHA256 matches inventory | 0ea759fd052ab152957eeca86025e307ffb6719d086dd95ae5793baa96bbd785 | PASS |
| FW17: directory agrees with task_id | FW17 | PASS |
| FW17: run.json SHA256 matches inventory | 265a2e986194139e0009bf606e7a8f7a3211366cb872a94c8200e58a10e02bf8 | PASS |
| FW18: directory agrees with task_id | FW18 | PASS |
| FW18: run.json SHA256 matches inventory | b6803072ecc6e5c5ecdd345a56a126a1c0857073a8752c4314004f5728b0d325 | PASS |
| FW19: directory agrees with task_id | FW19 | PASS |
| FW19: run.json SHA256 matches inventory | 1744a8399f672a71af61a1fbdbd8ebf2c20cf216939c46b9ab621bac05e8b468 | PASS |
| FW20: directory agrees with task_id | FW20 | PASS |
| FW20: run.json SHA256 matches inventory | 2c0b0afca5f41ffb78e8fd4d6102a05bd1ae9f4c1280b13bbc5b3961787d793a | PASS |
| L-N01: directory agrees with task_id | L-N01 | PASS |
| L-N01: run.json SHA256 matches inventory | 8734ff1a1bc1621aee6853713fed8c38056ce4b7e7e595ece9192c258aac2146 | PASS |
| L-N02: directory agrees with task_id | L-N02 | PASS |
| L-N02: run.json SHA256 matches inventory | af9f47f97950e456c39647f7fa155d005507495b280566878dc93de18ce97fae | PASS |
| L-N03: directory agrees with task_id | L-N03 | PASS |
| L-N03: run.json SHA256 matches inventory | e05be6c149e1b82e317756fb9afefc0c545386b247bb165934aaa4bfa1d9b5b4 | PASS |
| L-N04: directory agrees with task_id | L-N04 | PASS |
| L-N04: run.json SHA256 matches inventory | f20cf73694376e44b34573669cc996a68cd97e939d8b06bdf873c3e2a4f4d026 | PASS |
| L-N05: directory agrees with task_id | L-N05 | PASS |
| L-N05: run.json SHA256 matches inventory | c0ec30eda41ceba899047c15d0bd3984c78cf35e54e062cd9999c8e1040cfd73 | PASS |
| L-N06: directory agrees with task_id | L-N06 | PASS |
| L-N06: run.json SHA256 matches inventory | f4713bae21c8b0517583fc3aeb7d6f55a832df564dd92ebf020ed6aa3286953d | PASS |
| L-N07: directory agrees with task_id | L-N07 | PASS |
| L-N07: run.json SHA256 matches inventory | bf68d59b6cf2bfd67411070a0ccd859e8a47edd1358704adf9e419db64db45c0 | PASS |
| L-N08: directory agrees with task_id | L-N08 | PASS |
| L-N08: run.json SHA256 matches inventory | 9c8afd972b18b7852f3b278e1cb11edf10ff31363b8de3f4305e6360a61a3939 | PASS |
| R01: directory agrees with task_id | R01 | PASS |
| R01: run.json SHA256 matches inventory | a567c47408a7b2aeb1c592253b69d9665dc40e6bcaa670c79156446bcb1f3dde | PASS |
| R02: directory agrees with task_id | R02 | PASS |
| R02: run.json SHA256 matches inventory | 97c0081032348bfd34135398886ceade5143bea74ecfef9718fbc07dbecd61c3 | PASS |
| R03: directory agrees with task_id | R03 | PASS |
| R03: run.json SHA256 matches inventory | 5b638044862c739d24d7db43a26b9e3a729d4a5d657c6a624c89159ab772294b | PASS |
| R04: directory agrees with task_id | R04 | PASS |
| R04: run.json SHA256 matches inventory | 4554cb2853e26ef24136fb831dc35cf5f0d1509bfe4deea73032d5dba8d34b5a | PASS |
| W01: directory agrees with task_id | W01 | PASS |
| W01: run.json SHA256 matches inventory | f7cd2e21881bf35268cebf1887155502cd60cec037326035cf9c8d7fe4f1d7a4 | PASS |
| W02: directory agrees with task_id | W02 | PASS |
| W02: run.json SHA256 matches inventory | 1f2c5e9440a5393cfb2c59b732e847acf3e2a3a831b2fc7e3e2f0a2f98d967df | PASS |
| W03: directory agrees with task_id | W03 | PASS |
| W03: run.json SHA256 matches inventory | 0f2fcadecad1187f777e1e938d8004fa847589fc766da47a01a370b59491d852 | PASS |
| W04: directory agrees with task_id | W04 | PASS |
| W04: run.json SHA256 matches inventory | 276ac368028b30f47194c1610b856a8ba008de12c28c3838db9ad2b26ea4a6d8 | PASS |
| W05: directory agrees with task_id | W05 | PASS |
| W05: run.json SHA256 matches inventory | 82dc74ecb80231883b8d81c3761ab5980a18067d9e16b981a81d2f0c342b3482 | PASS |
| W06: directory agrees with task_id | W06 | PASS |
| W06: run.json SHA256 matches inventory | 8dc702997e48f2ec376d62103d83f3d3be054b911ec267ba2deef3dd19135415 | PASS |
| W07: directory agrees with task_id | W07 | PASS |
| W07: run.json SHA256 matches inventory | e10e9f07c39e0cf61a534716913554ad53b72a0fe2e631753b5fad4875dd1b8a | PASS |
| W08: directory agrees with task_id | W08 | PASS |
| W08: run.json SHA256 matches inventory | 905e309c9abd28a91120e2298f030671ab28477e5c28bbea0209abfe4876cd80 | PASS |
| Formal IDs match the specified 40-case set | ['FW01', 'FW02', 'FW03', 'FW04', 'FW05', 'FW06', 'FW07', 'FW08', 'FW09', 'FW10', 'FW11', 'FW12', 'FW13', 'FW14', 'FW15', 'FW16', 'FW17', 'FW18', 'FW19', 'FW20', 'L-N01', 'L-N02', 'L-N03', 'L-N04', 'L-N05', 'L-N06', 'L-N07', 'L-N08', 'R01', 'R02', 'R03', 'R04', 'W01', 'W02', 'W03', 'W04', 'W05', 'W06', 'W07', 'W08'] | PASS |
| Exactly the 14 selected prompt strings were evaluated | ['TD044', 'TD045', 'TD046', 'TD047', 'TD052', 'TD053', 'TD054', 'TD061', 'TD062', 'TD063', 'TD070', 'TD071', 'TD072', 'TD078'] | PASS |
| FW11: current annotation_table/xlsx classification | [{'kinds': ['annotation_table'], 'formats': ['xlsx'], 'product_id': 'p4_0', 'label': '可直接下载的 XLSX'}] | PASS |
| FW12: current annotation_table/xlsx classification | [{'kinds': ['annotation_table'], 'formats': ['xlsx'], 'product_id': 'p6_0', 'label': '可直接下载的 XLSX'}] | PASS |
| FW20: current annotation_table/xlsx classification | [{'kinds': ['annotation_table'], 'formats': ['xlsx'], 'product_id': 'p7_0', 'label': '可直接下载的 XLSX'}] | PASS |
| FW18: current report bodies are shared then distinct | ['shared', 'distinct'] | PASS |
| TD044: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD045: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD046: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD047: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD052: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD053: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD054: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD061: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD062: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD063: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD070: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD071: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD072: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| TD078: current pure source predicates require local | {'binding': 'local', 'constraints_auto': {'source_policy': 'required', 'requested_mode': 'local', 'required_tool_groups': [['local_hybrid_search']], 'data_audit_requested': False, 'pure_transform': False, 'reason_code': 'explicit_local'}} | PASS |
| External input hashes remain unchanged during replay | [] | PASS |
| Current source hashes remain unchanged during replay | [] | PASS |
| Git index SHA256 remains unchanged during replay | 3c26463437f3d481545da20c228bead296091d3a4b95b237d0d8e9b672125b8e | PASS |
