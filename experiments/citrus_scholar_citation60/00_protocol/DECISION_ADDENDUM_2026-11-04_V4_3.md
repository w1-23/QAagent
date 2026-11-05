# Decision addendum v4.3: protocol revision after second external review

Status: `HISTORICAL_REVIEW_RECORD`
Decision version: `v4.3`
Date: `2026-11-04`
Repository: `w1-23/QAagent`
Review baseline: `9719f40daa8d2e4122d451bedafdeca271b5e9d5`
Parent baseline: `b6bd31327b34584b0f3b5ec7ac6a7974d508efb2`
Execution authorization: `NONE`

This document records the second-review decisions. It does not approve a
Pilot, prove model access, change a gate to `PASS`, or authorize server,
database, model, scoring, or analysis commands. The v4.2 decision record and
all prior Git objects remain unchanged as historical records. Where this file
conflicts with v4.2 or any plan, this v4.3 file controls after external review
approval.

## 1. Design retained

The following design is retained without reopening:

- CitrusScholar is evaluated as an integrated evidence-and-workflow system,
  not as a new foundation model, RAG algorithm, autonomous-discovery method,
  or broad crop-generalization claim.
- `B0` is a model-only diagnostic baseline with no local retrieval, web,
  attachment, or tool access.
- `B1` is a fair simple-RAG comparator using the same Agent model, sources,
  attachment text, parsing results, permissions, and a frozen comparable
  retrieval budget as `B2`.
- `B2` is the locked complete CitrusScholar experimental instance.
- Core `E01_QA60_EGTS` remains 60 questions: 52 `ANSWERABLE` and 8
  `UNANSWERABLE`, with 180 B0/B1/B2 runs.
- Supplementary `E01_SUPP_CHUNK_CITATION60` remains 60 independently frozen
  target-chunk questions, 180 runs, with exact final-citation target-chunk
  identity as its only primary endpoint.
- Route192, Composite24, and WF01-WF07 remain separate experiments and are
  never pooled with either E01 dataset.
- Two human experts independently score all 36 Pilot and all 180 formal QA60
  answers. A third human expert adjudicates every disputed rubric field.
  Human consensus is the QA60 primary measurement.
- GLM-5.3-Flash (J1), MiMo-V2.6-Flash (J2), and MiniMax-M3.1-Flash (J3) remain
  candidate judge models. They share one OpenCode Go service entry point, so
  the paper must describe them as judges from three model families routed
  through one service, not as three independent service providers.
- No Gold Answer or Gold Claim is created. Citation60 receives no
  semantic-support rating.
- `B2 vs B1` is the sole primary confirmatory comparison.

## 2. Seven P0 corrections

| ID | v4.3 correction | Authoritative implementation in this review unit |
|---|---|---|
| P0-1 | Replace unconditional public release with `L0/L1/L2` data classes and release checks | `PUBLICATION_SCOPE.md`, `SERVER_EXECUTION_PROTOCOL.md`, asset ledger |
| P0-2 | Rename `evidence_used` to `evidence_traceable` and define operational decision boundaries | `EXPERT_SCORING_PROTOCOL.md`, `07_scoring/rubric.md`, `SCORING_CODEBOOK_v4.3.md` |
| P0-3 | Add score, field-adjudication, issue, attestation, and human-consensus schemas | `07_scoring/expert/*` |
| P0-4 | Freeze B2-vs-B1 as the unique primary comparison with exact McNemar and stratified paired bootstrap | `09_analysis/analysis_contract.md` |
| P0-5 | Prohibit any model-generated final `decision`; machine output uses four fields only | unified judge protocol, v4.3 prompt candidates |
| P0-6 | Standardize Agent variables to `CITRUS_AGENT_API_KEY/BASE_URL/MODEL_ID` | server protocol and model configuration |
| P0-7 | Split `strict-human`, `strict-judge`, `strict-citation`, and `strict-all` | analysis contract and execution README |

## 3. Scoring authority

`00_protocol/EXPERT_SCORING_PROTOCOL.md` is the sole answer-scoring authority.
The four fields are:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

For answerable items:

```text
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error
```

For unanswerable items:

