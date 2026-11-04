# Experiment package scope

No additional corpus-access restriction is imposed by the experiment design.
The authorized study package is versioned from inputs through analysis.
Execution is server-only in the isolated experiment instance; local workstations
are for authoring and review, not experiment, model, test, or analysis runs.

## Included

- Experiment protocol, gates, retention ledger and manuscript plan.
- Core QA60 and supplementary citation60 questions.
- The 60 frozen chunks, their source metadata and the authoring packet.
- Routing, composite-task and workflow definitions.
- Review packets, completed reviews and adjudications once available.
- Python orchestration, validation, adapters and regression tests.
- Raw runs, SSE events, tool traces, model answers and artifacts once available.
- Raw LLM-judge and adjudicator requests/responses, scores and result tables.
- Two independent expert score sheets for every Pilot/formal QA60 response,
  third-expert disagreement adjudications, blinded packets and deterministic
  human-consensus tables.
- Statistical tables, figures and manuscript source files once available.
- Synthetic spreadsheet fixtures and explicitly labelled simulated dry-run data.

## Excluded

- passwords, API keys, cookies and tokens
- raw personal authentication records
- model weights and runtime caches

Credentials are supplied only through a protected server environment file or
credential store. They must never be copied into experiment files, logs,
prompts, screenshots, expert packets, GitHub or reports. Any credential already
exposed in chat must be revoked/rotated before use. Source material must be
released only within the rights/authorization recorded under G12.

## Verification

1. After external GPT review, configure B0/B1/B2 on the isolated server instance according to `05_baselines/model_config.json`.
2. On that server, run `python scripts/E01_chunk_citation/50_verify_freeze.py`.
3. On that server, run `python scripts/check_package.py`.
4. On that server, run `python -m pytest -q scripts/E01_chunk_citation/tests`.

The package check requires the 60 frozen chunk records, all 120 formal question
identities, the 240-row two-reviewer validity packet, expert-scoring materials
and the supplementary 180-row runtime plan. Missing chunks are a hard
failure, not a normal "private corpus" state.

Files under `06_runs/simulated_dry_run/` and
`09_analysis/simulated_dry_run/` are synthetic interface tests. Every record is
marked `SIMULATED_DRY_RUN` and must never be reported as a study result.
