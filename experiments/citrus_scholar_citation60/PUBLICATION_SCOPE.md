# Experiment package scope

This repository does not impose an experimental corpus-access restriction. The
entire study package is versioned so that the run can be reproduced from inputs
through analysis.

## Included

- Experiment protocol, gates, retention ledger and manuscript plan.
- Core QA60 and supplementary citation60 questions.
- The 60 frozen chunks, their source metadata and the authoring packet.
- Routing, composite-task and workflow definitions.
- Review packets, completed reviews and adjudications once available.
- Python orchestration, validation, adapters and regression tests.
- Raw runs, SSE events, tool traces, model answers and artifacts once available.
- Raw LLM-judge and adjudicator requests/responses, scores and result tables.
- Statistical tables, figures and manuscript source files once available.
- Synthetic spreadsheet fixtures and explicitly labelled simulated dry-run data.

## Excluded

- passwords, API keys, cookies and tokens
- raw personal authentication records
- model weights and runtime caches

Credentials are supplied only through a secure environment or credential
store. They must never be copied into experiment files, logs, prompts or chat.

## Verification

1. Configure B0/B1/B2 according to `05_baselines/model_config.json`.
2. Run `python scripts/E01_chunk_citation/50_verify_freeze.py`.
3. Run `python scripts/check_package.py`.
4. Run `python -m pytest -q scripts/E01_chunk_citation/tests`.

The package check requires the 60 frozen chunk records, all 120 formal question
identities, the 240-row two-reviewer validity packet, the 180-row core-QA judge
template and the supplementary 180-row runtime plan. Missing chunks are a hard
failure, not a normal "private corpus" state.

Files under `06_runs/simulated_dry_run/` and
`09_analysis/simulated_dry_run/` are synthetic interface tests. Every record is
marked `SIMULATED_DRY_RUN` and must never be reported as a study result.
