# Unified question-validity and machine-judge protocol

Version: `4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`
Current decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`
Historical decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_1.md`
Scoring authority: `00_protocol/EXPERT_SCORING_PROTOCOL.md`

This version supersedes prior text that allowed a model-written final
decision, required a Gold Answer, omitted field-level human adjudication,
treated all experiment data as unconditionally public, or bound a complete
human analysis to a failed machine-judge service.

## 1. Retained study design

1. `E01_QA60_EGTS` and `E01_SUPP_CHUNK_CITATION60` are both formal
   experiments. They have separate questions, runs, endpoints, denominators,
   analyses, and result tables and are never pooled.
2. No Gold Answer or Gold Claim is created.
3. All 120 questions receive two independent domain-expert validity
   certifications. Reviewers may be trained graduate students. Any
   disagreement is adjudicated by the PI or a third domain expert.
4. Core QA60 has 60 questions: 52 `ANSWERABLE` and 8 `UNANSWERABLE`.
5. Citation60 has 60 questions, each bound to exactly one frozen target
   chunk. Its only formal endpoint is deterministic target-chunk inclusion in
   the final answer's `citations` or `sources`.
6. Human consensus from two independent experts and field-level third-expert
   adjudication is the QA60 semantic endpoint. The confirmatory system endpoint
   `egts_system_outcome` also retains authentic infrastructure failures.
7. J1, J2, and J3 are a separate automated measurement. No LLM replaces a
   human adjudicator.

## 2. Core QA60 scoring and outcome

The authoritative operational definitions are in
`00_protocol/EXPERT_SCORING_PROTOCOL.md`. The scored fields are:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

For `ANSWERABLE`:

```text
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error
```

For `UNANSWERABLE`:

```text
answer_correct = not_applicable
evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

Every condition reports `egts_system_outcome` / 60 planned slots, the separately
scored `human_consensus_egts` coverage and rate, answerability / 60, answer
correctness / 52, evidence traceability / 52, and fatal-error rate / 60. NA is
retained as NA and is never filled with zero. No expert or model enters the
final EGTS or PASS/FAIL.

For `UNANSWERABLE`, a clear refusal of the targeted missing fact remains the
main success behavior. Additional substantive background facts must be
explicitly distinguished from the target fact and checked for factual and
evidentiary consequence; they cannot vanish from evaluation merely because
the two answer-quality fields are `not_applicable`.

The unique confirmatory comparison is `B2 vs B1` on `egts_system_outcome` using
a two-sided exact McNemar test and a 10,000-replicate, six-stratum paired
bootstrap retaining the B0/B1/B2 triplet for each resampled question.
`human_consensus_egts` is reported separately over authentically scorable
answers with coverage and infrastructure failures disclosed. Full
rules are in `09_analysis/analysis_contract.md`; exact interval, RNG,
applicability, AC1, and kappa implementation is in
`12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`.

## 3. Human expert workflow

1. The server creates one condition-blinded packet per answer with a random
   `BLIND_######` ID.
2. It independently shuffles packet order for each expert.
3. Each expert receives the question, expected answerability label, permitted
   source scope, final answer, normalized citations, common frozen reference
   evidence, codebook, score template, issue log, and completion checklist.
4. The packet must not reveal condition, provider, model, adapter, run ID,
   question ID, stratum code, target-chunk ID, machine score, or the other
   expert's result.
5. The system's own evidence summary cannot be the only reference evidence.
   Each reference passage must link to source identity, version or SHA, and a
   page/section/paragraph/chunk locator.
6. Both experts score all 36 Pilot answers and all 180 formal QA60 answers.
7. A mismatch on any of the four fields triggers third-expert adjudication for
   each disputed field only. The two original scores remain unchanged.
8. One attestation is retained for each reviewer and batch. It records
   independence, no external search, no consultation, codebook training, and
   the packet-manifest SHA.

Formal scoring is delivered in six batches of 30 packets. The B0/B1/B2 packet
for one question are placed in three distinct batches; no batch repeats a
question. Pilot uses three batches of 12. Reviewers do not receive another
condition's answer and may not compare conditions across batches.

Required tracked schemas:

- `07_scoring/expert/expert_scoring_TEMPLATE.csv`;
- `07_scoring/expert/expert_adjudication_TEMPLATE.csv`;
- `07_scoring/expert/score_issue_log_TEMPLATE.csv`;
- `07_scoring/expert/human_consensus_TEMPLATE.csv`;
- `07_scoring/expert/reviewer_attestation_TEMPLATE.json`.

The fixed reason-code vocabulary is in
`07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`. Reason codes are assigned
under the field they explain in `reason_codes_by_field_json`. Free-text new
categories, `OTHER`, and reviewer-created fatal categories are prohibited in
formal scoring. Every substantive false field and every `fatal_error=true`
needs a non-empty short rationale and answer span; `fatal_error=false` does not
itself require either.

## 4. Machine-judge roles

The candidate judge models are:

| Role | Candidate ID | Family | Scope |
|---|---|---|---|
| J1 | `glm-5.3-flash` | GLM | Independent score of all 180 core QA60 answers |
| J2 | `mimo-v2.6-flash` | MiMo | Independent score of all 180 core QA60 answers |
| J3 | `minimax-m3.1-flash` | MiniMax | Field-level adjudication only |

All three are routed through one OpenCode Go OpenAI-compatible service entry
point. The paper must describe them as three model families through one
service, not as three independent service providers.

The Agent is a separate Alibaba Cloud Bailian candidate,
`deepseek-v4.1-flash`. Judge calls use a different endpoint and different
credential variables. A judge configuration that shares the Agent model
family, endpoint, or runtime key is a blocking error.

