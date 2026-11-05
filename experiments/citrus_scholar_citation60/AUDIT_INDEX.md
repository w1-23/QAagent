# CitrusScholar experiment audit index

Protocol version: `4.3.1`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`

Version labels:

| Label | SHA |
|---|---|
| External review baseline | `9719f40daa8d2e4122d451bedafdeca271b5e9d5` |
| Reviewed target | `d156dab5dc299198696f2194f778086f5d79e0cc` |
| v4.3.1 implementation commit | resolve with `git rev-parse HEAD` |

This is the entry point for external GPT review. It is not a results summary
and not evidence that any gate passed.

## 1. Current facts

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| G01-G15 | G01 `BLOCKED` after the known SSH timeout; G02-G15 `PENDING` |
| PASS gates | `0` |
| Agent | Alibaba Cloud Bailian candidate `deepseek-v4.1-flash`, `UNCONFIGURED` |
| Judges | OpenCode Go candidate GLM/MiMo/MiniMax families, `UNCONFIGURED` |
| Existing judge runtime | static code only; no model call authorized |
| Core QA60 | 60 frozen candidate questions: 52 answerable, 8 unanswerable |
| Supplemental citation60 | 60 frozen derived questions, one protected target chunk each |
| Validity reviews | blank 240-row packet; no human decision |
| Expert scoring | blank v4.3.1 schemas; no answer packet or rating |
| Synthetic dry run | interface-only `SIMULATED_DRY_RUN`, never a study result |
| Manuscript | plans and templates only |
| Credentials | no key/token/cookie/Authorization value in the package |

This review unit changes protocol, data release, schemas, candidate code, and
documentation only. It does not execute the experiment runtime.

## 2. Authoritative reading order

1. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_1.md`
2. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
3. `07_scoring/rubric.md`
4. `07_scoring/expert/SCORING_CODEBOOK_v4.3.1.md`
5. `09_analysis/analysis_contract.md`
6. `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`
7. `12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`
8. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
9. `PUBLICATION_SCOPE.md`
10. `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`
11. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
12. `05_baselines/baseline_contract.md`
13. `00_protocol/02_实验执行与论文写作总计划.md`
14. `00_protocol/05_论文写作执行计划.md`
15. `11_manuscript/storyline_and_writing_plan.md`
16. `11_manuscript/manuscript_skeleton.md`
17. `12_reproducibility/data_dictionary.md`
18. `12_reproducibility/CITATION60_ANCHOR_RELEASE_AUDIT.md`
19. `00_protocol/change_log.csv`
20. `scripts/audit_static_package.py`

## 3. v4.3.1 review corrections

| ID | Correction | Audit path |
|---|---|---|
| R3-P0-1 | Complete chunk text and authoring packet removed from latest public tree; hashes retained; Git-history cleanup remains a PI decision | `L1_CONTROLLED_ASSET_MANIFEST.json`, citation README, `PUBLICATION_SCOPE.md` |
| R3-P0-2 | Asset register now exposes `data_class` and `release_status`; raw evidence defaults to protected `L1` | asset register and refresh script |
| R3-P1-1 | Intended-run denominator and six terminal statuses fixed; missing raw runs cannot be fabricated | `FAILURE_DENOMINATOR_MATRIX.md` |
| R3-P1-2 | Human corrections become append-only with original/revised values and approval | score issue template, expert protocol |
| R3-P1-3 | Machine judges and J3 share one field-scoped schema, including `answer_span_quote` | prompts, config, template, aggregator, tests |
| R3-P1-4 | UNANSWERABLE additional background facts and fatal-error categories closed | codebook, rubric, prompts |
| R3-P1-5 | citation60 described as five corpus sources, not representative public citrus corpora | citation README, plan, manuscript plan |
| R3-P1-6 | B1/B2 hard resource caps and actual accounting defined | model config, baseline contract |
| R3-P1-7 | Statistics implementation frozen, including RNG, interval, formulas, and NA rules | statistical implementation spec |
| R3-P1-8 | Expert batching separates the three condition packets for each question | expert protocol and codebook |
| R3-P2-1 | B0 adapter change recorded as legacy environment-template rename only, not runtime implementation | decision record and baseline contract |
| R3-P0-3 | Public citation anchors audited against protected text: 60/60 per file, max 46 raw / 49 normalized contiguous characters, none at the 50-character failure threshold or copied as a complete sentence/paragraph | `12_reproducibility/CITATION60_ANCHOR_RELEASE_AUDIT.md`, `scripts/E01_chunk_citation/60_audit_release_anchors.py` |
| R3-CLEAN-1 | Synthetic dry-run fixtures regenerated with `evidence_traceable`, field-scoped reasons, `answer_span_quote`, no model-written decision, and J3 disputes only | `scripts/S00_simulated_dry_run/00_generate_simulated_data.py`, `06_runs/simulated_dry_run`, `09_analysis/simulated_dry_run` |

## 4. Data release boundary

| Class | GitHub |
|---|---|
| `L0` credentials, cookies, authentication data | prohibited everywhere |
| `L1` full/restricted source text, private SOPs, identity maps, unreviewed raw runs/logs | protected server storage; public SHA/locator/redaction lineage only |
| `L2` reviewed questions/metadata, redacted run derivatives, SHA, scores, statistics, manuscript materials | allowed only after file-level release review |

