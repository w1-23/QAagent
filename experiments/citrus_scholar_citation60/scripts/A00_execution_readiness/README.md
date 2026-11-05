# A00 real Agent execution-readiness audits

Protocol version: `4.4.0`
Status: `CANDIDATE_IMPLEMENTATION_AWAITING_EXTERNAL_REVIEW`
Execution in this repository review unit: `NONE`

These scripts prepare and audit real-server execution. They do not treat the
registered corpus, frozen question, or retrieved chunk as forbidden Agent
input. The experiment tests the actual CitrusScholar system.

| Script | Purpose |
|---|---|
| `00_repository_asset_audit.py` | verify externally reviewed commit/tree, remote `main`, Git object integrity, frozen inputs, lineage, and optional legacy archive integrity |
| `01_git_history_audit.py` | scan reachable history for credential patterns and report removed protected paths |
| `10_server_api_probe.py` | execute Stage 0-gated health, account auth-flow, runtime config, Agent/Judge provider and model-identity probes as immutable attempts |
| `11_transport_preflight.py` | credential-free TCP, SSH-banner and `/health` reachability evidence |
| `20_audit_agent_execution_evidence.py` | verify one real request/trace pair, actual evidence-file SHA-256 values, model identity, normal corpus retrieval, successful B2 completion, and global new-session isolation |
| `20_audit_agent_input_boundary.py` | superseded; exits without running |

## Correct execution model

The Agent is allowed to:

- receive a normal frozen question and source scope;
- search the registered CitrusScholar corpus;
- receive source/chunk IDs, excerpts and text through normal retrieval;
- read local literature and attachments through the platform;
- use normal tools and produce artifacts.

The experiment must not disable corpus access to make an audit pass.

Retained controls prevent experimental contamination:

- one independent question, one new session, empty conversation history;
- different conditions and repeats use different sessions;
- no scoring map, answer key, expert/Judge score, blind map, or other-condition
  answer is injected into the Agent request;
- Judge output never substitutes for the Agent answer;
- failed, cancelled, retried, partial and empty attempts remain recorded.

## Stage 0: repository, runtime, corpus and observability

On the experiment server after external review, both values must come from the
reviewed GitHub commit and must not be derived from the checkout:

```bash
REPO="$(git rev-parse --show-toplevel)"
REVIEWED_HEAD="<REVIEWED_COMMIT_SHA>"
REVIEWED_TREE="<REVIEWED_TREE_SHA>"
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/00_repository_asset_audit.py \
  --repo-root "$REPO" \
  --reviewed-head "$REVIEWED_HEAD" \
  --reviewed-tree "$REVIEWED_TREE" \
  --github-repo w1-23/QAagent \
  --require-public \
  --output-dir "/home/WZQ/citrus_experiment/evidence/stage0/$REVIEWED_HEAD"
```

The audit verifies local `HEAD`, local tree, GitHub `main`, GitHub tree, clean
worktree and `git fsck`. An attached `main` branch or a detached `HEAD` is
accepted only when both reviewed SHA values match exactly; any other branch is
blocked.

`CITRUS_EXPERIMENT_L1_ROOT` is optional. Omitting it does not block real Agent
testing. `--require-legacy-archive` is reserved for a separate archive-integrity
check and must not be used as an Agent execution gate.

Complete
`01_preflight/G01_SERVER_ISOLATION_REPORT_TEMPLATE.json` as the Stage 0 server
report. It records deployment SHA/tree and file hashes, service/database/session/
upload/artifact separation, production write-denial proof, five accessible
corpora, index generation and source hashes, all 17 registered tools, log
destinations, and the Python/dependency/system snapshot. Stage 1 refuses to run
unless this report is `PASS`, its SHA is frozen in the probe plan, and both
reviewed SHA values match.

## Stage 1: real server/API evidence

Validate the plan without executing it:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py \
  --plan experiments/citrus_scholar_citation60/01_preflight/SERVER_API_PROBE_PLAN.json
