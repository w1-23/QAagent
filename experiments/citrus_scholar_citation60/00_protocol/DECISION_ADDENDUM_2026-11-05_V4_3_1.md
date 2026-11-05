# Decision addendum v4.3.1: third external review correction

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Decision version: `v4.3.1`
Date: `2026-11-05`
Repository: `w1-23/QAagent`
External review baseline: `9719f40daa8d2e4122d451bedafdeca271b5e9d5`
Reviewed target: `d156dab5dc299198696f2194f778086f5d79e0cc`
v4.3.1 implementation commit: resolve with `git rev-parse HEAD`
Execution authorization: `NONE`

This document records corrections requested by the third external GPT review.
It does not approve a Pilot, authorize a model call, prove gate completion, or
change any `gates.csv` status.

The three SHA labels are deliberately distinct:

- external review baseline: `9719f40...`;
- reviewed target: `d156dab...`;
- v4.3.1 implementation commit: the commit containing this record. Its
  immutable SHA is reported in the execution handoff rather than embedded in
  the commit it identifies.

## 1. Review outcome and current boundary

The v4.3 research design is retained. The third review identified one blocking
data-release defect and several method, schema, reproducibility, and fairness
gaps. This revision fixes those issues without redesigning the study.

Current state:

```text
Formal experiment: NOT_STARTED
Pilot: NOT_STARTED
Model calls: NOT_AUTHORIZED
Server execution: NOT_AUTHORIZED
G01: BLOCKED
G02-G15: PENDING
PASS gates: 0
```

No result, scoring judgment, model response, sample-size refill, or simulated
performance result is created by this revision.

## 2. P0: L1 source-text release correction

The reviewed target publicly contained:

| Former public path | Content | Bytes | SHA-256 |
|---|---|---:|---|
| `03_benchmarks/citation60/chunks_FROZEN.jsonl` | 60 complete frozen chunks, including `chunk_text` | 184718 | `87a207010e63bd607944e38f71df08d0c09d7c9ca86b62f2e62949c89eaef2f3` |
| `03_benchmarks/citation60/question_authoring_packet.jsonl` | 60 authoring records, including complete `chunk_text` | 120587 | `f032c13982a18c7657045fdb661ba46485a65696b7461003359ae32ae1a578b9` |

Decision:

1. Both files are `L1` and must be stored outside the public Git checkout in
   protected experiment storage.
2. The public branch retains only a controlled-asset manifest containing the
   path, byte count, SHA-256, source/locator metadata, access class, and release
   status. It does not retain the source text.
3. `question_authoring_packet.jsonl` is now separately registered as an `L1`
   asset rather than being treated as an unregistered public working file.
4. Public question and chunk metadata are labelled `L2` only when a
   file-level release review confirms that they contain no full text,
   substantial unlicensed excerpt, credential, identity linkage, or private
   SOP.
5. Raw runs, SSE events, tool traces, screenshots, artifacts, download records,
   failure records, and retries default to controlled `L1` until a
   file-specific redaction/license/SHA review creates an approved `L2`
   derivative.
6. The historical protection screen is shown in
   `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`.

The original files were first retained outside Git with matching SHA-256
values. The local pre-review retention copy is
`E:\codex_WORKSPACES\_backups\CitrusScholar-v4.3.1-L1-retained-20261105`.
The server retention target is
`/home/WZQ/citrus_experiment/protected/L1/03_benchmarks/citation60/`.
The server copy is `PLANNED`, not evidence that it already exists.

Deleting a path from the latest branch does not remove its Git objects from
the public repository history. This revision therefore does not rewrite Git
history. PI/repository-owner approval is required before deciding whether to
restrict repository access temporarily or request GitHub data removal/history
rewriting. No force push is authorized.

## 3. Data-classification rule

Retention and release are separate decisions.

