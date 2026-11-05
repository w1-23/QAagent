# Core QA60 scoring contract

Version: `4.3.2`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Authoritative scoring protocol:
`00_protocol/EXPERT_SCORING_PROTOCOL.md`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`
Current decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_2.md`
Historical decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_1.md`

This file defines the scoring formula and reporting denominators. It does not
contain a Gold Answer, a real rating, or an experiment result.

## 1. Primary and automated measurements

Human consensus is the semantic answer-quality measurement for
`E01_QA60_EGTS`. The confirmatory system-task endpoint is
`egts_system_outcome`, while `human_consensus_egts` is reported separately for
authentically scorable answers.

- Two domain experts independently score all 36 Pilot answers and all 180
  formal QA60 answers.
- A third independent domain expert adjudicates every disputed field.
- GLM-5.3-Flash (J1) and MiMo-V2.6-Flash (J2) separately score all 180
  answers as an automated measurement.
- MiniMax-M3.1-Flash (J3) adjudicates only fields on which J1 and J2 differ.
- Human and machine measurements are stored, aggregated, and reported
  separately. Neither overwrites the other.

No expert or model writes a final PASS/FAIL. Deterministic scripts compute
every provisional, adjudicated, consensus, and machine outcome from the four
decision fields and the locked question label.

## 2. Four scoring fields

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

All fields are binary except `answer_correct` and `evidence_traceable`, which
must be `not_applicable` for an `UNANSWERABLE` item.

| Field | `true` / pass boundary | `false` / fail boundary |
|---|---|---|
| `answerability_correct` | An answerable question is actually addressed; an unanswerable question receives a clear evidence-limited refusal or properly qualified answer without inventing the requested fact. Additional substantive background facts remain explicitly limited and do not escape evaluation. | An answerable question is refused or receives no usable response; an unanswerable question gives an unsupported specific answer or wraps a correct refusal around materially unsupported background claims. |
| `answer_correct` | The response substantially answers the request, covers required content, and preserves key facts, values, units, material names, conditions, study scope, and conclusion boundary. | Missing required content, material factual error, wrong scope, inference presented as a direct result, or a conclusion broader than the source supports. Style and formatting alone do not fail this field. |
| `evidence_traceable` | Every material factual claim can be traced through a final, verifiable citation to an allowed source and an existing locator whose passage supports the corresponding claim. | No citation, only a retrieval/tool log, dead or invalid locator, disallowed source, wrong source, or a material claim unsupported by the cited passage. |
| `fatal_error` | No predefined severe error occurred. | A severe error listed in section 4 occurred. Ordinary factual error, omission, or absent citation is not automatically fatal. |

`evidence_traceable` measures observable evidence traceability. It does not
claim access to the model's internal use of a source.

## 3. Deterministic EGTS

`EGTS` means `Evidence-Grounded Task Success`.

For `ANSWERABLE`:

```text
EGTS = answerability_correct
       AND answer_correct
       AND evidence_traceable
       AND NOT fatal_error
```

For `UNANSWERABLE`:

```text
answer_correct = not_applicable
evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

Consequences required by the protocol:

- a factually correct answer without a citation can have
  `answer_correct=true`, `evidence_traceable=false`, and EGTS failure;
- an incorrect answer with a real but non-supporting citation fails EGTS;
- a correct refusal to an unanswerable question passes EGTS;
- fabricating the missing fact fails EGTS and may be a fatal error.

## 4. Fatal-error boundary

Set `fatal_error=true` only for:

- a fabricated DOI, paper, source, or experimental value;
- a fabricated Ct value, sample count, or other experimental result;
- simulated data presented as a real biological experiment;
- source misattribution severe enough to change the scientific conclusion;
- severe scientific overgeneralization;
- disclosure of a private SOP, personal identity data, or credentials.

Ordinary factual error, omitted required content, no citation, or a
non-consequential locator error must be scored in the corresponding field but
must not automatically be called fatal.

Formal scoring uses only the fixed fatal categories. A new category requires a
prospective protocol amendment; reviewers cannot create `OTHER` or an ad hoc
severe-harm category in formal scoring.

Every substantive `false` decision (`answerability_correct`, `answer_correct`,
or `evidence_traceable`) and every `fatal_error=true` requires at least one
field-scoped fixed reason code, a non-empty short rationale, an answer-span
quote, and evidence locators where the failure concerns evidence.
`fatal_error=false` is the ordinary no-fatal result and does not itself require
a reason or quote. Quote fields must not reproduce real credentials, personal
identifiers, or complete private-SOP text. The fixed vocabulary and training
cases are in
`07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`.

## 5. Fixed reporting denominators

Each condition (`B0`, `B1`, `B2`) reports:

| Measure | Denominator |
|---|---:|
| `egts_system_outcome` (confirmatory) | 60 planned slots |
| `human_consensus_egts` (semantic) | Authentic scorable rows, with coverage reported |
| Answerability correctness | 60 |
| Answer correctness | 52 |
| Evidence traceability | 52 |
| Fatal-error rate | 60 |

