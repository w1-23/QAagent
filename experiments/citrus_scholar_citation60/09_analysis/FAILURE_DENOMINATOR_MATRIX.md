# Failure status and intended-run denominator matrix

Version: `4.3.1`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Applies to: `P00_PILOT`, `E01_QA60_EGTS`, and
`E01_SUPP_CHUNK_CITATION60`

This matrix is prospective. It authorizes no run and contains no experiment
result.

## 1. Denominator principle

The denominator is the frozen intended-run set:

- Pilot: 12 questions x 3 conditions = 36 planned runs;
- QA60: 60 questions x 3 conditions = 180 planned runs;
- citation60: 60 questions x 3 conditions = 180 planned runs.

A run remains in its planned denominator even when it fails, is cancelled, is
skipped for an infrastructure reason, or has no raw record. The analysis does
not replace the intended denominator with the subset that happened to
succeed.

No missing raw record is used to synthesize an answer, expert packet, score, or
citation observation. The report contains both the intended total and the
number of authentic terminal records.

## 2. Terminal run statuses

| Status | Meaning | Expert packet | QA60 outcome | Citation observation | Strict gate |
|---|---|---|---|---|---|
| `completed` | Authentic terminal response with usable final response and expected completion signals | Yes | Expert-scored from answer fields | Observed from final citations/sources | Eligible when other requirements pass |
| `partial` | Authentic response ended with an incomplete but inspectable answer | Yes | Expert-scored from the actual partial answer | Observed from final citations/sources | Eligible, with status retained |
| `no_results` | Authentic terminal response but no usable final result | Yes, including the empty/no-result state | Expert-scored; dimensions follow the observed absence/refusal | `target_chunk_cited=0` unless an explicit final citation exists | Eligible only if the authentic terminal record and required score data exist |
| `failed` | Authentic terminal failure before an evaluable answer, such as service/tool/transport failure | No semantic score packet | `EGTS=0` as system task failure; semantic fields `not_scored_infrastructure` | Observation status `NO_FINAL_OUTPUT`, hit `0` | May pass only if failure evidence and status are valid; no semantic effect is claimed |
| `cancelled` | Planned run intentionally cancelled | No semantic score packet | `EGTS=0` as system task failure; semantic fields `not_scored_infrastructure` | Observation status `RUN_CANCELLED`, hit `0` | May pass only if cancellation evidence and status are valid |
| `skipped` | Predetermined run-level skip, not a silent omission | No semantic score packet | `EGTS=0` as system task failure; semantic fields `not_scored_infrastructure` | Observation status `RUN_SKIPPED`, hit `0` | May pass only if skip reason and evidence are valid |

For a terminal infrastructure failure, `egts_system_outcome=0`,
`human_consensus_egts` is blank, and semantic fields are
`not_scored_infrastructure`. This is a task-execution outcome, not a claim
that `answer_correct=false` or `fatal_error=true`.

## 3. Additional reason classes

| Reason class | Treatment |
|---|---|
| `MODEL_EMPTY_ANSWER` | `no_results` unless accompanied by an explicit refusal; expert sees the empty/refusal state and scores the semantic response. |
| `MODEL_CORRECT_REFUSAL` | An actual answer, not an infra failure. It receives normal expert scoring under the locked `UNANSWERABLE` rule. |
| `TOOL_FAILURE` | A failure only when the terminal run could not produce an evaluable answer. If a partial answer exists, `partial` is scored and the tool failure remains a diagnostic. |
| `SERVICE_UNAVAILABLE` | `failed`; system task failure and separate failure-rate component. |
| `TRANSPORT_FAILURE` | `failed` after the frozen retry limit; all attempts remain linked. |
| `BUDGET_VIOLATION` | `failed` when no evaluable answer exists, otherwise `partial`; the frozen cap breach and all actual resource counts remain attached and visible. |
| `RAW_RUN_MISSING` | `planned_but_missing`; no synthetic observation. The intended denominator retains the slot, and the strict gate fails. |

## 4. Strict-gate consequences

### `strict-human`

Fails when any planned run lacks either:

1. an authentic terminal record; or
2. a valid semantic score representation for a status requiring one; or
3. a valid infrastructure-failure representation for a terminal non-answer
   status.

Infrastructure failures do not require invented expert ratings of facts that
were never produced.

### `strict-citation`

Fails when any planned run has no authentic terminal record or no
deterministic observation. An authentic `failed`, `cancelled`, or `skipped`
record gets a statused `target_chunk_cited=0` observation. A missing raw run
does not.

### `strict-judge`

Fails when any `completed`, `partial`, or `no_results` QA60 run lacks a valid
authentic run or J1/J2 record, or when any required J3 adjudication is
missing. An authentic `failed`, `cancelled`, or `skipped` run uses a
deterministic infrastructure record and does not receive invented judge
input. Judge input cannot be fabricated from a missing raw run.

### `strict-all`

Fails if any declared experiment or release module fails its own strict rule.
Independent modules are reported separately.

## 5. Reporting

Every condition reports:

- intended runs;
- authentic terminal runs;
- `completed`, `partial`, `no_results`, `failed`, `cancelled`, `skipped`;
- `RAW_RUN_MISSING`;
- infrastructure failure rate;
- budget-cap violations;
- semantic scoring coverage;
- EGTS denominator and numerator;
- the independent failure/degradation table.

For QA60, semantic subdimensions use their locked 52/60 or 52/52 applicability
rules. Infrastructure rows never become false factual judgments and are never
reported as fatal errors.

For citation60, the target-hit denominator remains the 60-question intended
set per condition. The report distinguishes observed locator hits, authentic
non-hits, and missing observations.