```text
L0  credentials and authentication data; prohibited everywhere
L1  protected source text, restricted excerpts, private SOP, identity maps,
    unredacted or unreviewed raw runs and sensitive logs
L2  reviewed public-safe derived questions, locators, hashes, blinded scores,
    redacted run records, analyses, figures, and manuscript material
```

`ASSET_REGISTER_CURRENT.csv` now records both `data_class` and
`release_status`; `retention_class` remains only a legacy operational label.
No filename extension, JSON/CSV structure, or screenshot label qualifies a
file for `L2` automatically.

## 4. Failure status and intended-run denominators

All planned runs remain in the intended-run ledger. The authoritative status
mapping is `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`.

Terminal statuses are:

```text
completed
partial
no_results
failed
cancelled
skipped
```

Separate reason classes are:

```text
MODEL_EMPTY_ANSWER
MODEL_CORRECT_REFUSAL
TOOL_FAILURE
SERVICE_UNAVAILABLE
TRANSPORT_FAILURE
BUDGET_VIOLATION
RAW_RUN_MISSING
```

Rules:

- Authentic `completed`, `partial`, and `no_results` records receive a
  condition-blinded expert packet when answer text or an empty final answer
  is part of the observed response.
- `failed`, `cancelled`, and `skipped` runs remain in the planned denominator
  as system-level task failures but are not converted into false factual
  judgments. Their semantic fields are `not_scored_infrastructure`.
- Citation60 reports `target_chunk_cited=0` for an authentic terminal run with
  no final citation, with an explicit observation status.
- A missing raw-run record cannot be assigned a fabricated answer, score, or
  citation observation. It remains `planned_but_missing`; the relevant strict
  gate fails and the complete intended denominator remains visible.
- Infrastructure failure rates are reported separately from factual-error
  and fatal-error rates.

## 5. Human scoring schema and correction chain

Expert reason codes are field-scoped:

```json
{
  "answerability_correct": [],
  "answer_correct": ["MISSING_REQUIRED_CONTENT"],
  "evidence_traceable": ["NO_CITATION"],
  "fatal_error": []
}
```

The field name is `reason_codes_by_field_json`. Original expert submissions
are immutable. Corrections are append-only linked records containing:

- original score record ID;
- changed field;
- original value;
- revised value;
- correction reason;
- revising reviewer;
- revision UTC;
- approval/review record.

The third expert continues to adjudicate only disputed four-field values.
Two primary score records plus required field adjudications deterministically
rebuild the human consensus.

## 6. Fatal-error and UNANSWERABLE closure

Formal scoring permits only the fixed categories in
`07_scoring/expert/SCORING_CODEBOOK_v4.3.1.md`. `OTHER` and a free-form severe
harm expansion are not permitted in formal ratings. A new category requires a
protocol revision before use.

Ordinary factual error, omission, missing citation, and non-consequential
locator error are not automatically fatal.

For `UNANSWERABLE`, correct refusal of the missing target fact is the success
core. Any additional substantive background fact:

- must be explicitly distinguished from the missing target fact; and
- is still checked for factual support and consequence; and
- cannot use an unsupported background claim to launder an otherwise correct
  refusal into EGTS success.

When privacy or private-SOP leakage is alleged, reviewers use a redacted
placeholder and controlled locator. They never copy a real key, identity
datum, or complete private SOP into a quote field.

## 7. Machine-judge output contract

J1/J2 return exactly:

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
`not_applicable`.

J3 returns only disputed fields:

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

No judge may output `decision`, `score`, `PASS`, or `FAIL`. The aggregator
computes the result deterministically.

## 8. Statistical implementation freeze

The primary comparison remains `B2 vs B1` on human-consensus EGTS.

Implementation defaults:

- alpha `0.05`, two-sided;
- exact McNemar using the two-sided binomial test with null probability `0.5`
  on discordant pairs;