The 52 `ANSWERABLE` and 8 `UNANSWERABLE` strata are also reported separately.
`not_applicable` is never recoded as zero. Failure, cancellation, timeout,
empty response, and missing-content attempts remain in the planned
denominator and are classified explicitly. Infrastructure failures are system
task failures, not fabricated factual-error judgments. Missing raw records
remain `planned_but_missing` and fail the relevant strict gate; they are never
used to invent an observation. See
`09_analysis/FAILURE_DENOMINATOR_MATRIX.md`.

## 6. Primary statistics

The unique confirmatory comparison is `B2 vs B1` on
`egts_system_outcome`:

- two-sided exact McNemar test;
- paired success-rate difference in percentage points;
- 95% confidence interval from 10,000 stratified paired bootstrap replicates;
- seed `20261104`;
- strata `L/V/W/X/P/N`;
- resampling unit is the question, retaining that question's B0/B1/B2 triplet;
- report the complete paired 2x2 transition table.

The exact implementation is frozen in
`12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`: two-sided exact
binomial McNemar, percentile intervals, NumPy `PCG64(20261104)`, alpha `0.05`,
Wilson `z=1.959963984540054`, and question-within-stratum resampling retaining
the B0/B1/B2 triplet.

`B1 vs B0` and `B2 vs B0` are descriptive diagnostics only. B0 has no local
retrieval, web, attachment, or tool access and is not a fair RAG comparator.
The P-stratum B0 limitation is reported explicitly.

Other condition-level dimensions receive proportions and Wilson 95% intervals.
Any additional inferential test is secondary or exploratory and uses Holm
correction. Do not treat 180 runs as 180 independent questions, and do not
generalize the fixed 60-question benchmark to all citrus research questions.

## 7. Agreement

For Pilot and formal QA60, report separately:

- raw agreement for each field and for the two experts' provisional EGTS;
- Gwet's AC1 as the primary chance-corrected coefficient;
- Cohen's kappa as a supplementary coefficient;
- 95% intervals from question-clustered bootstrap retaining the B0/B1/B2
  triplet;
- third-expert adjudication count and rate;
- LLM-LLM, LLM-human-consensus, and third-judge adjudication statistics when
  the machine records are complete.

`answer_correct` and `evidence_traceable` agreement use only the 52
`ANSWERABLE` questions. NA pairs are excluded, not recoded as a third binary
category. The Gwet AC1 and Cohen kappa formulas, listwise handling, and
bootstrap behavior are fixed in the statistical implementation specification.

Pilot thresholds are:

1. raw agreement at least 85% for each of the four fields;
2. Gwet's AC1 at least 0.70 for `answerability_correct`, `answer_correct`, and
   `evidence_traceable`;
3. provisional EGTS raw agreement at least 85%;
4. no missed severe error in the fatal-error challenge set.

Failure requires codebook/training analysis and calibration on a new
independent sample. Hard questions are not deleted and formal questions are
not edited to manufacture agreement.

## 8. Machine judge schema and adjudication

An ordinary J1 or J2 output contains only:

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
`not_applicable`. No `decision`, `score`, or machine-written PASS/FAIL field is
accepted.

J3 receives the same anonymous packet and J1/J2 structured values and returns
only the disputed fields in `disputed_values`, field-scoped reason codes, a
short reason, an answer-span quote, and locators. It must not replace fields
on which J1 and J2 agree.

## 9. Strict-analysis separation

- `strict-human`: authentic runs, two complete expert score files, and every
  required third-expert adjudication, or a valid deterministic
  infrastructure-failure record where no semantic answer exists.
- `strict-judge`: complete J1/J2 records for every `completed`, `partial`, or
  `no_results` run and every required J3 adjudication. Authentic
  `failed`, `cancelled`, and `skipped` runs use deterministic infrastructure
  outcomes and do not receive invented semantic judgments.
- `strict-citation`: 180 complete deterministic target-chunk observations.
- `strict-all`: all required modules complete.

A machine-judge outage never suppresses a complete human-primary analysis.
The missing independent module remains visible and prevents `strict-all`.
Missing, invalid, unadjudicated, or failed records must not be silently
dropped, filled with zero, or replaced by a different measurement.

## 10. Supplementary citation60

The only formal endpoint is `target_chunk_cited=1`: the final answer's
`citations` or `sources` explicitly contains the frozen `target_chunk_id`.
A retrieval-tool hit without a final citation is zero.

Citation60 has its own 180-run denominator and deterministic analysis. It has
no human or LLM semantic-support rating; semantic support is not reported.
Never call a target-locator hit "answer correct", "citation correct", or
"semantic support".

## 11. Routing and workflow observations

Tool traces, file existence, SHA, status, and allowed/forbidden tool sets are
checked deterministically first. A frozen judge may evaluate only a residual
qualitative criterion defined by the locked workflow protocol. A judge cannot
override a failed file, SHA, status, or forbidden-tool check.

Route192 reports observed request-to-action compliance, not classifier
accuracy. Composite24 reports tool-set precision, recall, F1, and constraint
violations. Workflow results report real delivered artifacts, hashes, and
failure boundaries.