```text
answer_correct = not_applicable
evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

No expert or model supplies a final PASS/FAIL field. A deterministic script
computes every provisional, adjudicated, consensus, and machine outcome.

Reporting denominators are fixed:

| Measure | Denominator |
|---|---:|
| Human-consensus EGTS | 60 |
| Answerability correctness | 60 |
| Answer correctness | 52 |
| Evidence traceability | 52 |
| Fatal-error rate | 60 |

The 52 answerable and 8 unanswerable strata are also reported separately.

## 4. Agreement and primary statistics

Human agreement is reported for each of the four fields and for the two
experts' provisional EGTS. Gwet's AC1 is the primary chance-corrected
coefficient; Cohen's kappa is supplementary; raw agreement is mandatory.
Confidence intervals use a question-clustered bootstrap retaining the B0/B1/B2
triplet for each resampled question.

The unique confirmatory comparison is `B2 vs B1` on human-consensus EGTS:

- two-sided exact McNemar test;
- paired success-rate difference in percentage points;
- 95% confidence interval from 10,000 stratified paired bootstrap replicates;
- fixed seed `20261104`;
- strata L/V/W/X/P/N;
- resampling unit = question, retaining all three condition rows;
- complete paired 2x2 transition table.

`B1 vs B0` and `B2 vs B0` are descriptive diagnostics. They do not support a
primary architecture-superiority claim. The P stratum's B0 limitation is
reported explicitly.

Other dimension-level inference is secondary/exploratory and, if tested,
uses Holm correction. The 180 runs are not treated as 180 independent
questions. No claim is generalized beyond the fixed 60-question benchmark.

## 5. Retry and strict-analysis separation

An attempt may be retried at most twice only for a transport-level failure
that produced no answer content. Any attempt that produced substantive output
is never retried because its quality is poor. Every attempt and the lineage
remain in the denominator and evidence record.

Analysis gates are independent:

- `strict-human`: authentic runs, two complete expert score files, and all
  required human adjudications are present;
- `strict-judge`: J1/J2 and all required J3 records are complete;
- `strict-citation`: 180 deterministic citation observations are complete;
- `strict-all`: all of the above plus the other required modules are complete.

A judge outage cannot suppress a valid human-primary analysis. Any missing
independent module remains visible and prevents `strict-all`.

## 6. Public and restricted data

The GitHub repository is public. All study data remain retained, but not all
records may be publicly redistributed.

| Class | Content | GitHub public repository |
|---|---|---|
| `L0` | API keys, cookies, passwords, authentication data, private identity linkage | Prohibited |
| `L1` | Non-redistributable full text, restricted excerpts, private SOPs, raw sensitive logs, identity maps | Prohibited; protected server storage only |
| `L2` | Public-safe questions, permitted evidence locators/excerpts, redacted run records, SHA values, scores, statistics, manuscript materials | Allowed only after release review |

For `L1`, the public package records source SHA, locator, access class,
redaction lineage, and public derivative SHA. Raw evidence is never deleted
merely to satisfy public-release rules. Every run is checked for account IDs,
signed URLs, Authorization headers, personal data, attachment leakage, and
source redistribution rights before public release.

## 7. Server isolation

All executable experiment work remains server-only under
an operator-defined protected experiment root. The experiment service must use a dedicated
OS service user, isolated database and storage, distinct environment file,
loopback port `8012`, and no write access to the production root. Preferred
controls are separate filesystem ownership, read-only mounts for immutable
inputs, and an ACL/AppArmor or equivalent policy; operator discipline alone is
not an isolation control.

Agent credentials are only:

```text
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
```

Judge credentials are only:

```text
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
```

The candidate Agent model ID remains `deepseek-v4.1-flash`; actual response
identity, version, region/workspace, and terms must be probed and frozen on the
server. No configuration is `READY` before connectivity, terms confirmation,
Pilot calibration, blind checks, and retention checks.

## 8. Implementation hold

This review unit updates protocol, templates, schemas, and documentation only.
It does not implement or execute the new judge dispatcher, aggregator, packet
builder, adapter, prompt runtime, or analysis code. Existing runtime scripts,
prompts, and configuration that still encode legacy behavior are explicitly
not authorized for execution. Their replacement is a separate v4.3
implementation work unit after external approval of this protocol.

The latest server check available to that work unit did not succeed. G01 is recorded
as `BLOCKED`, and G02-G15 remain `PENDING`. This document changes no gate to
`PASS` and supplies no gate evidence.

## 9. Supersession and traceability

- The v4.2 `DECISION_ADDENDUM_2026-11-04_JUDGE_MODELS_OPENCODE_GO.md` remains
  intact as the prior reviewed baseline.
- This v4.3 record supersedes v4.2 only for the seven corrections and details
  stated here; all other retained design decisions remain in force.
- Any later change to models, prompts, endpoints, scores, denominators,
  statistics, data classes, or safety boundaries requires a new append-only
  `change_log.csv` row and external review.
