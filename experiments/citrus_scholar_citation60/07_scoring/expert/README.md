# Human expert scoring materials v4.3.1

Status: `AWAITING_EXTERNAL_GPT_REVIEW`

Human consensus is the primary answer-quality measurement for core QA60. Two
experts independently score every Pilot answer (36) and every formal QA60
answer (180). A third expert adjudicates each disputed field only. The
authoritative rules are in `00_protocol/EXPERT_SCORING_PROTOCOL.md`.

This directory contains blank schemas and instructions. It contains no answer
packet, Gold Answer, real rating, or adjudication. Server-side packet builders
may create real packets only after authentic runs and common frozen reference
evidence exist.

## Files

- `expert_scoring_TEMPLATE.csv`: primary score schema;
- `expert_adjudication_TEMPLATE.csv`: one row per disputed field;
- `score_issue_log_TEMPLATE.csv`: missing/invalid/unrateable/correction log;
- `human_consensus_TEMPLATE.csv`: deterministic derived consensus with lineage;
- `reviewer_attestation_TEMPLATE.json`: one independence attestation per
  reviewer and batch;
- `SCORING_CODEBOOK_v4.3.1.md`: operational definitions, fixed reason codes, and
  training cases.

## Scoring fields

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

For `ANSWERABLE`, all four are Boolean. For `UNANSWERABLE`, `answer_correct`
and `evidence_traceable` must be `not_applicable`. The expert never enters
EGTS or PASS/FAIL.

Every substantive false field and every fatal error requires a fixed
field-scoped reason code, a non-empty short rationale, an answer-span quote,
and evidence locators where applicable. `fatal_error=false` is the ordinary
no-fatal result and does not itself require a reason or quote.
`reason_codes_by_field_json` is a JSON object keyed by the four fields; each
value is an array. `evidence_span_locators_json` is a JSON array. There is no
formal `OTHER` category.

## Blinding and packets

Each real packet has a random `BLIND_######` identifier. Reviewer packet order
is independently shuffled. Formal QA60 is delivered in six batches of 30
packets, with the three conditions for one question in distinct batches so
no batch repeats a question. Pilot uses three batches of 12. A packet contains
the question, expected
answerability label, allowed-source scope, final answer, normalized citations,
common frozen reference evidence, scoring instructions, score sheet, issue
log, and completion checklist.

It must not contain condition, provider, model, adapter, run ID, question ID,
stratum, target-chunk ID, machine score, another reviewer's rating, or a
semantic map that reveals the condition. The system's own evidence summary
cannot be the only reference evidence.

## Data handling

Preserve every returned file byte-for-byte and record its SHA. Validation and
consensus outputs are separate files. Never edit a returned reviewer file in
place. A correction creates a new linked record and issue ID and records the
original score record ID, field, original/revised values, reason, reviewer,
UTC, and approval record.

Reviewer identity linkage is `L1` and remains in protected server storage.
Public packets and derivatives use pseudonymous IDs and pass the release checks
in `PUBLICATION_SCOPE.md`. Restricted source excerpts are delivered through
approved server access and represented publicly by SHA, locator, access class,
and redaction lineage.

## Independence

Reviewers must not browse the web, call tools, consult another reviewer, inspect
machine scores, infer the condition, or access a Gold Answer. Any condition
guess is recorded after submission as a blinding-risk observation and cannot
change the original score.
