# CitrusScholar experiment repository audit index

This file is the entry point for an external auditor or GPT reviewing the
experiment and paper package. It is not a results summary.

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
| Manuscript | Planning and templates only; no fabricated Results |

## Read in this order

1. `00_protocol/02_实验执行与论文写作总计划.md`
2. `00_protocol/05_论文写作执行计划.md`
3. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
4. `05_baselines/baseline_contract.md`
5. `11_manuscript/storyline_and_writing_plan.md`
6. `11_manuscript/manuscript_skeleton.md`
7. `09_analysis/analysis_contract.md`
8. `12_reproducibility/data_dictionary.md`
9. `PUBLICATION_SCOPE.md`

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
3. Confirm no Gold Answer set is active.
4. Confirm citation60 checks only final cited chunk IDs, not retrieval traces.
5. Confirm semantic-support rate is absent.
6. Confirm B0/B1/B2 share the same model, source scope and attachment boundary.
7. Confirm simulated records cannot flow into formal Results.
8. Confirm every numeric result has a raw-data and script lineage.
9. Confirm all failures, cancellations and missing runs stay in the audit.
10. Confirm credentials are absent while experiment data is retained.

## Verification commands

Run from this directory:

```powershell
python scripts/check_package.py
python scripts/E01_chunk_citation/50_verify_freeze.py
python scripts/S00_simulated_dry_run/10_verify_simulated_data.py
python -m pytest -q scripts/E01_qa60_egts scripts/E01_chunk_citation scripts/A01_llm_judge
```

Expected current outcome:

- package check: `PASS`
- frozen citation input check: `PASS`
- synthetic data check: `PASS`
- regression tests: pass
- formal experiment: still `NOT_STARTED`

## Paper assets

- `11_manuscript/storyline_and_writing_plan.md`: argument and section design.
- `11_manuscript/manuscript_skeleton.md`: editable manuscript skeleton.
- `11_manuscript/claim_source_map_TEMPLATE.csv`: claim-to-evidence ledger.
- `11_manuscript/table_figure_provenance_TEMPLATE.csv`: table/figure lineage.
- `11_manuscript/paper_version_registry.csv`: paper version history.
- `11_manuscript/review_response_log.csv`: reviewer-response tracking.
- `10_figures/figure_plan.md`: planned scientific figures.
- `09_analysis/analysis_contract.md`: statistics and prohibited analyses.