```

The checked-in plan is `UNCONFIGURED` and unauthorized. It uses the actual
account routes:

```text
/api/v2/auth/register
/api/v2/auth/login
/api/v2/auth/me
/api/v2/config
/health
```

The auth-flow probe retains the session Cookie only in memory and never writes
the Cookie value to an attempt file.

Only after external review and plan freeze may the server run:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py \
  --plan "/home/WZQ/citrus_experiment/config/SERVER_API_PROBE_PLAN_FROZEN.json" \
  --out "/home/WZQ/citrus_experiment/evidence/stage1/$REVIEWED_HEAD" \
  --model-config experiments/citrus_scholar_citation60/05_baselines/model_config.json \
  --judge-config experiments/citrus_scholar_citation60/05_baselines/judge_config.json \
  --stage0-report "/home/WZQ/citrus_experiment/evidence/stage0/$REVIEWED_HEAD/G01_SERVER_ISOLATION_RUNTIME_REPORT.json" \
  --execute
```

Each provider request is a separate immutable attempt. The evidence retains
safe request body, safe response body, actual response model, request ID,
usage, cost/list-cost/allowance when returned, HTTP status, timing and SHA.
No Authorization or Cookie value is archived.

A provider attempt passes only when the HTTP status is accepted, generated
content is non-empty, response ID and actual model are present, the actual
model is frozen-allowed, Judge content matches the required JSON structure,
and returned usage is structurally valid. The Agent probe environment model ID
must equal the frozen configuration. The application `/api/v2/config` provider
and model must also equal that configuration, and Stage 2 cross-binds the
saved Stage 1 manifest to the trace.

The experiment origin allowlist and approved Stage 0 report SHA are hard
preconditions. `/api/v2/config` does not expose the 17 tool names, so tool
registration is verified from the approved G01 report and its evidence hash,
not inferred from the model fields.

An EnvironmentFile must resolve outside the Git checkout. Mode/ownership alone
is insufficient. Rate-limit probing is `DEFERRED`, not passed: no destructive
stress test is performed; normal provider throttling metadata is retained;
retry behavior is tested offline; live probing requires a disposable isolated
endpoint and an approved budget.

## Stage 2: real end-to-end Agent evidence

The request and trace formats are:

```text
01_preflight/AGENT_EXECUTION_REQUEST_TEMPLATE.json
01_preflight/AGENT_EXECUTION_TRACE_TEMPLATE.json
```

Example server command:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py \
  --request "/home/WZQ/citrus_experiment/evidence/raw/$RUN_ID/request.json" \
  --trace "/home/WZQ/citrus_experiment/evidence/raw/$RUN_ID/trace.json" \
  --evidence-root "/home/WZQ/citrus_experiment/evidence/raw/$RUN_ID" \
  --expected-model-config experiments/citrus_scholar_citation60/05_baselines/model_config.json \
  --stage1-manifest "/home/WZQ/citrus_experiment/evidence/stage1/$REVIEWED_HEAD/PROBE_MANIFEST.json" \
  --session-registry "/home/WZQ/citrus_experiment/evidence/global_session_registry.csv" \
  --expected-condition B2 \
  --required-tool-groups LOCAL \
  --require-corpus-access \
  --out "/home/WZQ/citrus_experiment/evidence/stage2/$RUN_ID.audit.json"
```

The model configuration and Stage 1 manifest are mandatory acceptance inputs.
The session registry is strongly required: without it the report records
`cross_condition_contamination=NOT_ASSESSED` and cannot pass.

```text
--question-catalog
```

The audit requires:

1. matching request/trace identity fields and request SHA;
2. new empty session;
3. actual provider/model/response ID bound to the frozen model and Stage 1 response;
4. SSE start and terminal events;
5. normal retrieval and source locator evidence when requested;
6. tool names, arguments, statuses and control-material path protection;
7. actual raw-response, SSE, retrieval, tool-result and backend-log files whose
   real bytes match the recorded SHA-256 values;
8. raw Agent answer captured; final displayed answer == persisted Agent answer;
   any `done.answer` == that final answer after reviewed post-processing;
9. `answer_source=AGENT`;
10. no scoring/answer-key/other-condition material in request, tool arguments,
    tool results or result files;
11. at least one completed real B2 run with normal retrieval/tool execution;
12. global session registry uniqueness and no cross-condition reuse.

Normal `chunk_text` and `chunk_id` in retrieval or tool results are valid and
must not be flagged as leakage.

## Execution discipline

1. Outputs are write-once.
2. Real probes run only on the dedicated experiment server.
3. Credentials and Cookie values come only from protected server environment
   files and never enter evidence.
4. Missing inputs produce `BLOCKED`; they are never replaced by a guess.
5. Synthetic fixtures test code paths only and never become formal evidence.
6. The actual execution round starts only after external review of this
   repository commit.
