# E01 core QA60

> **v4.3 review note:** This is a legacy implementation description and is not
> executable for the current design. The approved-for-review design makes
> double-expert consensus the primary measurement: two experts independently
> score all 36 Pilot and 180 formal outputs, with a third expert adjudicating
> disputed fields. The four fields are `answerability_correct`,
> `answer_correct`, `evidence_traceable`, and `fatal_error`. GLM/MiMo/MiniMax
> are a separate automated comparison through one OpenCode Go service entry.
> Runtime code and configuration remain unaligned until a later reviewed
> implementation work unit. Do not run these commands.

This directory owns `E01_QA60_EGTS`: 60 locked questions x B0/B1/B2 = 180
runs. Current scoring and execution rules are defined by
`00_protocol/EXPERT_SCORING_PROTOCOL.md`,
`00_protocol/SERVER_EXECUTION_PROTOCOL.md`, and
`00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`.

Execution order:

1. Validate the candidate question schema.
2. Verify the unified 120-question validity lock.
3. Write `qa60_LOCKED.csv` once.
4. Regenerate the run plan from locked input.
5. Run B0/B1/B2 through real adapters.
6. Build reviewer-specific expert and machine-judge packets; dispatch only
   after the review gate and required server configuration are satisfied.
7. Run strict human-consensus and automated-judge aggregation separately.
8. Run strict analysis only from real raw, expert, and judge records.
