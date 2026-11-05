# CitrusScholar experiment audit index

Protocol version: `4.4.0`
Hardening revision: `4.4.2`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_2`
Execution authorization in this review unit: `NONE`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`
Reviewed parent commit:
`79a700c6b4c3ae582b980d68f3b4eca4b102267d`
Reviewed parent tree:
`af3631d5ed768e4d2aa53e16da6b1dbe3bd0edb9`

This file is the review entry point. It separates preparation and candidate
code from server evidence and results. It is not evidence that an account,
provider, gate, Pilot, or formal experiment passed.

## 1. v4.4.0 correction

The v4.3.x execution-boundary design treated questions and corpus access as
Agent-visible hazards. That interpretation is `SUPERSEDED`.

The authoritative v4.4.0 decision is
`00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`:

| Object | v4.4.0 rule |
|---|---|
| Test question | Normal Agent input; send it directly |
| Registered corpus | Available through normal CitrusScholar retrieval and reading |
| Retrieved `source_id`, `chunk_id`, `chunk_text`, title, excerpt | Valid evidence, not leakage |
| Normal local, web, attachment, audit, verification, and export tools | Enabled according to the condition |
| Scoring map, answer key, blind map, other-condition answer, Judge output | Prohibited from Agent input |
| One independent question | One new session with empty history |
| One long workflow | One dedicated workflow session for its related steps |
| Different conditions and repeats | Different new sessions and immutable run IDs |
| Synthetic fixture | Interface test only; never formal execution evidence |

`CITRUS_EXPERIMENT_L1_ROOT` is optional for Stage 1 and Stage 2 Agent testing.
L1 still governs protected retention and republication review outside Git.

The old
`scripts/A00_execution_readiness/20_audit_agent_input_boundary.py` is
`SUPERSEDED` by
`scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py`.

The 2026-11-05 external review of `8491db2` accepted the research
principles but blocked Stage 0-2 execution until its evidence auditors were
hardened. The correction is recorded in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_HARDENING.md`. It
does not change the v4.4.0 research design, corpus access, models, endpoint,
denominators, expert scoring or statistics.

The subsequent review of `f1dbbe0` accepted the research direction and
identified a consolidated set of Stage 1/Stage 2 evidence-binding and G01
gate defects. The single consolidated correction is recorded in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_CONSOLIDATED.md`.

The review of `2d8be31` accepted the consolidated evidence binding and allowed
Stage 0 read-only execution. Four remaining items were required before
Stage 1/Stage 2: remote credential redaction, normalized G01 evidence/deployment
binding, frozen tool-registry binding, and strict global session IDs plus SHA.
Their implementation is recorded in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_1_STAGE0_GATE_CLOSURE.md`.

The review of `79a700c` found four remaining executable-gate defects: deployment
file SHA values were not recomputed from the actual files, Stage 2 trusted a
self-reported LOCAL group, normal runners did not all enforce the three
authorization signals, and the service/client request IDs were not explicitly
cross-bound. It also identified the missing real B0/B1/B2 adapters, Pilot
runner, capture schema and complete four-dimension analysis output.

The v4.4.2 candidate closes these items without changing the research design:
all deployment files are content-SHA checked; the 17-tool registry freezes
name/group/category/version; all execution paths use explicit authorization;
dual request IDs are retained; B0/B1/B2 are real adapters; the loopback provider
proxy binds raw provider bodies to B2; the 36-run Pilot generator/runner is
implemented; and placeholder Pilot materials fail closed before `--execute`.
The implementation is recorded in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_2_SERVER_PILOT_GATE.md`.

## 2. Current facts

| Item | State |
|---|---|
| Real Agent account registration | `NONE` |
| Alibaba Cloud Agent call | `NONE` |
| OpenCode Go Judge call | `NONE` |
| Server experiment instance | `NOT_VERIFIED_FOR_V4_4` |
| Model/API credentials | not present in repository; rotated/reprovisioned on server only |
| Core QA60 | 60 candidate questions: 52 answerable, 8 unanswerable |
| Citation60 | 60 protected frozen questions; 180 planned runs |
| Expert scoring | candidate materials only; zero answer packets and zero scores |
| Machine judging | candidate implementation; no API identity or calibration evidence |
| Pilot | `NOT_STARTED` |
| Formal runs | `NONE` |
| Analysis/figures/manuscript results | `NONE` |

