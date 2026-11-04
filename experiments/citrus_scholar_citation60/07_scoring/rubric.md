# Scoring contract

## Human responsibility

Human reviewers certify question validity only. They do not write Gold Answers
and do not score every run. Two reviewers independently complete all 120
questions; disagreements go to a third reviewer or project lead.

## Core QA60

The primary answer-quality measurement is produced by two frozen LLM judges.
Each judge receives a condition-blinded pack containing the question, final
answer, verifiable citations and required source context. It returns structured
fields:

```text
answerability_correct
answer_correct
evidence_used
fatal_error
decision
reason_code
short_reason
evidence_span_locators
```

The deterministic fields in the final score table are:

- `answer_correct_1_0`: aggregate pass/fail.
- `answerability_correct_1_0`: abstention/answerability behavior.
- `evidence_used_1_0`: cited evidence was used for the answer; `NA` when the
  correct behavior is abstention.
- `fatal_error_1_0`: any critical contradiction, fabricated evidence or unsafe
  scientific overclaim.
- `judge_1_decision`, `judge_2_decision`, `judge_3_decision`, `final_decision`.

Judge 1 and judge 2 run independently. Agreement becomes the final decision.
Disagreement invokes the pretrained/pinned adjudicator defined in
`05_baselines/judge_config.json`. A failed judge call is `JUDGE_ERROR`, not 0 or
1, and blocks strict analysis until resolved by a new recorded attempt.

## Supplementary citation60

The only formal endpoint is `target_chunk_cited=1`: the final answer's
`citations` or `sources` explicitly contains the frozen `target_chunk_id`.
A retrieval-tool hit without a final citation is 0.

No human semantic-support rating is collected. Semantic support is not reported.
Do not report:

- semantic support;
- `1/0.5/0` support distribution;
- “citation correctness” as a synonym for target-chunk locator hit;
- a human/model claim that the cited passage semantically supports every answer
  statement.

## Routing and workflows

Tool traces, file existence, SHA, status and allowed/forbidden tool sets are
checked deterministically first. A frozen LLM judge may evaluate only the
remaining qualitative criterion and must return a structured `1/0/NA` plus a
short evidence pointer. It cannot override a failed file/SHA or missing-tool
check.
