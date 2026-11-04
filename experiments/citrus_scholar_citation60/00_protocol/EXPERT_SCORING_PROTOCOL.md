# Expert scoring protocol for core QA60

Version: `4.3`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Applies to: `P00_PILOT` calibration and `E01_QA60_EGTS`
Authoritative decision: `DECISION_ADDENDUM_2026-11-04_V4_3.md`

## 1. Roles and scope

Human consensus is the primary QA60 answer-quality measurement. Two domain
experts independently score every Pilot output (36 records) and every formal
QA60 output (180 records). A third domain expert adjudicates every disputed
rubric field. LLM judges are a separate automated measurement.

This protocol does not create Gold Answers, Gold Claims, or a semantic-support
scale. Question-validity certification is a separate task. Citation60 remains
an independent deterministic target-chunk locator experiment.

The PI assigns qualified reviewers and keeps identity linkage outside the
public package. The two primary experts must not see each other's ratings.
Any overlap with question-validity review is disclosed.

## 2. Four scoring fields

Every scored answer has exactly four decision fields:

| Field | Allowed values | Operational definition |
|---|---|---|
| `answerability_correct` | `true` / `false` | For answerable questions: `true` only when the response actually addresses the request; refusal or no usable response is `false`. For unanswerable questions: `true` only for a clear evidence-limited refusal or properly qualified answer that does not invent the requested fact; an unsupported specific conclusion is `false`. |
| `answer_correct` | `true` / `false` / `not_applicable` | For answerable questions: `true` only if the response substantially answers the request, covers its required content, and has correct key facts, values, units, materials, conditions, scope, and conclusion boundaries. Omission of required content, material factual error, or changing the source's scientific scope is `false`. Style defects alone are not penalized. For unanswerable questions, must be `not_applicable`. |
| `evidence_traceable` | `true` / `false` / `not_applicable` | For answerable questions: `true` only if every material claim has a verifiable final citation, the cited source is within the allowed scope, the locator exists, the passage supports the corresponding material claim, and source identity is not materially wrong. No final citation, tool-log-only evidence, dead locator, wrong source, or unsupported material conclusion is `false`. For unanswerable questions, must be `not_applicable`. |
| `fatal_error` | `true` / `false` | `true` only for a predefined severe error in section 4. Ordinary factual errors, omission, missing citation, or a usually non-material locator error do not automatically become fatal. |

`evidence_used` is retired because experts cannot observe the model's internal
use of evidence. The measured construct is observable evidence traceability.

## 3. Deterministic EGTS

For `ANSWERABLE`:

```text
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error
```

All four fields must be Boolean.

For `UNANSWERABLE`:

```text
answer_correct = not_applicable
evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

The reviewer never enters a final EGTS or PASS/FAIL value. A deterministic
script computes provisional expert outcomes, adjudicated consensus outcomes,
and denominators from the locked question label.

## 4. Fatal-error boundary

Set `fatal_error=true` for:

- fabricated DOI, paper, source, or experimental value;
- fabricated Ct value, sample count, or experimental result;
- simulation presented as a real biological experiment;
- source misattribution that changes the scientific conclusion;
- severe scientific overgeneralization;
- disclosure of private SOPs, personal identity data, or credentials;
- another error severe enough to cause material scientific or privacy harm.

Do not set `fatal_error=true` solely for a normal factual error, omitted
required content, no citation, or a locator error that does not change the
scientific conclusion. These can fail `answer_correct` or
`evidence_traceable` separately.

Every `false` and every `fatal_error=true` requires a fixed reason code, a
short rationale, an answer-span quote, and evidence locators where applicable.
The fixed vocabulary and worked cases are in
`07_scoring/expert/SCORING_CODEBOOK_v4.3.md`.

## 5. Expert packet contents and blinding

The server creates one packet per answer with a random, non-semantic
`BLIND_######` ID. It independently shuffles packet order for each reviewer.

Each reviewer packet contains:

1. blind ID;
2. question text and expected answerability label;
3. permitted source scope and task constraints;
4. final answer and normalized citations/sources;
5. common frozen reference evidence for the question;
6. scoring instructions, codebook, score sheet, issue log, and completion
   checklist;
7. no experimental condition, provider, model, adapter, run ID, question ID,
   stratum code, target-chunk ID, LLM score, or other reviewer rating.

