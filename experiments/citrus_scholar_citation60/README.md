# CitrusScholar real-agent experiment toolkit

Protocol version: `4.4.0`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
Execution in this review commit: `NONE`
Formal experiment: `NOT_STARTED`

This package evaluates two non-interchangeable E01 components:

- `E01_QA60_EGTS`: 60 questions, 52 answerable and 8 unanswerable; real Agent
  answers are scored by two independent experts and a third-expert field
  adjudicator, with GLM/MiMo/MiniMax as a separate automated measurement.
- `E01_SUPP_CHUNK_CITATION60`: 60 frozen questions with one frozen target chunk
  each and a deterministic target-chunk citation endpoint.

The two components have separate run plans, denominators, scoring and results.
They are never pooled.

The v4.4.0 execution model is authoritative:

- questions are normal Agent input;
- the Agent may retrieve and read the complete registered corpus through
  CitrusScholar;
- normal retrieved source text, chunk IDs and excerpts are valid evidence;
- an L1 root is not an Agent execution prerequisite;
- different questions, conditions and repeats use new sessions;
- all real request/SSE/tool/retrieval/log/answer/artifact evidence is retained;
- synthetic fixtures remain interface tests only.

The repository does not contain the complete corpus. It records source
registry, index generation, hashes, accepted metadata, retrieval evidence and
reviewed derivatives.

Read in this order:

1. `AUDIT_INDEX.md`
2. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4.md`
3. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
4. `00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md`
5. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
6. `00_protocol/02_实验执行与论文写作总计划.md`
7. `00_protocol/05_论文写作执行计划.md`
8. `scripts/A00_execution_readiness/README.md`

This commit is a pre-execution review baseline. Do not call a model, create an
experiment account, run a Pilot, or create formal results before external GPT
review of this exact commit/tree.
