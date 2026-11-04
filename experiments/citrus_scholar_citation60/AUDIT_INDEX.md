# CitrusScholar experiment audit index

Protocol version: `4.3`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`
GitHub baseline reviewed for this revision:
`9719f40daa8d2e4122d451bedafdeca271b5e9d5`

This is the entry point for external GPT review. It is a protocol audit
checkpoint, not a results summary and not evidence that any gate passed.

## 1. Current facts

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| G01-G15 | G01 `BLOCKED` after the known SSH timeout; G02-G15 `PENDING` |
| Agent | Alibaba Cloud Bailian candidate `deepseek-v4.1-flash`, `UNCONFIGURED` |
| Judges | OpenCode Go candidate GLM/MiMo/MiniMax families, `UNCONFIGURED` |
| Existing judge runtime | legacy, non-executable |
| Core QA60 | 60 frozen candidate questions: 52 answerable, 8 unanswerable |
| Supplemental citation60 | 60 frozen questions, one target chunk each |
| Validity reviews | blank 240-row packet; no human decision |
| Expert scoring | blank v4.3 schemas; no answer packet or rating |
| Synthetic dry run | interface-only `SIMULATED_DRY_RUN`, never a study result |
| Manuscript | plans and templates only |

The current review unit changes protocol, schemas, prompts, configurations, and
documentation only. It does not implement or authorize the new runtime.

## 2. Authoritative reading order

1. `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`
2. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
3. `07_scoring/rubric.md`
4. `09_analysis/analysis_contract.md`
5. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
6. `PUBLICATION_SCOPE.md`
7. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
8. `05_baselines/baseline_contract.md`
9. `00_protocol/02_实验执行与论文写作总计划.md`
10. `00_protocol/05_论文写作执行计划.md`
11. `11_manuscript/storyline_and_writing_plan.md`
12. `11_manuscript/manuscript_skeleton.md`
13. `12_reproducibility/data_dictionary.md`
14. `00_protocol/change_log.csv`

## 3. Seven P0 corrections

| ID | Correction | Audit path |
|---|---|---|
| P0-1 | All data retained, public release restricted by `L0/L1/L2` | `PUBLICATION_SCOPE.md`, server protocol, asset ledger |
| P0-2 | `evidence_traceable` replaces the former use-based field; operational boundaries fixed | expert protocol, rubric, codebook |
| P0-3 | Score, field-adjudication, issue, attestation, consensus schemas added | `07_scoring/expert/` |
| P0-4 | `B2 vs B1` human-consensus EGTS is the unique primary comparison | analysis contract |
| P0-5 | Model-written final decisions prohibited; J3 returns only disputed fields | unified protocol, prompts, judge template |
| P0-6 | Agent variables unified as `CITRUS_AGENT_*` | model config, baseline contract, server protocol |
| P0-7 | Human, judge, citation, and all-module strict gates separated | analysis contract, rubric, README |

## 4. Frozen scoring boundary

Fields:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

`ANSWERABLE`:

```text
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error
```

`UNANSWERABLE`:

```text
answer_correct = not_applicable
evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

Denominators: EGTS 60, answerability 60, answer correctness 52, evidence
traceability 52, fatal-error rate 60. NA is never zero. Experts do not enter
the final outcome.

## 5. Primary statistics

Unique confirmatory comparison: `B2 vs B1`, human-consensus EGTS.

- two-sided exact McNemar;
- paired difference in percentage points;
- 10,000 stratified paired bootstrap replicates;
- seed `20261104`;
- L/V/W/X/P/N strata;
- question-level resampling retaining all three condition rows;
- complete 2x2 transition table.

`B1 vs B0` and `B2 vs B0` are descriptive only. B0 is not a fair RAG comparator.

## 6. Expert and machine independence

- Two experts independently score all 36 Pilot and 180 formal answers.
- A third human expert adjudicates every disputed field.
- Human consensus is the primary QA60 outcome.
- J1/J2 score all 180; J3 adjudicates only field-level machine disagreement.
- GLM, MiMo, and MiniMax share one OpenCode Go service entry point.
- Judges do not use the Agent family, endpoint, or runtime credential.
- Machine output contains no final decision field.

## 7. Public/restricted data

| Class | GitHub |
|---|---|
| `L0` credentials, cookies, authentication data | prohibited everywhere |
| `L1` restricted full text/excerpts, private SOPs, identity maps, raw sensitive logs | protected server storage; public SHA/locator/redaction lineage only |
| `L2` public-safe questions/evidence, redacted runs, SHA, scores, statistics, manuscript materials | allowed after release review |

All raw evidence is retained. Public release is not the same as retention.

## 8. Static review checks required for this commit

- `git diff --check`;
- JSON parse for every changed JSON file;
- CSV header, row-count, and row-width validation;
- Markdown fence and active-document consistency checks;
- active-surface search for retired scoring fields, model-written decisions,
  legacy Agent variables, old root names, unconditional public-release claims,
  and unresolved readiness claims;
- secret scan for Bailian/OpenCode/Authorization signatures.

No server, SSH, database, model API, Pilot, formal experiment, scoring, or
analysis command belongs to this review unit.

## 9. Experiment inventory

| ID | Experiment | Planned units | Current evidence |
|---|---|---:|---|
| `S00_SIMULATED_DRY_RUN` | interface regression | 180+180+192+24+7 | synthetic only |
| `P00_PILOT` | independent calibration | 12×3=36 | `NOT_STARTED` |
| `E01_QA60_EGTS` | core answer quality | 60×3=180 | frozen candidate |
| `E01_SUPP_CHUNK_CITATION60` | target locator | 60×3=180 | frozen candidate |
| `E02_ROUTE192` | observed action compliance | 192 | candidate |
| `E03_MULTI24` | composite tool tasks | 24 | candidate |
| `E04_CITATION_REPEAT72` | optional repeat sensitivity | 72 | optional |
| `E05`-`E11` | WF01-WF07 | 7 cases, 40 criteria | protocol only |
| `U01_USER12` | optional user study | about 12 | ethics-dependent |

Core QA60 and citation60 are never pooled. Citation60 does not receive a
semantic-support rating.

## 10. Paper assets

- `11_manuscript/storyline_and_writing_plan.md`;
- `11_manuscript/manuscript_skeleton.md`;
- `11_manuscript/claim_source_map_TEMPLATE.csv`;
- `11_manuscript/table_figure_provenance_TEMPLATE.csv`;
- `10_figures/figure_plan.md`;
- `00_protocol/05_论文写作执行计划.md`.

No manuscript number is valid until it is generated by a declared strict
analysis and linked through `analysis_manifest.json` and the claim-source map.