The common evidence sheet records source identity, version or SHA,
page/section/paragraph/chunk locator, permitted excerpt or authorized retrieval
pointer, and evidence-packet SHA. The system's own evidence summary cannot be
the only reference context. Conditions receive identical evidence context for
the same question.

Metadata blinding cannot prevent an answer's content from revealing a
condition. Do not edit substantive answers. Record observed inference risk as
a limitation.

## 6. Required tracked schemas

### 6.1 Primary expert score

`07_scoring/expert/expert_scoring_TEMPLATE.csv`:

```text
batch_id,blind_id,reviewer_id,score_version,answerability_correct,answer_correct,evidence_traceable,fatal_error,reason_codes_json,short_reason,answer_span_quote,evidence_span_locators_json,score_status,issue_id,reviewed_utc
```

`score_status` is one of `SCORED`, `UNRATEABLE`, `MISSING`, `INVALID`.
`reason_codes_json` is a JSON array drawn only from the fixed vocabulary.
Literal `not_applicable` is allowed only for the two specified fields on
`UNANSWERABLE` items.

### 6.2 Field adjudication

`07_scoring/expert/expert_adjudication_TEMPLATE.csv`:

```text
batch_id,blind_id,field_name,expert1_reviewer_id,expert1_value,expert2_reviewer_id,expert2_value,adjudicator_id,adjudicated_value,reason_code,short_reason,answer_span_quote,evidence_span_locators_json,reviewed_utc,adjudication_record_id
```

One row is required for every disputed field. Agreed fields retain their
shared value. The third expert cannot replace the first two original scores,
and no majority vote, average, or LLM decision is permitted.

### 6.3 Issue log, attestation, and consensus

`07_scoring/expert/score_issue_log_TEMPLATE.csv` retains missing, invalid,
unrateable, correction, and linkage issues. `reviewer_attestation_TEMPLATE.json`
records one independence attestation per reviewer and batch.
`human_consensus_TEMPLATE.csv` defines the derived consensus schema and
lineage to both original score files and all required adjudication records.

## 7. Disagreement handling

Compare the two primary experts on the four fields only. Any mismatch triggers
third-expert adjudication for each disputed field. Differences in prose,
reason codes, or evidence-locator choice do not trigger adjudication when the
four decision values are identical.

The adjudicator receives the same blinded packet, both anonymized field values,
and both short rationales. The adjudicator records a value and rationale for
every disputed field only.

If a primary score, required adjudication, locator, or answer span is missing
or invalid, keep the record and block the relevant strict gate. Corrections
are new linked records; original submissions are immutable.

## 8. Agreement and Pilot calibration

Report separately for Pilot and formal QA60:

- raw agreement for each dimension and for the two experts' provisional EGTS;
- Gwet's AC1 for each dimension as the primary chance-corrected coefficient;
- Cohen's kappa as a supplementary coefficient;
- 95% CIs from question-clustered bootstrap retained across B0/B1/B2;
- third-expert adjudication count and rate;
- missing, invalid, unrateable, and corrected records;
- LLM-LLM and LLM-to-human-consensus agreement when judge data are complete.

Pilot gates:

1. raw agreement at least 85% for each of the four fields;
2. Gwet AC1 at least 0.70 for `answerability_correct`, `answer_correct`, and
   `evidence_traceable`;
3. provisional EGTS agreement at least 85%;
4. no missed severe error in the fatal-error challenge set.

If a gate fails, analyze disagreements, revise codebook/training, retain the
old version and scores, and calibrate on a new independent sample. Never
delete hard questions or modify formal questions to improve agreement.

## 9. Retained outputs and release class

Retain:

- packet builder inputs, blind maps, reviewer assignments, and packet manifest;
- exact reference evidence with source SHA/locator and rights class;
- each original score submission, attestation, intake timestamp, SHA, and
  schema-validation report;
- issue and correction records;
- field-level disagreement and adjudication records;
- deterministic consensus and agreement outputs;
- score/consensus schemas, code version, protocol version, and analysis SHA.

Expert identities, credential data, and `L1` restricted material stay in
protected server storage. Public `L2` derivatives include pseudonymous IDs,
SHA values, locators, permitted excerpts, and redaction lineage. See
`PUBLICATION_SCOPE.md`.

## 10. Completion rule

Every assigned blind ID must appear exactly once in each primary expert's
submission. The score file must pass schema, label/NA, enum, row-count,
timestamp, locator, and attestation checks before consensus. Missing, invalid,
and unrateable rows are retained in the denominator record; they are never
silently dropped or converted to zero.
