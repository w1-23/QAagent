# Statistical analysis contract

Version: `4.3`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`

No formal result may be produced from this protocol until its prerequisites
are satisfied. This document defines the frozen analysis rules; it is not
evidence that a model, gate, Pilot, or experiment has passed.

## 1. Inputs and lineage

### 1.1 Core QA60 human primary

`07_scoring/expert/consensus/<batch>.csv` must contain 180 rows for
`E01_QA60_EGTS` x `B0/B1/B2`. Every row must link to:

- one authentic raw run;
- two immutable expert score records;
- every required field-level third-expert adjudication;
- input score-file SHA-256 values, packet-manifest SHA-256, protocol version,
  and the deterministic aggregation version.

Human consensus is the primary QA60 measurement. A complete human dataset can
be analyzed when `strict-human` passes even if the machine-judge dataset is
unavailable.

### 1.2 Core QA60 machine comparator

`07_scoring/judge/qa_judge_aggregated.csv` must contain 180 rows, one per run.
Every row must link to J1 and J2 raw responses and to J3 only when J1 and J2
differ on any of the four fields. A failed J3 leaves that run `BLOCKED`; it is
not replaced by a vote, average, or one judge's answer.

Machine outputs never contain a model-written final decision. The aggregate
script derives the four consensus fields and EGTS from the fixed formulas.

### 1.3 Supplementary citation60

`07_scoring/reviews/citation_hit_observations.csv` must contain 180
deterministic observations, one per authentic run. `target_chunk_cited` is
derived only from an explicit frozen `target_chunk_id` in the final
`citations` or `sources`.

Do not use retrieval-tool events as final citations. Do not generate a
semantic-support rating, a `1/0.5/0` support distribution, or a
`citation_semantic_support.csv` file.

## 2. Fixed core QA60 outcomes and denominators

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

Each condition reports:

| Measure | Denominator |
|---|---:|
| Human-consensus EGTS | 60 |
| Answerability correctness | 60 |
| Answer correctness | 52 |
| Evidence traceability | 52 |
| Fatal-error rate | 60 |

Report the 52 answerable and 8 unanswerable strata separately. Do not recode
`not_applicable` as zero. Failure, cancellation, timeout, empty answer, and
invalid content remain in the assigned denominator and are reported by
predefined status.

## 3. Unique primary confirmatory comparison

The only confirmatory hypothesis test is:

```text
Endpoint: human-consensus EGTS
Comparison: B2 vs B1
Test: two-sided exact McNemar
Effect: paired success-rate difference B2 - B1, in percentage points
Interval: 95% stratified paired bootstrap
Replicates: 10,000
Seed: 20261104
Strata: L/V/W/X/P/N
Resampling unit: question
```

Each bootstrap draw resamples questions within stratum and retains all three
condition records for every selected question. The analysis reports the
complete paired 2x2 transition table with cell counts.

`B1 vs B0` and `B2 vs B0` are descriptive diagnostics, not confirmatory tests.
B0 has no local retrieval, web, attachment, or tool access and must not be
described as a fair RAG baseline. The P stratum's missing PDF input in B0 is
reported explicitly and must not be interpreted as a causal effect of the
platform's architecture.

Do not treat 180 runs as 180 independent questions. The fixed 60-question
benchmark does not support a population claim over all citrus research
questions.

## 4. Secondary measures

For each condition, report the five core dimensions listed in section 2 and
the separate 52/8 strata. Use Wilson 95% confidence intervals.

Other dimensions, strata, or comparisons are secondary or exploratory. If an
additional inferential test is run, label it exploratory and apply Holm
correction to the declared family. Do not select favorable tests after seeing
the results.

Required agreement outputs, computed separately for Pilot and formal QA60:

- raw agreement for each of the four fields;
- raw agreement of the two experts' provisional EGTS;
- Gwet's AC1 for each field as the primary chance-corrected coefficient;
- Cohen's kappa for each field as a supplementary coefficient;
- 95% intervals using question-cluster bootstrap retaining B0/B1/B2;
- third-expert field-adjudication count and rate;
- LLM-LLM agreement when J1/J2 are complete;
- LLM-to-human-consensus agreement when both independent measurements are
  complete;
- J3 adjudication count and rate when applicable.

## 5. Pilot calibration

The independent Pilot is 12 non-formal questions, two from each stratum
L/V/W/X/P/N, run once under B0, B1, and B2 (36 answers).

- Both experts score all 36 answers independently.
- J1 and J2 score all 36; J3 adjudicates any four-field disagreement.
- Pilot records never enter formal QA60 denominators.
- Thresholds: field raw agreement >= 85%; Gwet's AC1 >= 0.70 for
  `answerability_correct`, `answer_correct`, and `evidence_traceable`;
  provisional EGTS raw agreement >= 85%; no missed severe error in the
  fatal-error challenge set.
- A failed threshold requires disagreement analysis, codebook or training
  revision, preservation of the old version, and validation on a new
  independent calibration sample.

Do not delete difficult questions, modify formal questions, or silently change
the rubric to force agreement.

## 6. Independent strict gates

The aggregator must expose independent flags:

```text
strict-human
strict-judge
strict-citation
strict-all
```

### 6.1 `strict-human`

Requires:

- 180 authentic core QA60 runs with valid terminal states;
- two complete immutable expert score submissions per run;
- all required third-expert field adjudications;
- valid fixed reason codes, NA placement, spans, locators, and attestations;
- deterministic 180-row human-consensus output.

### 6.2 `strict-judge`

Requires:

- complete J1/J2 records for all 180 runs;
- valid schema and four-field values;
- all required J3 records;
- immutable attempt, request, response, model identity, SHA, usage, timing, and
  error records.

### 6.3 `strict-citation`

Requires 180 complete deterministic citation observations with authentic run
lineage.

### 6.4 `strict-all`

Requires all modules needed by the declared paper analysis to pass their own
strict gates, including routing, composite tasks, workflows, and release
checks. A judge outage prevents `strict-all` but does not invalidate an
otherwise complete `strict-human` analysis.

## 7. Missing, failed, and retry records

- Missing, invalid, empty, timeout, cancellation, partial, failed, and
  unadjudicated records remain in the ledger.
- They are never filled with zero, deleted, or silently converted to success.
- A strict gate exits non-zero when its required records are incomplete.
- A planned run may be retried at most twice only for a transport-level
  failure that produced no answer content.
- Any attempt with substantive answer content is never retried because of poor
  quality.
- Every attempt and its linkage to the planned run are retained.

## 8. Citation60 analysis

Citation60 is analyzed separately from core QA60:

- condition-level target-chunk hit rate with Wilson 95% intervals;
- five-corpus micro and macro summaries;
- paired target-chunk hit difference, `B2 - B1`, by question;
- corpus-stratified paired bootstrap with seed `20261104`;
- exact McNemar as a descriptive paired test and the complete 2x2 table;
- target-document, allowed-corpus, wrong-corpus, and no-citation components;
- all failures and missing observations in their assigned denominator.

No semantic-support analysis is permitted. Target-locator hit is not answer
correctness or citation correctness in a semantic sense.

## 9. Required outputs and lineage

`09_analysis/tables/` must contain, as applicable:

- human-consensus condition summaries;
- human agreement and adjudication tables;
- independent machine-judge condition summaries;
- LLM agreement and human-consensus agreement tables;
- 180-row human-consensus and machine-aggregate traceback tables;
- citation condition and paired-effect tables;
- routing, composite, and workflow tables;
- missing/failure tables;
- `analysis_manifest.json`.

The manifest records input SHA-256 values, script SHA-256 values, protocol
version, analysis version, seed, package versions, command, UTC time, strict
flags, and output SHA-256 values.

Every manuscript number must be reproducible from raw inputs plus the recorded
analysis code. Hand-entered result numbers are prohibited.

## 10. Prohibited analyses

- pooling core QA60 and citation60;
- treating runs as independent questions;
- replacing human consensus with machine consensus;
- majority vote or averaging when adjudication is required;
- recoding missing or NA as zero;
- deleting failed or partial runs from the intended denominator;
- reporting semantic support for citation60;
- calling route observations classifier accuracy;
- claiming B2 superiority before the confirmatory result is complete;
- extrapolating from 60 fixed questions to all citrus science.
