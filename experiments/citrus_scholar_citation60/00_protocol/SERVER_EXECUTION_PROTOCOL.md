# Server-only real-agent execution protocol

Version: `4.4.0`
Status: `READY_FOR_EXTERNAL_REVIEW`
Current decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
Observation contract: `00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md`
Experiment root: selected by the server operator
Experiment bind address: `127.0.0.1:8012` unless G01 records an approved
equivalent

This review commit does not start a server, create an account, call a provider,
run a question, judge an answer, or produce a result.

## 1. Execution boundary

All actual execution occurs on the dedicated experiment server:

- Git and environment verification;
- service/database/corpus/index checks;
- account registration and login;
- Agent and Judge API calls;
- real questions and long workflows;
- SSE, tool, retrieval, backend-log and artifact capture;
- Pilot, formal runs, scoring, analysis, figures and paper data.

The local workstation is limited to source/protocol editing, offline tests and
Git operations.

The experiment must not modify the production CitrusQA service, database,
sessions, uploads, logs, caches or artifact store. It may use the same final
reviewed source release in a separate experiment deployment with independent
runtime state.

## 2. Correct corpus boundary

The Agent may use the complete registered corpus through normal
CitrusScholar retrieval and source-reading tools. Questions, sources, chunks,
citations and retrieved text are normal experiment inputs/results.

The full corpus is not copied into GitHub. Repository records contain source
registry, index generation, hashes, permitted metadata, retrieval evidence and
reviewed derivatives.

An L1 archive root may be recorded separately for release safety, but it is not
a prerequisite for Stage 1 or Stage 2 Agent execution.

## 3. Server layout

```text
<experiment-root>/
  checkout/                   # exact reviewed commit
  config/                     # non-secret frozen configuration
  evidence/
    stage0/
    stage1/
    stage2/
    pilot/
    formal/
    judge/
    expert/
    analysis/
  logs/                       # service, API, task, retrieval and tool logs
  secrets/                    # mode 700 directory, mode 600 files, outside Git
  agent/state/                # independent accounts, sessions and databases
  agent/workspace/            # experiment uploads and generated artifacts
```

Raw evidence is write-once and remains on the server pending release review.
Reviewed redacted derivatives and SHA manifests may be committed to GitHub.

## 4. Isolation controls

1. Dedicated service user and service unit.
2. Separate database, sessions, uploads, artifacts, logs and caches.
3. Loopback-only service bind unless an authenticated HTTPS proxy/tunnel is
   approved.
4. Experiment user cannot write the production root.
5. Production credentials are unreadable to the experiment user.
6. Resource limits prevent experiment load from affecting production.
7. Each independent question uses a new session.
8. Different conditions and repeats never share a session.
9. Long workflows use their own new session.

These controls protect the environment and study independence. They do not
disable corpus retrieval.

## 5. Credentials

Use one protected environment file outside the Git checkout:

```text
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
CITRUS_EXPERIMENT_BASE_URL
CITRUS_EXPERIMENT_EVIDENCE_ROOT
CITRUS_PROBE_USERNAME
CITRUS_PROBE_PASSWORD
```

The environment directory is mode `700`; the file is mode `600`. Secrets are
not passed through command-line arguments or written to prompts, logs,
requests, screenshots or Git.

Authentication evidence records only:

```text
auth_present
set_cookie_present
http_status
request_id
response model
usage / cost / allowance
timestamps and SHA-256
```

It never records the Authorization value, Cookie value, password or API key.

The keys previously pasted into chat are exposed and are not reused.

## 6. GitHub synchronization

1. The local editor commits reviewed protocol and code changes to `main`.
2. External GPT reviews the exact commit/tree.
3. The experimental server fetches and checks out that explicit commit.
4. Work stops if the checkout is dirty, the parent differs, or the commit/tree
   cannot be proven.
5. The server writes evidence to the experiment root.
6. The server validates hashes, secret redaction and result schemas.
7. Reviewed derivatives are committed and pushed.
8. Each work unit ends with a report and pauses for external review.

Never use `git reset --hard`, force push or history rewriting.

## 7. Work units

### WU-00: v4.4 external review

Commit only the corrected protocol, candidate code, templates and offline
tests. Do not call a provider or execute a question.

### WU-01: Stage 0 environment/corpus/logging

Record commit/tree, deployed files, `/health`, service separation, database,
registered corpora, generation, index hashes, source accessibility, experiment
directories, log destinations and runtime versions.

