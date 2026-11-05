# CitrusScholar experiment audit index

Protocol version: `4.3.3`
Review state: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This is the review entry point. It separates plans and candidate code from
server evidence and results. It is not evidence that a gate, provider, Pilot,
or experiment passed.

## 1. Version lineage

| Label | Commit |
|---|---|
| v4.3.2 fifth-review baseline | `d45bf93897a4305b734a9a3d2abdfc0da03eb600` |
| v4.3.3 implementation | resolved from the pushed `main` commit for `fix: implement fifth review execution boundaries` |

The fifth review correction is recorded in
`00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`. Its review handoff is
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_3.md`.

## 2. Current facts

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| GitHub repository | `PRIVATE`, authenticated contents/tree/history readable |
| Server work | `NOT_PERFORMED_IN_THIS_REVIEW_UNIT` |
| G01-G15 | G01 `BLOCKED`; G02-G15 `PENDING` |
| PASS gates | `0` |
| Agent | Bailian candidate `deepseek-v4.1-flash`, `UNCONFIGURED` |
| Judges | OpenCode Go GLM/MiMo/MiniMax, `UNCONFIGURED` |
| Model/API calls | none in this review unit |
| Core QA60 | 60 candidate questions: 52 answerable, 8 unanswerable |
| Citation60 | 60 protected frozen questions; 180 planned runs |
| Human scoring | candidate implementation; zero answer packets and zero scores |
| Machine judging | candidate implementation; execution blocked |
| Synthetic dry run | visibly labelled interface fixture; never a result |
| Manuscript | plans and templates only |

## 3. Fifth-review correction

The following distinction is authoritative:

| Object | Agent visibility | Use |
|---|---|---|
| current frozen question | allowed, exactly one | test input |
| platform-retrieved source | allowed through legitimate tools | evidence gathering |
| target chunk ID | prohibited from Agent input | deterministic post-answer citation verification |
| target chunk text injected outside normal retrieval | prohibited | frozen reference only |
| answer key, scoring map, expert/judge score | prohibited | post-run scoring only |
| another condition's answer/session | prohibited | isolation audit |

Frozen questions and scripts are not intrinsically an experiment leak.
The former v4.3.2 statement that public question disclosure was itself a P0
validity failure is `SUPERSEDED`. The retained issue is simulation provenance,
release hygiene, and Agent input/tool-boundary enforcement.

The repository is now Private. This was verified by reading authenticated
metadata, `main`, `AUDIT_INDEX.md`, the recursive tree, and commit history.
Evidence: `01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05.json`.
Historical Git objects remain; no history rewrite, force push, or deletion was
performed.

## 4. Locked study design

| Experiment | Fixed size |
|---|---:|
| Pilot | 12 questions x B0/B1/B2 = 36 |
| QA60 | 60 questions x B0/B1/B2 = 180 |
| Citation60 | 60 questions x B0/B1/B2 = 180 |
| Route192 | 192 |
| Multi24 | 24 |
| Citation repeat72 | 72 |

The confirmatory system endpoint is `egts_system_outcome`.
`human_consensus_egts` is a separate semantic endpoint with its actual scored
coverage reported. QA60 and Citation60 are never pooled.

Every question-condition-run is an independent API request, Agent execution,
session, and raw record. No multiple-question prompt, prior-answer propagation,
cross-condition memory, overwrite, or failure-record deletion is allowed.

## 5. Executable audit stages

| Stage | Required evidence | Current state |
|---|---|---|
| 0. Repository and assets | repository audit, 16 L1 byte/SHA checks, frozen-input consistency, lineage | code and tests present; server evidence not run |
| 1. Server/API | server, registration, login, Go, Agent, admin, environment, rate-limit, identity | declarative plan and fail-closed runner present; unconfigured |
| 2. Input isolation | one-question request, new session, request/session binding, forbidden fields/tokens/paths, tool trace | strict fail-closed auditor and synthetic tests present |
| 3. B0/B1/B2 integration | real endpoint calls, actual tools, SSE, citations, artifacts, model identity | `BLOCKED` |
| 4. Scoring | six blinded batches, attestations, corrections, third expert, AC1/kappa/coverage | candidate implementation; no real packet |
| 5. Pilot | 36 real runs; both experts and J1/J2 score all; J3 on disagreement | `NOT_STARTED` |
| 6. Analysis | complete denominators, McNemar, bootstrap, Wilson, agreement | implementation only; no result |
| 7. Archive | Git bundle, two backups, SHA manifest, dependency lock, licence record | not started |

New readiness code is under `scripts/A00_execution_readiness/`.
The checked-in API probe plan is
`01_preflight/SERVER_API_PROBE_PLAN.json` and intentionally has:

```text
status = UNCONFIGURED
execution_authorized = false
review_status = AWAITING_SIXTH_EXTERNAL_GPT_REVIEW
```

## 6. Scoring and statistics boundary

Core QA60 uses:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

Experts do not write PASS/FAIL. Deterministic scripts compute:

```text
ANSWERABLE:
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error

