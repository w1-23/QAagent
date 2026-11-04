# CitrusScholar experiment repository audit index

This file is the entry point for an external auditor or GPT reviewing the
experiment and paper package. It is not a results summary.

## Current review checkpoint

The current design amendment is `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-04.md` (`AWAITING_EXTERNAL_GPT_REVIEW`). This submission updates the low-cost three-judge design, full human expert scoring, and server-only execution contract. Do not infer implementation, model connectivity, gate passage, Pilot completion, or formal results from the plan commit.

The two credentials previously pasted in chat are considered exposed and must be rotated before use. All experiment commands, tests, model calls, database access, scoring, and analysis run on `/home/WZQ/citrus_experiment/` after review. The latest known SSH probe to `211.69.141.120:28022` timed out; G01 remains blocked until connectivity and isolation are verified.

The checked-in `05_baselines/model_config.json`, `judge_config.json`, judge prompts, and runtime judge scripts are not updated in this documentation-only review unit. Treat them as legacy/unconfigured and do not execute them. After GPT review, a separate implementation commit must align them with v4.2 and be reviewed before any server probe.

## Current state

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| G01-G15 preflight gates | `PENDING` |
| Core QA60 questions | 60, frozen candidate |
| Supplementary citation60 questions | 60, one target chunk each |
| Citation chunks | 60 frozen records, present and hash-verified |
| Synthetic dry run | Complete and clearly labelled `SIMULATED_DRY_RUN` |
| Validity reviews | Blank 240-row packet; no human decisions yet |
| Model and judge configuration | `UNCONFIGURED` |
| Current provider design | Bailian Agent; OpenCode Go GLM + MiMo + MiniMax judges, pending server probes |
| Core QA60 human scoring | Two experts score all 180; third expert adjudicates field disagreements |
| Current review gate | `AWAITING_EXTERNAL_GPT_REVIEW` |
| Manuscript | Planning and templates only; no fabricated Results |

## Read in this order

1. `00_protocol/02_实验执行与论文写作总计划.md`
2. `00_protocol/05_论文写作执行计划.md`
3. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
4. `00_protocol/DECISION_ADDENDUM_2026-11-04_JUDGE_MODELS_OPENCODE_GO.md`
5. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
6. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
7. `05_baselines/baseline_contract.md`
8. `11_manuscript/storyline_and_writing_plan.md`
9. `11_manuscript/manuscript_skeleton.md`
10. `09_analysis/analysis_contract.md`
11. `12_reproducibility/data_dictionary.md`
12. `PUBLICATION_SCOPE.md`

The expert score-sheet CSV is intentionally header-only in this planning commit. Real question/answer packets are generated only from authentic server Pilot/formal runs; no illustrative answer or synthetic human rating is included.

## Experiment inventory

| ID | Experiment | Planned units | Current evidence class |
|---|---|---:|---|
| `S00_SIMULATED_DRY_RUN` | Full pipeline interface test | 180+180+192+24+7 | `SIMULATED_DRY_RUN` only |
| `P00_PILOT` | Independent pilot | 12 x 3 | `NOT_STARTED` |
| `E01_QA60_EGTS` | Core QA60 answer quality | 60 x 3 = 180 | `FROZEN_INPUT_NOT_RUN` |
| `E01_SUPP_CHUNK_CITATION60` | Supplementary target-chunk citation | 60 x 3 = 180 | `FROZEN_INPUT_NOT_RUN` |
| `E02_ROUTE192` | Observable route compliance | 192 | `CANDIDATE_NOT_RUN` |
| `E03_MULTI24` | Composite tool-set execution | 24 | `CANDIDATE_NOT_RUN` |
| `E04_CITATION_REPEAT72` | Optional repeat sensitivity | 72 | `OPTIONAL_NOT_RUN` |
| `E05`-`E11` | Workflow cases WF01-WF07 | 7 cases, 40 criteria | `PROTOCOL_ONLY` |
| `U01_USER12` | Optional user study | 12 | `ETHICS_DEPENDENT_NOT_RUN` |

## Conditions

- `B0`: model-only response; no corpus, web, attachment or tools.
- `B1`: fair simple RAG using the same model, sources, attachment text and
  retrieval budget as B2, without the agent tool workflow.
- `B2`: the complete CitrusScholar release locked at G01.

## Data classes

| Class | Meaning | May appear in Results |
|---|---|---|
| `SIMULATED_DRY_RUN` | Synthetic interface-test data | No |
| `CANDIDATE` / `UNLOCKED` | Planned or draft input | No |
| `FROZEN_INPUT_NOT_RUN` | Hash-locked inputs without real runs | No |
| `REAL_RUN` | Authentic service/model execution with retained evidence | Yes, after strict analysis |
| `REAL_JUDGE` | Authentic frozen-judge response | Yes, after aggregation |
| `MISSING` | Required evidence unavailable | Must be reported as missing |

## Audit checks

1. Confirm the two E01 question sets remain separate and are never pooled.
2. Confirm all 120 questions have two independent validity-review slots.
3. Confirm two experts independently score all Pilot and formal QA60 outputs and a third adjudicates any disputed rubric fields.
4. Confirm expert packets include common frozen reference evidence and conceal condition, model, run/question IDs, LLM scores, and the other reviewer's ratings.
5. Confirm no Gold Answer set is active.
6. Confirm citation60 checks only final cited chunk IDs, not retrieval traces.
7. Confirm semantic-support rate is absent.
8. Confirm B0/B1/B2 share the same Agent model and frozen generation settings.
9. Confirm simulated records cannot flow into formal Results.
10. Confirm every numeric result has a raw-data and script lineage, and all failures remain in the audit.
11. Confirm credentials and restricted identity linkage are absent while permitted experiment data and results are retained.

## Verification commands

Run from the experiment server checkout, never from the local workstation:

```bash
python scripts/check_package.py
python scripts/E01_chunk_citation/50_verify_freeze.py
python scripts/S00_simulated_dry_run/10_verify_simulated_data.py
python -m pytest -q scripts/E01_qa60_egts scripts/E01_chunk_citation scripts/A01_llm_judge
```

The last recorded pre-amendment checks were package `PASS`, citation freeze `PASS`, simulated dry-run `PASS`, and 21 regression tests passed. Those historical results do not test this amendment. Re-run the checks on the experiment server after the current review checkpoint; formal experiment status remains `NOT_STARTED`.

## Paper assets

- `11_manuscript/storyline_and_writing_plan.md`: argument and section design.
- `11_manuscript/manuscript_skeleton.md`: editable manuscript skeleton.
- `11_manuscript/claim_source_map_TEMPLATE.csv`: claim-to-evidence ledger.
- `11_manuscript/table_figure_provenance_TEMPLATE.csv`: table/figure lineage.
- `11_manuscript/paper_version_registry.csv`: paper version history.
- `11_manuscript/review_response_log.csv`: reviewer-response tracking.
- `07_scoring/expert/`: blank expert score schema and instructions; response packets are built and scored on the server.
- `10_figures/figure_plan.md`: planned scientific figures.
- `09_analysis/analysis_contract.md`: statistics and prohibited analyses.
