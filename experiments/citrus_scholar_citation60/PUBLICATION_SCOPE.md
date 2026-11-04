# Public release scope

## Included

- Experiment protocol, gates, retention ledger and manuscript plan.
- Core QA60, supplementary citation60, routing, composite-task and workflow
  definitions that contain questions and metadata rather than corpus text.
- The unified 120-question, two-reviewer validity packet and adjudication
  template. These contain no completed reviewer identities or judgments.
- Core QA60 machine-judge templates, prompt metadata and deterministic
  aggregation configuration. Reviewer and judge signatures remain blank.
- Python orchestration, validation, adapter templates and public regression
  tests that do not require corpus text.
- Frozen hashes, source manifests, sampling metadata and target identifiers.
- Synthetic spreadsheet fixtures used only for product testing.
- Public frozen questions and target identifiers; no target chunk text.

## Deliberately excluded

- `03_benchmarks/citation60/chunks_FROZEN.jsonl`
- `03_benchmarks/citation60/question_authoring_packet.jsonl`
- downloaded papers, PDFs, private SOPs and index databases
- `06_runs/raw/`, SSE logs, tool traces and model answers
- completed validity reviews, reviewer identities, adjudications and result tables
- raw LLM-judge requests/responses, judge prompts containing restricted text,
  and real judge scores or adjudication notes
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
The public package check validates all 120 formal question identities, the
240-row two-reviewer packet, the 180-row core QA judge template, the
supplementary 180-row runtime plan and every available frozen hash. It reports
`PRIVATE_CORPUS_REQUIRED` for checks that can only run after licensed chunks are
restored. No public script contains or reconstructs the excluded source text.