The final v4.4.2 credential-free transport check found TCP `28022` and
`8012` open, but SSH banner exchange timed out after approximately 10 seconds
and experiment `/health` timed out after 12 seconds with no response. No
authenticated execution occurred. This is a transport blocker, not Stage 0
evidence; server reachability and the deployed commit/tree must be rechecked
from the reviewed commit/tree.

Legacy Git history still reaches a removed
`03_benchmarks/citation60/chunks_FROZEN.jsonl` blob containing actual source
text. `01_preflight/LEGACY_CITATION60_SOURCE_TEXT_EXPOSURE_AUDIT.json` records
only its object hashes, byte count, record count, source count, and
redistribution-risk disposition. This is a public-release rights finding for
the PI/repository owner, not an Agent-access restriction and not a reason to
hide normal corpus retrieval from the Agent.

## 3. Locked experiment sizes

| Experiment | Fixed size |
|---|---:|
| Pilot | 12 questions x B0/B1/B2 = 36 |
| QA60 | 60 questions x B0/B1/B2 = 180 |
| Citation60 | 60 questions x B0/B1/B2 = 180 |
| Route192 | 192 |
| Multi24 | 24 |
| Citation repeat72 | 72 |

QA60 and Citation60 have separate prompts, run plans, endpoints, scoring, and
denominators. They are never pooled.

## 4. Stage definitions

| Stage | Required evidence | Current state |
|---|---|---|
| 0. Repository, runtime, corpus, observability | externally supplied reviewed commit/tree, clean checkout, remote tree equality, `git fsck`, complete G01 isolation/runtime report, registered corpora, index generation, source hashes, 17 tools, evidence/log destinations | Prior `79a700c` baseline was read-only Stage 0 eligible; v4.4.2 candidate awaits review. `G01=BLOCKED` until real server/runtime evidence exists |
| 1. Server, account, Providers | health, registration/login, authenticated session, actual Bailian Agent identity, three actual Judge identities, usage/cost/allowance, rate-limit behavior | `NOT_STARTED` |
| 2. Real end-to-end execution | one real question in a new session with normal corpus retrieval, SSE, tool/retrieval trace, backend logs, final answer, citations, timings, and model identity | `NOT_STARTED`; no request/trace pair |
| 3. Tool/workflow execution | real local, web, attachment, table-audit, citation-verification, export, and long-workflow evidence | `NOT_STARTED` |
| 4. Scoring | blinded two-expert scoring, third-expert field adjudication, LLM Judge J1/J2/J3, corrections and agreement | `CANDIDATE_ONLY` |
| 5. Pilot | 36 real runs; experts and J1/J2 score all; J3 only disputed fields; calibration thresholds | `NOT_STARTED` |
| 6. Formal experiment | QA60, Citation60, Route192, Multi24, optional repeats, and WF01-WF07 | `NOT_STARTED` |
| 7. Analysis and archive | strict denominators, statistics, figures, claim-source map, manifests, backups, SHA verification | `NOT_STARTED` |

No stage may be promoted from a document, fixture, or prior commit without its
own evidence.

## 5. Model and provider boundary

| Role | Provider | Candidate model | Scope |
|---|---|---|---|
| Agent | Alibaba Cloud Bailian | `deepseek-v4.1-flash` | all conditions and workflows; actual model from response |
| Judge 1 | OpenCode Go | `glm-5.3-flash` | all QA60 answers |
| Judge 2 | OpenCode Go | `mimo-v2.6-flash` | all QA60 answers independently |
| Judge 3 | OpenCode Go | `minimax-m3.1-flash` | four-field disagreement adjudication only |

The Judge family must not be DeepSeek and must not reuse the Agent credential,
runtime client, or endpoint. Candidate IDs do not prove availability. Stage 1
must record the requested and actual response models, version/fingerprint when
available, region/workspace, request ID, usage, cost, allowance, status, and
redacted request/response SHA-256 values.

The model and Judge configuration files are:

- `05_baselines/model_config.json`;
- `05_baselines/judge_config.json`;
- `01_preflight/SERVER_API_PROBE_PLAN.json`.

All three are `UNCONFIGURED` and have `execution_authorized=false` in this
review unit.

## 6. Observation and answer provenance

Every independent answer must be reconstructable from:

```text
request -> request_id -> account/session/run -> actual model/response ID
        -> SSE -> retrieval query/hit -> tool calls/results
        -> backend logs -> raw answer -> reviewed final answer
        -> persisted answer -> citations/locators -> artifacts and SHA-256
        -> errors/retries/terminal status -> Judge/expert provenance
```

