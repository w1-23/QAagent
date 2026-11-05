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
| Public-audit visibility correction | `6979aaaab9536f3eaf672c02763cafef232c8c2d` |
| Stage 0-2 evidence | this commit; source evidence was generated against `6979aaa` |

The fifth review correction is recorded in
`00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`. Its review handoff is
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_3.md`.
The sixth-round execution evidence and stop state are recorded in
`00_protocol/EXECUTION_ADDENDUM_2026-11-05_STAGE0_1_2.md` and
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_3_STAGE0_1_2.md`.

## 2. Current facts

| Item | State |
|---|---|
| Formal experiment | `NOT_STARTED` |
| Formal results | `NO_RESULTS` |
| GitHub repository | `PUBLIC`; anonymous metadata, raw file, tree, and history reads verified |
| Server work | transport attempted; Stage 1 blocked before credentials/API calls |
| G01-G15 | G01 `BLOCKED`; G02-G15 `PENDING` |
| PASS gates | `0` |
| Agent | Bailian candidate `deepseek-v4.1-flash`, `UNCONFIGURED` |
| Judges | OpenCode Go GLM/MiMo/MiniMax, `UNCONFIGURED` |
| Model/API calls | none; transport blocked, plan refused execution |
| Core QA60 | 60 candidate questions: 52 answerable, 8 unanswerable |
| Citation60 | 60 protected frozen questions; 180 planned runs |
| Human scoring | candidate implementation; zero answer packets and zero scores |
| Machine judging | candidate implementation; execution blocked |
| Synthetic dry run | visibly labelled interface fixture; never a result |
| Stage 0 | repository audit `PASS`; asset acceptance `BLOCKED` because L1 root is unavailable |
| Stage 2 | not run; no real request/trace pair |
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

The PI approved restoring the repository to Public for continuous external
review. Before the change, the current tree and all 910 reachable text blobs
were scanned for the two provider key prefixes, Bearer Header values, and
assigned API Keys; no match was found. After the change, anonymous GitHub API,
raw-file, and Git-ref reads succeeded.

A follow-up audit at `6979aaa` scanned 915 text blobs and found zero
credential matches. The current tree contains zero protected citation60 paths,
but legacy Git history still reaches 14 protected L1 paths. The PI authorized
Public visibility without a history rewrite; this known legacy-object exposure
is disclosed for external review rather than represented as a clean history.

Evidence:
`01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json`.
The earlier Private-state evidence remains unchanged as historical evidence.
No history rewrite, force push, or protected-material deletion was performed.
Public visibility does not authorize redistribution of L1 material.

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
| 0. Repository and assets | repository audit, 16 L1 byte/SHA checks, frozen-input consistency, lineage | repository `PASS`; asset acceptance `BLOCKED_L1_ROOT_NOT_PROVIDED` |
| 1. Server/API | server, registration, login, Go, Agent, admin, environment, rate-limit, identity | TCP opened but SSH banner and 8012 `/health` timed out; no credential/API call |
| 2. Input isolation | one-question request, new session, request/session binding, forbidden fields/tokens/paths, tool trace | `BLOCKED`; no real request/trace pair, synthetic fixtures rejected as evidence |
| 3. B0/B1/B2 integration | real endpoint calls, actual tools, SSE, citations, artifacts, model identity | `BLOCKED` |
| 4. Scoring | six blinded batches, attestations, corrections, third expert, AC1/kappa/coverage | candidate implementation; no real packet |
| 5. Pilot | 36 real runs; both experts and J1/J2 score all; J3 on disagreement | `NOT_STARTED` |
| 6. Analysis | complete denominators, McNemar, bootstrap, Wilson, agreement | implementation only; no result |
| 7. Archive | Git bundle, two backups, SHA manifest, dependency lock, licence record | not started |

New readiness code is under `scripts/A00_execution_readiness/`.
The credential-free transport diagnostic is `11_transport_preflight.py`.
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
2. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_3_3_STAGE0_1_2.md`
3. `00_protocol/EXECUTION_ADDENDUM_2026-11-05_STAGE0_1_2.md`
4. `01_preflight/STAGE0_1_2_EXECUTION_REPORT_2026-11-05.md`
5. `01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json`
6. `01_preflight/stage0/6979aaaab9536f3eaf672c02763cafef232c8c2d/STAGE0_AUDIT_MANIFEST.json` and `STAGE0_SUPPLEMENTAL_MANIFEST.json`
7. `01_preflight/stage1/6979aaaab9536f3eaf672c02763cafef232c8c2d/TRANSPORT_PREFLIGHT.json`
8. `01_preflight/stage2/STAGE2_PRECONDITION_REPORT_6979aaa.json`
9. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`
10. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
11. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
12. `07_scoring/rubric.md`
13. `07_scoring/expert/SCORING_CODEBOOK_v4.3.2.md`
14. `09_analysis/analysis_contract.md`
15. `09_analysis/FAILURE_DENOMINATOR_MATRIX.md`
16. `12_reproducibility/STATISTICAL_IMPLEMENTATION_SPEC.md`
17. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
18. `PUBLICATION_SCOPE.md`
19. `12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json`
20. `01_preflight/SERVER_API_PROBE_PLAN.json`
21. `scripts/A00_execution_readiness/README.md`
22. `05_baselines/baseline_contract.md`
23. `00_protocol/02_实验执行与论文写作总计划.md`
24. `00_protocol/05_论文写作执行计划.md`
25. `11_manuscript/storyline_and_writing_plan.md`
26. `00_protocol/change_log.csv`

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
pytest: 72 passed, 5 skipped
package check: PASS
static package audit: PASS; 284 files secret-scanned
git diff --check: PASS
JSON parse check: PASS
credential-prefix scan: PASS
changed paths outside experiments/citrus_scholar_citation60/: none
```

The five skipped tests require protected citation60 inputs and were not treated
as passed. Stage 0 ran against the public repository but not against a protected
L1 root; the protected-asset manifest therefore reports 16 declared and 0
verified. Stage 1 stopped at the SSH-banner/HTTP-health transport boundary
before reading credentials or making an Agent/Judge request. Stage 2 stopped
before invocation because no real request/trace pair exists. No database,
Agent, Judge, Pilot, formal experiment, scoring, analysis, or paper result was
run. Passing static checks does not authorize a model, server, Pilot, or formal
run.

## 10. Stop condition

This revision ends after an ordinary push to public `main`. Wait for the sixth
external GPT review. Do not start authenticated Stage 1 probing, Stage 2,
Pilot, formal experiments, scoring, analysis, or paper results before review.