Use
`01_preflight/G01_SERVER_ISOLATION_REPORT_TEMPLATE.json` and complete it before
Stage 1. The Stage 0 result must show that local `HEAD`, local tree, GitHub
`main` and GitHub tree all equal the externally reviewed SHA/tree, the worktree
is clean, `git fsck` passes, the experiment origin is allowlisted, all five
corpora are accessible, all 17 tools are registered, logs are locatable, and
the experiment account cannot write production state. The approved report SHA
is a Stage 1 precondition.

### WU-02: Stage 1 server/account/provider

Register a dedicated account, log in, verify `/api/v2/auth/me`, read
`/api/v2/config`, probe the Bailian Agent and OpenCode Go GLM/MiMo/MiniMax,
and retain every request attempt, actual model, usage, cost/allowance, status,
timing and SHA. Connectivity upgrades to `PILOT_READY`, never automatically
`READY`.

Provider success requires non-empty generated content, response ID, actual
model, frozen-allowed response identity, Judge JSON structure and valid usage
when returned. `CITRUS_AGENT_MODEL_ID`, the frozen model configuration, the
application `/api/v2/config` provider/model and the later Stage 2 trace must
agree. The protected environment file must resolve outside the Git checkout.
For every Judge role, the probe request model, configured `candidate_model_id`,
configured `allowed_model_ids`, model-identity allowlist and non-null actual
response model must agree; an `UNCONFIGURED` Judge configuration cannot pass.
The Stage 1 manifest must retain the role, request ID, requested model, actual
model, request SHA, usage, status and attempt path separately for J1, J2 and
J3.
No account or provider request is executed unless the approved Stage 0
isolation report and origin allowlist pass.

Rate-limit stress testing is not part of normal Stage 1. Throttling metadata
from ordinary responses is retained; retry behavior is verified offline. A
live throttling test requires a disposable isolated endpoint and an approved
budget. If it does not run, evidence records `DEFERRED`, not `PASS`.

### WU-03: Stage 2 end-to-end observation

Run one real question in a new session. Capture normal corpus retrieval, source
reading, SSE, tool calls, retrieval hits, backend logs, citations, final text,
model identity and timing. Run the v4.4 execution-evidence auditor.

Acceptance requires actual raw-response, SSE, retrieval, tool-result,
backend-log and artifact files whose bytes match recorded SHA-256 values. A
global session registry must prove unique, condition-independent sessions.
Scoring/control-path material is rejected from both tool arguments and tool
results. The raw API response content, response ID and actual model must match
the trace; SSE and retrieval captures must parse as event lists and match every
recorded event; each tool-result file must equal its inline result. Backend log
references must be real path+SHA objects and include the request, run or
response identity, with the request ID present in at least one log. B2 must
include at least one successfully `completed` `LOCAL` tool event; empty,
failed, cancelled or skipped-only tool histories cannot establish Stage 2
success. All failed attempts remain retained.

### WU-04: multi-tool and workflow

Run representative local retrieval, web, attachment, table review, citation
verification, export and long workflow cases. Each independent question or
condition uses a new session; one related long workflow uses its own session.

### WU-05: validity and fairness

Complete 120/120 two-reviewer validity certification and B0/B1/B2 fairness
evidence. Do not alter source access to create an artificial comparison.

### WU-06: Pilot

Run 36 independent Pilot answers, two-expert scoring, J1/J2 scoring, J3
disagreement adjudication, challenge cases and agreement checks. Pilot never
enters the formal denominator.

### WU-07: formal protocol lock

After Pilot and external review, freeze prompts, models, run plans, retrieval
settings, scoring schemas, blind maps, analysis code and source/index
manifests.

### WU-08 onward: formal experiments

Run QA60, Citation60, Route192, Composite24, optional repeat experiment and
WF01-WF07 as separate work units. Preserve all attempts, failures, scores and
artifact hashes.

## 8. Stage acceptance

A Stage 2 result is `PASS` only if the v4.4 auditor proves request/trace
identity, global session isolation, real Agent answer provenance, frozen model
identity/Stage 1 cross-binding, SSE, normal retrieval, tool events, successful
completed B2 status with a completed `LOCAL` tool, exact raw-response/SSE/
retrieval/tool-result binding, and actual backend-log/tool/artifact file SHA
binding.

A written plan, script import, synthetic fixture, provider connectivity alone
or file existence does not close an experiment gate.
