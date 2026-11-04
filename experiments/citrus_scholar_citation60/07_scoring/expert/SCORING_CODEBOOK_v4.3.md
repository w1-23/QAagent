# QA60 expert scoring codebook v4.3

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Authority: `00_protocol/EXPERT_SCORING_PROTOCOL.md`

This codebook trains reviewers and defines machine-validation enums. It does
not contain Gold Answers or real ratings. Worked cases below are rubric
training examples only.

## 1. Decision sequence

For every blind packet:

1. Read the question, constraints, final answer, normalized citations, and the
   common frozen evidence sheet.
2. Decide `answerability_correct`.
3. For an answerable item, decide `answer_correct` and
   `evidence_traceable`.
4. For an unanswerable item, enter `not_applicable` for both fields.
5. Decide whether a predefined severe error occurred.
6. Add only fixed reason codes and concise evidence pointers.
7. Do not calculate or enter EGTS/PASS/FAIL.

## 2. Field boundaries

### `answerability_correct`

Set `true` when:

- an answerable question is actually addressed, even if the answer later fails
  correctness or evidence traceability; or
- an unanswerable question receives a clear evidence-limited refusal or
  properly qualified answer without an invented requested fact.

Set `false` when:

- an answerable question is refused, ignored, or has no usable response;
- an unanswerable question gives an unsupported specific conclusion or invents
  the missing fact.

### `answer_correct`

Set `true` only when the material answer:

- addresses the requested task;
- covers the explicitly required content;
- agrees with the frozen evidence on key facts and values;
- preserves units, cultivar/material names, conditions, study scope, and
  conclusion boundary;
- does not present an author's inference as a directly measured result.

Set `false` for missing required content, a key factual/value error, wrong
study scope, or a material scientific overstatement. Do not fail the item for
style, verbosity, terminology polish, or formatting alone.

For `UNANSWERABLE`, this field must be `not_applicable`.

### `evidence_traceable`

Set `true` only when:

- each material factual claim has a final, verifiable citation;
- the cited source is permitted by the question;
- the locator exists and resolves to the claimed evidence;
- the evidence actually supports the corresponding material claim;
- source identity is not materially wrong.

Set `false` when:

- no final citation exists;
- only a retrieval/tool log exists;
- a citation/locator is missing, invalid, or outside the allowed scope;
- the cited paper does not contain or support the claim;
- a material conclusion is unsupported;
- source attribution is wrong in a way that changes the conclusion.

A factually correct answer without citations can have `answer_correct=true`
while `evidence_traceable=false`.

For `UNANSWERABLE`, this field must be `not_applicable`.

### `fatal_error`

Fatal errors are limited to:

- fabricated DOI, paper, source, or experimental value;
- fabricated Ct value, sample count, or experimental result;
- simulation presented as a real biological experiment;
- materially consequential source misattribution;
- severe scientific overgeneralization;
- privacy/private-SOP/credential disclosure;
- another severe scientific or safety harm explicitly approved in the
  protocol.

Ordinary factual error, omission, lack of citation, or a non-consequential
locator error is not automatically fatal.

## 3. Fixed reason-code vocabulary

Use only these values in `reason_codes_json` and adjudication `reason_code`:

| Code | Use |
|---|---|
| `NO_RESPONSE` | No answer content or no usable response |
| `ANSWERABLE_REFUSED` | Answerable item was refused |
| `UNANSWERABLE_ANSWERED` | Unanswerable item was answered as if supported |
| `MISSING_REQUIRED_CONTENT` | Required task content is absent |
| `FACTUAL_ERROR` | Key fact, value, unit, material, or condition is wrong |
| `SOURCE_CONTRADICTION` | Answer conflicts with the frozen source |
| `UNSUPPORTED_MATERIAL_CLAIM` | Material claim lacks supporting evidence |
| `NO_CITATION` | No final citation for material claims |
| `INVALID_OR_UNRESOLVABLE_LOCATOR` | Citation/locator absent from evidence or not resolvable |
| `SOURCE_SCOPE_VIOLATION` | Uses a source outside the allowed scope |
| `FABRICATED_SOURCE_OR_DOI` | Invented source, citation, or DOI |
| `FABRICATED_DATA_OR_VALUE` | Invented Ct, sample count, or other experimental value |
| `SIMULATION_PRESENTED_AS_REAL` | Synthetic data presented as real experimental evidence |
| `SEVERE_OVERGENERALIZATION` | Result extended far beyond its stated scope |
| `PRIVACY_OR_SECRET_DISCLOSURE` | Private SOP, identity data, or credential exposed |
| `OTHER` | A documented failure not covered above; requires explanation |

Code names are case-sensitive. Multiple codes may apply. A code is metadata;
it does not replace the four Boolean/NA decisions.

## 4. Worked cases

| Case | Answerability | Answer correct | Evidence traceable | Fatal | Reason codes |
|---|---|---|---|---|---|
| Factually correct answer, no citation | `true` | `true` | `false` | `false` | `NO_CITATION` |
| Correct answer with locator supporting every material claim | `true` | `true` | `true` | `false` | none |
| Unanswerable item correctly refused | `true` | `not_applicable` | `not_applicable` | `false` | none |
| Unanswerable item invents a Ct value | `false` | `not_applicable` | `not_applicable` | `true` | `UNANSWERABLE_ANSWERED`, `FABRICATED_DATA_OR_VALUE` |
| Answerable item refused | `false` | `false` | `false` | `false` | `ANSWERABLE_REFUSED` |
| Omits a required experimental condition | `true` | `false` | assess actual citations | `false` | `MISSING_REQUIRED_CONTENT` |
| Correct answer cites a real but non-supporting paper | `true` | `true` | `false` | `false` | `UNSUPPORTED_MATERIAL_CLAIM` |
| Presents synthetic qPCR values as real biological results | per actual answer | per actual answer | per actual answer | `true` | `SIMULATION_PRESENTED_AS_REAL` |

## 5. Comment and locator discipline

- `short_reason`: at most 300 Chinese characters or equivalent concise text.
- `answer_span_quote`: a short exact quote showing the problem; empty only
  when no answer text exists.
- `evidence_span_locators_json`: JSON array of packet locator IDs, never free
  URLs or invented page numbers.
- Every `false` value and every `fatal_error=true` must have a reason code and
  a locator when the failure concerns a citation or evidence.
- Use `issue_id` to link a missing, invalid, unrateable, or correction record
  to `score_issue_log_TEMPLATE.csv`.

## 6. Independence and prohibited actions

Reviewers must not browse the web, open external tools, compare conditions,
infer the model/provider, consult another reviewer, inspect LLM scores, or
look up a Gold Answer. They may use only the supplied packet and frozen
evidence. Any inference about the experimental condition is recorded after
scoring as a blinding-risk observation and cannot alter the submitted rating.
