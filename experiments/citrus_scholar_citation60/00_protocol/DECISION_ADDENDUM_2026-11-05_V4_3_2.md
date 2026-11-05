# Decision addendum v4.3.2: fourth external review correction

Decision version: `v4.3.2`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Parent baseline: `0ebdf0fddb0c12518bfdd9d638161462e388b953`
Execution authorization: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This record applies the fourth external review without changing the research
question, the 60-core/60-supplementary separation, or any gate to `PASS`.
Earlier decision records remain historical evidence.

## 1. Public-tree data boundary

The following citation60 assets are not released while G12 remains pending:

- complete frozen chunks and question-authoring records;
- frozen questions, target locators, manifests, sampling configuration, and
  corpus-scope metadata;
- citation run plans and disabled semantic-audit plans;
- the citation60 rows of the former combined validity packet.

The public validity packet and adjudication template now contain QA60 rows only.
Protected copies, bytes, SHA-256 values, and control reasons are declared in
`12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`. Files marked
`PENDING_RELEASE_REVIEW` are blocking states, not release authorization.

The Git repository remains public. Earlier commits still contain prior public
objects. Restricting repository access or requesting GitHub history cleanup is
a PI/repository-owner decision. This package does not rewrite history or force
push.

## 2. Execution authorization and retention

Every model, judge, Pilot, and formal-experiment entry is fail-closed. Execution
requires all three conditions:

```text
status == READY
execution_authorized == true
review_status in {APPROVED, APPROVED_FOR_EXECUTION}
```

Credentials belong only in a protected server environment file outside the Git
checkout. Request archives retain redacted bodies, endpoint host, `auth_present`,
hashes, actual response metadata, usage, timing, and every attempt. They never
retain an Authorization header or credential value.

LLM-judge aggregates and all human scoring materials are written to protected
`L1` storage. A separate file-level review is required before any `L2`
derivative can be published.

## 3. Judge correctness

- retries occur at most twice and only after a content-free transport failure
  or HTTP 408/429/500/502/503/504;
- malformed JSON, schema failure, empty model identity, and model mismatch are
  never retried as if they were transport failures;
- the response `model` must match the frozen role allowlist;
- missing or mismatched identity blocks strict aggregation;
- J3 returns exactly `disputed_values`, field-scoped
  `reason_codes_by_field`, `short_reason`, `answer_span_quote`, and
  `evidence_span_locators`; all extra top-level fields invalidate the response;
- J1/J2/J3 request, response, identity, token, cost, allowance, and attempt
  metadata remain traceable to each blind packet.

## 4. Blind packets and human scoring

Formal QA60 must resolve to 60 questions with exactly B0/B1/B2 in the planned
run set. Each question's three conditions use identical question text and the
same canonical frozen reference evidence. Raw-run SHA values stay only in the
protected blind mapping and are excluded from judge packets.

The human scoring implementation now provides:

- independent condition-blinded reviewer directories;
- Chinese instructions, question/answer CSV, reference-evidence CSV, CSV and
  XLSX score sheets, codebook, checklist, and attestation;
- strict primary-score validation;
- append-only correction validation;
- third-expert field-adjudication import;
- deterministic human consensus with system and semantic outcomes;
- human agreement and question-clustered uncertainty.

No Gold Answer and no semantic-support rating are created. citation60 retains
its deterministic target-chunk endpoint.

## 5. Statistical endpoint split

The confirmatory system endpoint is `egts_system_outcome` over all 60 planned
question slots per condition. Authentic infrastructure terminal states remain
in this denominator as system-task failures.

`human_consensus_egts` is the semantic-quality endpoint over authentically
evaluable answers only. Its scored coverage is reported with the denominator;
it is never silently treated as a complete 60-item denominator.

The unique confirmatory contrast is B2 versus B1:

- exact two-sided McNemar test;
- paired percentage-point difference;
- 10,000 question-within-stratum paired bootstrap replicates;
- NumPy `PCG64(20261104)`, percentile 95% interval;
- six fixed strata `L/V/W/X/P/N`;
- a resampled question retains all available condition rows.

Expert agreement uses only applicable authentic double score records and never
treats infrastructure failure as agreement.

## 6. Analysis implementation

`scripts/A01_analysis/run_strict_analysis.py` is the only current formal QA
analysis entry. It requires protected `L1` inputs and outputs and validates
the 60-question/180-slot structure, source clusters, and deterministic outcomes
before analysis.

`scripts/analyze_and_plot.py` and the old per-experiment summary shim are
`LEGACY_NON_EXECUTABLE`. They are retained only for provenance.

## 7. Unresolved blocker

The repository-history disclosure remains unresolved and requires PI action.
The citation60 protected assets, server connectivity, credentials, actual model
identities, Pilot calibration, and all G01-G15 evidence remain incomplete.
Static tests are not experiment results and cannot advance a gate.
