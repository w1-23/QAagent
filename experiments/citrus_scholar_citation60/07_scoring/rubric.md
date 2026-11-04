# Scoring contract

Version 2.0 design amendment: core QA60 human expert consensus is the primary
answer-quality measurement. Two experts independently score every Pilot and
formal QA60 response; a third expert adjudicates any disputed rubric field.
The full delivery and adjudication rules are in
`00_protocol/EXPERT_SCORING_PROTOCOL.md`.

## Human responsibility

Two distinct human tasks are retained. Two reviewers independently certify
validity of all 120 question items; separately, two experts independently score
all 36 Pilot outputs and all 180 formal QA60 outputs. A third expert
adjudicates any disagreement in the four scored dimensions. Experts do not
write Gold Answers. Use pseudonymous IDs and keep identity linkage separate.

## Core QA60

The primary QA60 outcome is human expert consensus. The frozen GLM and MiMo
judges provide an independent automated measurement; MiniMax adjudicates only
four-field disagreements. Each
receives a condition-blinded packet containing the question, final answer,
normalized citations and the same independently extracted frozen reference
context provided to human scorers. LLM outputs contain only the four rubric
fields plus reason and locator metadata; a script computes all outcomes:

```text
answerability_correct
answer_correct
evidence_used
fatal_error
reason_code
short_reason
evidence_span_locators
```

The deterministic fields in the final score table are:

- `human_consensus_egts`: deterministic aggregate pass/fail from adjudicated
  human fields.
- `llm_consensus_egts`: separately derived from J1/J2 and necessary J3 fields.
- `answerability_correct_1_0`: abstention/answerability behavior.
- `evidence_used_1_0`: cited evidence was used for the answer; `NA` when the
  correct behavior is abstention.
- `fatal_error_1_0`: any critical contradiction, fabricated evidence or unsafe
  scientific overclaim.
- `judge_1_*`, `judge_2_*`, `judge_3_*`: machine dimensions/outcomes, kept
  separately from the human outcome.
- `human_consensus_egts` and `llm_consensus_egts`: separately calculated
  primary and comparator outcomes; do not overwrite one with the other.

For an `ANSWERABLE` question, each expert's outcome is
`answerability_correct AND answer_correct AND evidence_used AND NOT fatal_error`.
For an `UNANSWERABLE` question, `answer_correct` and `evidence_used` must both
be `not_applicable`, and the outcome is
`answerability_correct AND NOT fatal_error`. Correct refusal therefore passes
when answerability is correct and no fatal error is present. Strict validation
rejects all other NA placements and missing adjudications.

For each B0/B1/B2 condition, report human-consensus EGTS / 60, answerability /
60, answer correctness / 52, evidence use / 52, and fatal-error rate / 60.
Report proportions with 95% Wilson intervals; paired comparisons match on
question ID. NA is excluded only from the explicitly specified 52-item
subdimensions, never recoded as zero.

Experts 1 and 2 score independently; a mismatch in any of the four fields
invokes the third human adjudicator for those fields. Do not use majority vote
or average ratings. J1/J2 run independently; disagreement in any of the four
fields invokes J3. Human adjudication and LLM adjudication remain separate.
Missing, invalid, or unadjudicated records block strict analysis.

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
check. If a human workflow rating is required by its locked criterion, retain
the blind packet and reviewer record separately from machine results.