UNANSWERABLE:
answer_correct = evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

Two experts independently score every evaluable Pilot and formal answer. A third
expert arbitrates field disagreements. Machine judges are an independent
comparator, not a replacement for human consensus.

Statistics remain exact two-sided McNemar and 10,000 question-within-six-strata
paired bootstrap replicates using
`numpy.random.Generator(numpy.random.PCG64(20261104))`. citation60 reports only
deterministic target-chunk locator hit. No semantic-support rate is produced.

## 7. Credential and evidence boundary

The keys previously pasted into chat are treated as exposed and must be
rotated. They are not reused and do not appear in this repository.

New credentials exist only in a server `EnvironmentFile` or equivalent
protected environment outside the Git checkout, with directory mode `700` and
file mode `600`.

Evidence may retain redacted request bodies, endpoint paths, `auth_present`,
actual response model, usage/cost/allowance when returned, status, timing,
hashes, and every attempt. It may not retain an Authorization value, cookie,
password, API key, or a hash derived from secret request content.

## 8. Authoritative reading order

1. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_3.md`
2. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`
3. `01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05.json`
4. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
5. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
6. `07_scoring/rubric.md`
7. `07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`
8. `09_analysis/analysis_contract.md`
9. `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`
10. `12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`
11. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
12. `PUBLICATION_SCOPE.md`
13. `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`
14. `01_preflight/SERVER_API_PROBE_PLAN.json`
15. `scripts/A00_execution_readiness/README.md`
16. `05_baselines/baseline_contract.md`
17. `00_protocol/02_实验执行与论文写作总计划.md`
18. `00_protocol/05_论文写作执行计划.md`
19. `11_manuscript/storyline_and_writing_plan.md`
20. `00_protocol/change_log.csv`

## 9. Pre-push checks

Required offline checks for this commit:

- `python -m compileall -q experiments/citrus_scholar_citation60/scripts`
- all experiment tests, including `A00_execution_readiness`;
- `python experiments/citrus_scholar_citation60/scripts/check_package.py`;
- `python experiments/citrus_scholar_citation60/scripts/audit_static_package.py`;
- `git diff --check`;
- gate-state, credential, protected-path, and changed-path checks.

Pre-push result on 2026-11-05:

```text
compileall: PASS
pytest: 67 passed, 5 skipped
package check: PASS
static package audit: PASS; 265 files secret-scanned
git diff --check: PASS
JSON parse check: PASS
credential-prefix scan: PASS
changed paths outside experiments/citrus_scholar_citation60/: none
```

The five skipped tests require protected citation60 inputs and were not treated
as passed. Stage 0 did not run against a protected L1 root; the protected-asset
manifest therefore reports 16 declared and 0 verified. No server, database,
Agent, Judge, Pilot, formal experiment, scoring, analysis, or paper result was
run. Passing these checks does not authorize a model, server, Pilot, or formal
run.

## 10. Stop condition

This revision ends after an ordinary push to private `main`. Wait for the sixth
external GPT review. Do not start Stage 1 probing, Pilot, formal experiments,
scoring, analysis, or paper results before review.
