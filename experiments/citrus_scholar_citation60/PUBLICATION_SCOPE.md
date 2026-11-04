# Public release scope

## Included

- Experiment protocol, gates, retention ledger and manuscript plan.
- Core QA60, supplementary citation60, routing, composite-task and workflow
  definitions that contain questions and metadata rather than corpus text.
- Python orchestration, validation, adapter templates and local tests.
- Frozen hashes, source manifests, sampling metadata and target identifiers.
- Synthetic spreadsheet fixtures used only for product testing.
- Scoring templates with no real participant or reviewer judgments.

## Deliberately excluded

- `03_benchmarks/citation60/chunks_FROZEN.jsonl`
- `03_benchmarks/citation60/question_authoring_packet.jsonl`
- downloaded papers, PDFs, private SOPs and index databases
- `06_runs/raw/`, SSE logs, tool traces and model answers
- completed human ratings, adjudications and result tables
- model credentials, cookies, account information and private endpoints

The excluded chunk files contain source-corpus text and must be restored only
under the source license and data-access agreement used for the study. Their
expected SHA-256 values remain in `questions_FROZEN.manifest.json` and
`README_FROZEN.md` so a local restore can be checked before running.

## Running with restored local inputs

1. Restore the excluded frozen files into `03_benchmarks/citation60/`.
2. Verify `chunks_FROZEN.jsonl` against the recorded SHA-256.
3. Configure B0/B1/B2 according to `05_baselines/model_config.json`.
4. Run `python scripts/E01_chunk_citation/50_verify_freeze.py`.
5. Run `python -m pytest -q scripts/E01_chunk_citation/tests`.

Without the excluded files, tests that require frozen corpus text are skipped.
No public script contains or reconstructs the excluded source text.
