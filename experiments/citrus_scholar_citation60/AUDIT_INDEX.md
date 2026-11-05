# CitrusScholar experiment audit index

Protocol version: `4.3.2`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This is the entry point for the fifth external GPT review. It is not a results
summary and is not evidence that a gate, provider, Pilot, or experiment passed.

## 1. Version lineage

| Label | Commit |
|---|---|
| Fourth-review baseline | `0ebdf0fddb0c12518bfdd9d638161462e388b953` |
| v4.3.2 implementation | resolved from the pushed `main` commit for `fix: close fourth review release and execution gaps` |

The fourth review is reproduced in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_2.md`. The latest
decision record is `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_2.md`.

## 2. Current facts

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| Server work | `NOT_PERFORMED_IN_THIS_REVIEW_UNIT` |
| G01-G15 | G01 `BLOCKED`; G02-G15 `PENDING` |
| PASS gates | `0` |
| Agent | Bailian candidate `deepseek-v4.1-flash`, `UNCONFIGURED` |
| Judges | OpenCode Go candidate GLM/MiMo/MiniMax, `UNCONFIGURED` |
| Model/API calls | none in this review unit |
| Core QA60 | 60 candidate questions: 52 answerable, 8 unanswerable |
| Citation60 | protected 60-question asset; public tree contains notice only |
| Validity reviews | blank 120 public QA60 review rows; no decision |
| Human scoring | candidate implementation only; no answer packet or score |
| Machine judging | candidate implementation only; execution blocked |
| Synthetic dry run | visibly labelled interface fixture; never a result |
| Manuscript | plans and templates only |

## 3. Fourth-review corrections

| Finding | Correction | Evidence |
|---|---|---|
| P0 historical text exposure | Latest tree no longer contains citation60 source text or derived benchmark data requiring G12 review. Historical Git objects remain an unresolved PI/repository-owner action. | `PUBLICATION_SCOPE.md`, `L1_CONTROLLED_ASSET_MANIFEST.json`, `03_benchmarks/citation60/README.md` |
| P1 retry boundary | Retry only content-free transport failures and HTTP 408/429/500/502/503/504; maximum two retries. Invalid JSON/schema/model identity never retries. | judge dispatch scripts and tests |
| P1 execution authorization | Every execute path requires `status=READY`, `execution_authorized=true`, and approved review status. | `scripts/execution_authorization.py`, dispatch scripts, tests |
| P1 aggregate storage | Judge aggregation defaults to protected `L1`, rejects public-tree output, and refuses overwrite. | `scripts/aggregate_llm_judges.py` |
| P1 Judge 3 strictness | Exactly five top-level fields are accepted; extra fields fail; evidence locators are retained in the aggregate. | judge prompts, aggregator, tests |
| P1 `.env` scan | All files are scanned and `.env`, `.env.*`, `*.env`, keys, PEM, and credential files are detected. | `scripts/audit_static_package.py`, tests |
| P1 stale analysis entry | The old analysis README is replaced; `analyze_and_plot.py` and the old summary shim fail as `LEGACY_NON_EXECUTABLE`. | `scripts/A01_analysis/README.md`, `run_strict_analysis.py` |
| P1 human scoring chain | Blind materials, score validation, append-only correction, third-expert adjudication, consensus, and agreement are implemented in candidate form. | `scripts/A02_human_scoring/` |
| P1 model identity | Actual response model must match each role allowlist; missing or mismatched identity blocks strict aggregation. | `judge_config.json`, dispatch/aggregation |
| P1 endpoint split | Confirmatory system endpoint is `egts_system_outcome`; semantic endpoint is reported separately with coverage. | analysis contract and analyzer |
| P1 blind-packet integrity | Formal 180 and Pilot 36 counts, exact B0/B1/B2 sets, identical per-question prompts and evidence, and raw-SHA exclusion are enforced. | packet builder and tests |

## 4. Public-release boundary

| Class | GitHub |
|---|---|
| `L0` credentials, cookies, authentication data | prohibited everywhere |
| `L1` restricted text, private SOPs, identity maps, unreviewed raw logs | protected server storage only |
| `L2` reviewed public-safe questions/metadata, redacted derivatives, hashes, scores, statistics, manuscript | allowed only after file-level release review |

Only `03_benchmarks/citation60/README.md` remains in the public citation60
directory. The 16 protected assets, their bytes, SHA-256 values, former paths,
and controls are declared in `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`.
No file marked `PENDING_RELEASE_REVIEW` may remain in the public tree.

The Git repository is public. Earlier commits still contain prior citation60
objects. Closing that exposure requires the PI/repository owner to restrict
access and/or request GitHub history cleanup. This review unit does not rewrite
history or force push.

## 5. Scoring and statistical boundary

Core QA60 uses:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

Experts do not write PASS/FAIL. A deterministic script computes:

```text
ANSWERABLE:
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error

UNANSWERABLE:
answer_correct = evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

The unique confirmatory B2-versus-B1 endpoint is `egts_system_outcome` over 60
planned slots per condition. `human_consensus_egts` is a separate semantic
endpoint for authentically scorable answers; its coverage and infrastructure
failure count must be reported. Agreement uses only authentic applicable
double scores.

Statistics are frozen as exact two-sided McNemar plus 10,000
question-within-six-strata paired bootstrap replicates using NumPy
`PCG64(20261104)` and percentile 95% intervals. Core QA60 and citation60 are
never pooled. citation60 reports deterministic target-chunk locator hit, not
semantic support or answer correctness.

## 6. Authoritative reading order

1. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_2.md`
2. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_2.md`
3. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
4. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
5. `07_scoring/rubric.md`
6. `07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`
7. `09_analysis/analysis_contract.md`
8. `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`
9. `12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`
10. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
11. `PUBLICATION_SCOPE.md`
12. `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`
13. `05_baselines/baseline_contract.md`
14. `00_protocol/02_实验执行与论文写作总计划.md`
15. `00_protocol/05_论文写作执行计划.md`
16. `11_manuscript/storyline_and_writing_plan.md`
17. `12_reproducibility/data_dictionary.md`
18. `00_protocol/change_log.csv`
19. `scripts/README.md`
20. `scripts/audit_static_package.py`

## 7. Review checks

Required offline checks for this commit:

- `python -m compileall -q experiments/citrus_scholar_citation60/scripts`
- offline judge, citation, human-scoring, and analysis tests;
- `python experiments/citrus_scholar_citation60/scripts/check_package.py`;
- `python experiments/citrus_scholar_citation60/scripts/audit_static_package.py`;
- `git diff --check`;
- gate-state, public-tree, credential, and changed-path checks.

Passing static tests does not change a gate or authorize an experiment.

Pre-push local result: `compileall` passed; `53 passed in 2.99s`; package check
passed with 16/16 declared protected assets verified; static audit passed with
256 files secret-scanned; `git diff --check` passed. These are offline checks
only, not server or model evidence.

## 8. Stop condition

This revision ends after commit and ordinary push to `main`. Wait for the fifth
external GPT review. Do not start Pilot, call Agent or Judge APIs, run formal
experiments, generate scores, or create paper result numbers.
