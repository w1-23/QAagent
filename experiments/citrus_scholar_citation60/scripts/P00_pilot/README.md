# P00 pilot

Protocol version: `4.4.2`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_2`
Execution: `NONE`

The versioned Pilot source is
`03_benchmarks/pilot/pilot12_v4_4_2.csv`. It declares 12 questions, exactly two
per `L/V/W/X/P/N` stratum, and the runner expands them to 36 immutable
B0/B1/B2 runs. The older `pilot12.csv` is retained as history and is not the
active source.

The current v4.4.2 source is a material-declaration candidate:
`availability_status=NOT_VERIFIED`. A run plan may be generated for schema
validation, but execution remains `BLOCKED` until each source/chunk reference,
locator and excerpt is verified and frozen. Declarations must not be presented
as real materials.

Commands:

```bash
python scripts/P00_pilot/10_make_pilot_runplan.py \
  --source 03_benchmarks/pilot/pilot12_v4_4_2.csv \
  --out <server-evidence-root>/pilot12_v4_4_2.runplan.csv

python scripts/P00_pilot/20_run_pilot.py \
  --plan <server-evidence-root>/pilot12_v4_4_2.runplan.csv \
  --stage2-report <approved-stage2-report.json> \
  --g10-report <approved-g10-report.json> \
  --out-root 06_runs/pilot \
  --execute
```

The plan generator refuses overwrite, non-12-question sources, duplicate IDs,
or any stratum count other than two. The runner revalidates all 36 rows and
requires the final reviewed execution authorization, Stage 2 `PASS`, G10
`PASS`, and non-DeepSeek Judge configuration before any adapter call.

The real B0/B1/B2 adapters and their resource counters live in
`05_baselines/adapters/`. B2 additionally requires the loopback provider
capture documented in `scripts/A00_execution_readiness/README.md`.

Formal QA60, Citation60, Route192 and Composite24 questions are prohibited
from this directory. See `01_preflight/SSE_浏览器回归验收手册.md` for the
manual browser evidence still required by G02.
