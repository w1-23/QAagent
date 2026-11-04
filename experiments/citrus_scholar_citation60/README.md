# CitrusScholar experiment toolkit

This package contains the executable experiment library and planning artifacts
for two non-interchangeable E01 components:

- **Core:** `E01_QA60_EGTS`, the retained 60-question L/V/W/X/P/N design with
  52 answerable and 8 unanswerable items, evaluated by two frozen LLM judges
  with a frozen LLM adjudicator.
- **Supplementary:** `E01_SUPP_CHUNK_CITATION60`, 60 questions sampled across
  five public corpora with exactly one frozen target chunk per question and a
  deterministic target-chunk citation endpoint.

The two datasets have separate run plans, denominators, scoring files and
reported results. They must never be pooled or used as substitutes for one
another.

All 120 formal questions receive two independent human validity certifications.
Reviewers may be trained graduate students. No Gold Answer set is created, and
semantic-support rate is explicitly not reported.

Start with `00_protocol/02_实验执行与论文写作总计划.md`. The immediate manual
blockers are in `00_protocol/04_需要PI确认与人工执行的清单.md`.
Experiment-specific entry points and the public/private validation boundary
are listed in `scripts/README.md`.

Public-release exclusions and local restore instructions are documented in
`PUBLICATION_SCOPE.md`.