All model IDs are candidates until the server probe records the actual
response model, version/fingerprint where available, region/workspace, date,
terms confirmation, and request evidence. No status may be `READY` before
that evidence and Pilot calibration are complete.

## 5. J1/J2/J3 output contract

J1 or J2 returns exactly one JSON object with only:

```json
{
  "answerability_correct": true,
  "answer_correct": true,
  "evidence_traceable": true,
  "fatal_error": false,
  "reason_codes_by_field": {
    "answerability_correct": [],
    "answer_correct": [],
    "evidence_traceable": [],
    "fatal_error": []
  },
  "short_reason": "",
  "answer_span_quote": "",
  "evidence_span_locators": []
}
```

For `UNANSWERABLE`, `answer_correct` and `evidence_traceable` are
`not_applicable`. A model-generated final `decision`, score, or PASS/FAIL is
invalid and must be rejected.

J1 and J2 compare the four fields only. If all four match, J3 is
`NOT_REQUIRED`. If any field differs, J3 receives the same anonymous packet
and both anonymized structured judgments and returns only the disputed fields:

```json
{
  "disputed_values": {
    "answer_correct": true
  },
  "reason_codes_by_field": {
    "answer_correct": ["FACTUAL_ERROR"]
  },
  "short_reason": "",
  "answer_span_quote": "",
  "evidence_span_locators": []
}
```

J3 cannot change an agreed field. J3 failure leaves that run `BLOCKED`; there
is no majority vote, average, or fallback to J1 or J2.

## 6. Blinding and reference evidence

- J1 and J2 run independently and do not see each other's outputs.
- J3 sees only the anonymous packet and anonymized J1/J2 structures.
- No judge sees B0/B1/B2, provider, model, adapter, run ID, question ID,
  stratum, target-chunk ID, human score, or another judge's provider name.
- The same question receives identical reference evidence under all
  conditions.
- The reference packet is built from frozen source evidence, not only from the
  system's own excerpts.
- Judges do not browse, call tools, consult external sources, or use hidden
  memory to fill missing evidence.

## 7. Retention and failure handling

Retain each attempt separately:

- redacted request body;
- complete safe raw response body;
- parsed JSON and schema-validation result;
- requested model and actual response model/version/fingerprint;
- region/workspace, request ID, date, temperature, seed, maximum output tokens,
  timeout, retry setting, prompt SHA, packet SHA, request SHA, response SHA;
- token usage, cost, list cost, allowance, elapsed time, HTTP status, attempt
  number, UTC timestamps, and error;
- J1/J2/J3 raw records and explicit `NOT_REQUIRED` where applicable;
- blind-ID mapping outside the judge packet;
- 180-row aggregate and complete analysis lineage.

HTTP error, timeout, empty response, invalid JSON, schema failure, invalid
locator, duplicate attempt, or missing response is `JUDGE_ERROR` or
`INVALID_SCHEMA`. It is not zero or success. A new attempt gets a new immutable
file.

Only content-free transport failures can be retried, at most twice. A response
with substantive content is never retried because of quality.

## 8. Pilot and model freeze

The independent Pilot is 12 non-formal questions: L/V/W/X/P/N, two each, run
under B0/B1/B2 for 36 answers. Both experts and J1/J2 score all 36; J3
adjudicates disagreements. Challenge cases cover correct refusal, wrong
refusal, fabricated DOI/citation, fabricated Ct/sample count, source mismatch,
unsupported evidence, overgeneralization, privacy/secret disclosure, private
SOP disclosure, ordinary factual-error negative cases, empty response,
timeout, and invalid schema. Fatal challenge fixtures report false negatives
and false positives and never enter formal QA60 results.

No formal question is used for debugging. Pilot data never enters formal
denominators. If human agreement or fatal-error sensitivity fails, the
codebook/training is revised, the previous version is retained, and a new
independent calibration sample is used.

Only after Pilot and external review may the model IDs, prompts, packet
builder, schemas, run plan, and analysis script be frozen.

## 9. Public and restricted retention

The repository is Private. The `L0/L1/L2` classes in `PUBLICATION_SCOPE.md`
control release:

- `L0` secrets and private authentication data are prohibited everywhere.
- `L1` non-redistributable full text, restricted excerpts, private SOPs,
  identity maps, and raw sensitive or unchecked logs stay in protected server
  storage. Raw runs are controlled `L1` by default, not automatically `L2`.
- `L2` reviewed questions, evidence locators, redacted run records, hashes,
  scores, statistics, and manuscript materials may enter GitHub only after
  release review.

All raw evidence is retained, but not every raw record is publicly
redistributable. Public derivatives include source SHA, locator, access class,
redaction lineage, and derivative SHA.

## 10. Lock conditions

Formal execution requires:

1. all G01-G15 states and evidence satisfy their own acceptance criteria;
2. two independent validity reviews and all adjudications cover 120/120
   questions;
3. core QA60 and citation60 frozen inputs and run plans are locked;
4. B1/B2 model, source, attachment, parser, permission, and retrieval-budget
   fairness is evidenced, including shared hard caps and actual model-call,
   token, cost, elapsed-time, retrieval, context, and tool-use coverage;
5. expert packet, score schema, adjudication schema, and blind-map tests pass;
6. candidate Agent and judges have server response identity and terms
   evidence;
7. Pilot thresholds pass and the final protocol/config/script manifest is
   frozen;
8. a fresh secret scan reports no L0 content in the release package.

The complete run-status/denominator rules are in
`09_analysis/FAILURE_DENOMINATOR_MATRIX.md`.

No plan, template, passing static test, or model probe is itself gate evidence.
