# CitrusScholar experiment toolkit

Protocol version: `4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`

This package contains the executable experiment library and planning artifacts
for two non-interchangeable E01 components:

- **Core:** `E01_QA60_EGTS`, the retained 60-question L/V/W/X/P/N design with
  52 answerable and 8 unanswerable items. Human consensus from two independent
  expert ratings and third-expert adjudication is primary; three LLM judges
  provide a separate automated measurement.
- **Supplementary:** `E01_SUPP_CHUNK_CITATION60`, 60 protected frozen questions
  derived from five corpus-source categories, with exactly one frozen target
  chunk per question and a deterministic target-chunk citation endpoint. Until
  G12 review is complete, only the safe notice and protected-asset manifest
  remain in the audited tree.

The two datasets have separate run plans, denominators, scoring files and
reported results. They must never be pooled or used as substitutes for one
another.

All 120 formal questions receive two independent human validity certifications.
For QA60 answers, two experts independently score all 36 Pilot and 180 formal
responses; a third expert adjudicates field disagreements. No Gold Answer set is
created, and semantic-support rate is explicitly not reported.

The current package contains reviewed candidate code only. Execution remains
closed because status is not `READY`, `execution_authorized` is false, and the
external review is pending. Do not run experiments, model calls, Pilot, or
formal analyses on the local workstation. All execution is server-only in the
isolated experiment instance described in
`00_protocol/SERVER_EXECUTION_PROTOCOL.md`.

Start with `00_protocol/02_实验执行与论文写作总计划.md`. The immediate manual
blockers are in `00_protocol/04_需要PI确认与人工执行的清单.md`.
Experiment-specific entry points are listed in `scripts/README.md`.
The full repository and manuscript audit starts at `AUDIT_INDEX.md`, with the
operational paper workflow in `00_protocol/05_论文写作执行计划.md`.

All study evidence must be retained, but repository/release handling follows the `L0/L1/L2`
classes in `PUBLICATION_SCOPE.md`. `L0` credentials never enter Git; `L1`
restricted full text/SOPs/identity maps/raw sensitive logs remain in protected
server storage; `L2` redacted/public-safe questions, evidence, runs, scores,
statistics, and manuscript materials may enter Git only after release review.

The two complete citation60 inputs were removed from the latest audited tree.
Earlier Git objects still require a PI/repository-owner access or history
decision; no force push or history rewrite is authorized.