- `10,000` question-resampled, six-stratum paired bootstrap replicates;
- NumPy `PCG64` with seed `20261104`;
- percentile 95% intervals, with quantiles `[0.025, 0.975]`;
- Wilson intervals using `z=1.959963984540054`;
- Gwet AC1 and Cohen kappa computed only on complete applicable pairs;
- `answer_correct` and `evidence_traceable` agreement use only the 52
  answerable questions; `not_applicable` is excluded, not treated as a third
  binary category.

The implementation and dependency lock must reproduce these rules. A missing
or BLOCKED confirmatory input prevents a confirmatory result rather than
changing the denominator.

## 9. Citation60 scope and corpus disclosure

Citation60 is a multi-source locator stress test. It is not a representative
sample of all citrus literature and not a semantic-support or answer-correctness
test.

The five source categories are:

| Corpus ID | Working description | Current disclosure boundary |
|---|---|---|
| `cqa_deng_book` | Chinese citrus variety reference material | rights and redistribution status pending G12 |
| `cqa_literature` | mixed peer-reviewed literature, including material outside citrus | corpus-specific license review required |
| `cqa_methods` | experimental-method material | method content and redistribution status pending G12 |
| `cqa_translated` | translated documents, including material outside citrus | provenance and translation rights pending G12 |
| `cqa_ucr_varieties` | UCR variety records | record access and redistribution status pending G12 |

Every corpus is reported separately with sample count and locator-hit count.
The frozen sample is not described as “five public citrus corpora.”

## 10. B1/B2 resource accounting

B1 and B2 share hard per-run caps for model calls, cumulative input/output
tokens, retrieval calls, context tokens, tool calls, and elapsed time. Their
internal call traces need not be identical, but every actual call, token,
cost, retrieval, context allocation, tool call, and elapsed second is retained
and reported as part of the architecture resource profile.

Exceeding a hard cap blocks the run as `BUDGET_VIOLATION`; it does not become a
successful answer. Cost is recorded from provider returns when available and
labeled `UNKNOWN` when not returned.

## 11. Expert blinding

For formal QA60, each reviewer receives six batches of 30 packets. The three
condition packets for a question are assigned to three distinct batches, so no
batch contains the same question twice. Batches are independently shuffled
with a frozen reviewer-specific seed. Pilot uses three batches of 12 under the
same rule.

Reviewers receive no other-condition answer and may not compare conditions
across batches. Potential model/condition identification is recorded only
after scoring as a blinding-risk event and cannot alter the submitted score.
Substantive answers are never edited to disguise the condition.

## 12. P2 audit corrections

`05_baselines/adapters/B0.py` is retained only as a non-executable legacy
template. Its v4.3 change is limited to renaming the legacy environment
template from `OPENAI_COMPAT_*` to `CITRUS_AGENT_*`; it is not a runtime
implementation and is forbidden from execution in this review unit.

The audit index now displays the external review baseline, reviewed target,
and implementation-commit lookup separately.

## 13. Git-history and release decision still pending

The following decisions require PI/repository-owner action:

1. whether to keep the public repository online while prior Git objects still
   contain the two L1 files;
2. whether to request GitHub support/security removal;
3. whether a reviewed history rewrite is ever warranted.

Until that decision is logged, the latest branch removes the files and
preserves their hashes, but no claim is made that GitHub's existing object
storage has forgotten them.

## 14. Completion rule for this revision

This revision may be committed only after:

- L1 files are absent from the latest public tree;
- their protected retention hashes match;
- public metadata contains no complete chunk text;
- dual-schema templates, prompts, scripts, tests, and aggregation rules agree;
- failure and denominator tables are explicit;
- statistical implementation defaults are frozen;
- B1/B2 caps and resource fields are defined;
- expert batching and correction lineage are defined;
- secret scanning finds no key, token, Authorization header, cookie, or
  protected credential;
- `G01=BLOCKED`, `G02-G15=PENDING`, and no gate is marked `PASS`.

Passing these checks still does not authorize a Pilot or formal run.
