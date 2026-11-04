# A01 LLM judge

> **v4.3 review note:** This describes legacy code and is not executable for
> the current design. The planned judge families are GLM, MiMo, and MiniMax via
> one OpenCode Go service entry point; human expert consensus remains the QA60
> primary measurement. J3 returns only disputed fields; model-written final
> decisions are invalid.
> Configuration, prompts, and runtime code are intentionally unchanged in this
> documentation-only review unit and must be aligned in a later reviewed
> implementation work unit. Do not run these commands yet.

Core QA60 is scored by two frozen, condition-blinded LLM judges. A third
frozen LLM adjudicator is called only when the first two decisions disagree.
Raw judge responses stay under `06_runs/judge_raw/` and aggregation writes
`07_scoring/judge/qa_judge_aggregated.csv`.

The three call stages are intentionally separate so reruns create new
attempts rather than overwriting old evidence.

Credentials are read only from the environment variables named in
`05_baselines/judge_config.json`. No key belongs in a packet, raw response,
log or Git file.

```powershell
python scripts/A01_llm_judge/00_freeze_judge_config.py --check
python scripts/A01_llm_judge/10_build_blind_packets.py
python scripts/A01_llm_judge/20_dispatch_judge_1_and_2.py --dry-run
python scripts/A01_llm_judge/20_dispatch_judge_1_and_2.py --execute
python scripts/A01_llm_judge/30_adjudicate_disagreements.py --dry-run
python scripts/A01_llm_judge/30_adjudicate_disagreements.py --execute
python scripts/A01_llm_judge/40_aggregate_decisions.py --strict
```

`--execute` requires a `READY` judge configuration and environment-provided
credentials. A missing or malformed judge response is retained as an error and
blocks strict aggregation.