For reviewed post-processing such as appending a verified download link, the
raw Agent answer need not equal the displayed final answer. The auditor requires
that:

- the raw Agent answer is retained;
- `answer_source=AGENT`;
- the final displayed answer equals the persisted answer;
- any SSE `done.answer` equals that final displayed answer;
- the request SHA-256, IDs, session, condition, model identity, tool statuses,
  retrieval evidence, and backend log references are bound.

The Stage 2 command and record contract are in
`00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md` and
`scripts/A00_execution_readiness/README.md`.

Stage 2 acceptance now additionally requires actual raw-response, SSE,
retrieval, tool-result, backend-log and artifact files whose real SHA-256
values match the trace; a frozen-model/Stage 1 cross-binding; a global session
registry marked `VERIFIED_CLEAN`; and one completed real B2 run. Missing
cross-run evidence is `NOT_ASSESSED`, not clean.

## 7. Scoring and statistical boundary

QA60 fields:

```text
answerability_correct
answer_correct
evidence_traceable
fatal_error
```

Deterministic aggregation:

```text
ANSWERABLE:
EGTS = answerability_correct AND answer_correct
       AND evidence_traceable AND NOT fatal_error

UNANSWERABLE:
answer_correct = evidence_traceable = not_applicable
EGTS = answerability_correct AND NOT fatal_error
```

Two experts independently score every evaluable Pilot and formal QA60 answer.
A third expert adjudicates field disagreements. Machine Judges are an
independent comparator, not a replacement for expert consensus.

The analysis uses exact two-sided McNemar and 10,000 question-within-six-strata
paired bootstrap replicates with
`numpy.random.Generator(numpy.random.PCG64(20261104))`. Citation60 reports only
deterministic target-chunk locator hit. It does not report semantic support.

## 8. Data and credential boundary

Real requests, SSE, tool/retrieval traces, backend logs, answers, citations,
artifacts, failures, retries, and SHA-256 manifests remain on the dedicated
server experiment root. Reviewed and redacted derivatives may be committed.

Credentials, passwords, cookies, Authorization values, and `.env` files must
never enter Git, logs, evidence, screenshots, prompts, expert packets, or Judge
packets. The credentials exposed in prior chat are treated as compromised and
must not be reused.

## 9. Authoritative reading order

1. `AUDIT_INDEX.md`
2. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_2_SERVER_PILOT_GATE.md`
3. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_1_STAGE0_GATE_CLOSURE.md`
4. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_CONSOLIDATED.md`
5. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4_HARDENING.md`
6. `00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4.md`
7. `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
8. `00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md`
9. `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
10. `00_protocol/02_实验执行与论文写作总计划.md`
11. `00_protocol/05_论文写作执行计划.md`
12. `00_protocol/EXPERT_SCORING_PROTOCOL.md`
13. `07_scoring/rubric.md`
14. `09_analysis/analysis_contract.md`
15. `03_benchmarks/UNIFIED_VALIDITY_AND_LLM_JUDGE_PROTOCOL.md`
16. `01_preflight/G01_SERVER_ISOLATION_REPORT_TEMPLATE.json`
17. `01_preflight/SESSION_REGISTRY_TEMPLATE.csv`
18. `05_baselines/model_config.json`
19. `05_baselines/judge_config.json`
20. `01_preflight/SERVER_API_PROBE_PLAN.json`
21. `01_preflight/gates.csv`
22. `scripts/A00_execution_readiness/README.md`
23. `PUBLICATION_SCOPE.md`
24. `01_preflight/LEGACY_CITATION60_SOURCE_TEXT_EXPOSURE_AUDIT.json`
25. `00_protocol/change_log.csv`

## 10. Pre-push verification

Required before pushing this revision:

- compile the experiment scripts;
- run the complete offline experiment test suite;
- run package and static-package audits;
- parse every JSON/JSONL file;
- check Markdown fences and CSV row widths;
- scan every file for credentials and prohibited credential filenames;
- run `git diff --check`;
- confirm changed paths stay under
  `experiments/citrus_scholar_citation60/`.

The executed commands and exact results are recorded in
`00_protocol/EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-05_V4_4.md`.

## 11. Stop condition

This revision ends after an ordinary push to public `main`. Do not register an
experiment account, create a server `.env`, call a model, run Stage 0-2, start
Pilot or formal runs, score answers, or produce research results until external
GPT has reviewed this exact commit/tree and the next work unit is explicitly
authorized.