Public-tree facts for this revision:

- `03_benchmarks/citation60/chunks_FROZEN.jsonl` is absent;
- `03_benchmarks/citation60/question_authoring_packet.jsonl` is absent;
- their protected SHA-256 values match the external-review finding;
- citation60 derived data files remain `PENDING_RELEASE_REVIEW` under G12;
- synthetic fixtures remain visibly labelled and are not formal results;
- Git history still contains old objects and the PI/repository-owner decision
  on access restriction or GitHub removal is pending.

No force push or history rewrite is performed.

## 5. Frozen scoring boundary

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
the final outcome. Infrastructure failure is represented separately and is not
called a factual or fatal error.

## 6. Failure and denominator facts

The planned run count is fixed before execution. `completed`, `partial`,
`no_results`, `failed`, `cancelled`, and `skipped` have distinct treatment.
`RAW_RUN_MISSING` retains the slot but fails the strict gate and cannot be
filled with a synthetic observation. See
`09_analysis/FAILURE_DENOMINATOR_MATRIX.md`.

## 7. Primary statistics

Unique confirmatory comparison: `B2 vs B1`, human-consensus EGTS.

- two-sided exact McNemar;
- paired difference in percentage points;
- 10,000 question-within-stratum paired bootstrap replicates;
- NumPy `PCG64(20261104)`, alpha `0.05`;
- percentile 95% intervals `[0.025, 0.975]`;
- L/V/W/X/P/N strata;
- all B0/B1/B2 rows retained for a resampled question;
- complete 2x2 transition table.

`B1 vs B0` and `B2 vs B0` are descriptive only. B0 is not a fair RAG comparator.
Agreement details and NA handling are in
`STATISTICAL_IMPLEMENTATION_SPEC.md`.

## 8. Expert and machine independence

- Two experts independently score all 36 Pilot and 180 formal answers.
- A third human expert adjudicates every disputed field.
- Formal packets are delivered in six batches of 30; one question's three
  condition packets are in three different batches.
- Human consensus is the primary QA60 outcome.
- J1/J2 score every `completed`, `partial`, and `no_results` run among the 180
  intended slots; authentic infrastructure terminal states use deterministic
  records, and J3 adjudicates only field-level machine disagreement.
- GLM, MiMo, and MiniMax share one OpenCode Go service entry point.
- Judges do not use the Agent family, endpoint, or runtime credential.
- Machine output contains no decision field.

## 9. Static review checks required for this commit

- `python scripts/audit_static_package.py`;
- `git diff --check`;
- Python compile check for changed scripts;
- JSON parse for every changed JSON file;
- CSV header, row-count, and row-width validation;
- Markdown fence and active-document consistency checks;
- retired `evidence_used`, model-written decision, `reason_codes_json`, old
  `OPENAI_COMPAT_*` runtime, and `five public citrus corpora` search;
- public-tree search proving both L1 text files are absent;
- public anchor audit proving no contiguous excerpt reaches 50 characters;
- L1 manifest bytes/SHA check against the retained originals;
- secret scan for Bailian/OpenCode/Authorization signatures and `.env`;
- gate-state check proving `G01=BLOCKED`, `G02-G15=PENDING`, and no PASS.

No server, SSH, database, model API, Pilot, formal experiment, scoring, or
analysis command belongs to this review unit.

## 10. Experiment inventory

| ID | Experiment | Planned units | Current evidence |
|---|---|---:|---|
| `S00_SIMULATED_DRY_RUN` | interface regression | 180+180+192+24+7 | synthetic only |
| `P00_PILOT` | independent calibration | 12x3=36 | `NOT_STARTED` |
| `E01_QA60_EGTS` | core answer quality | 60x3=180 | frozen candidate |
| `E01_SUPP_CHUNK_CITATION60` | target locator | 60x3=180 | frozen candidate; text protected |
| `E02_ROUTE192` | observed action compliance | 192 | candidate |
| `E03_MULTI24` | composite tool tasks | 24 | candidate |
| `E04_CITATION_REPEAT72` | optional repeat sensitivity | 72 | optional |
| `E05`-`E11` | WF01-WF07 | 7 cases, 40 criteria | protocol only |
| `U01_USER12` | optional user study | about 12 | ethics-dependent |

Core QA60 and citation60 are never pooled. Citation60 does not receive a
semantic-support rating.

## 11. Paper assets

- `11_manuscript/storyline_and_writing_plan.md`;
- `11_manuscript/manuscript_skeleton.md`;
- `11_manuscript/claim_source_map_TEMPLATE.csv`;
- `11_manuscript/table_figure_provenance_TEMPLATE.csv`;
- `10_figures/figure_plan.md`;
- `00_protocol/05_论文写作执行计划.md`.

No manuscript number is valid until it is generated by a declared strict
analysis and linked through `analysis_manifest.json` and the claim-source map.

## 12. Stop condition

This revision ends after commit and push. Wait for external GPT review. Do not
run Pilot or any model/API command merely because static checks pass.
