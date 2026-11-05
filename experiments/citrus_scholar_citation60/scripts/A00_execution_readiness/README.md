# A00 real Agent execution-readiness audits

Protocol version: `4.4.0`
Status: `CANDIDATE_IMPLEMENTATION_AWAITING_EXTERNAL_REVIEW`
Execution in this repository review unit: `NONE`

These scripts prepare and audit real-server execution. They do not treat the
registered corpus, frozen question, or retrieved chunk as forbidden Agent
input. The experiment tests the actual CitrusScholar system.

| Script | Purpose |
|---|---|
| `00_repository_asset_audit.py` | verify repository identity, frozen inputs, lineage, and optional legacy archive integrity |
| `01_git_history_audit.py` | scan reachable history for credential patterns and report removed protected paths |
| `10_server_api_probe.py` | execute health, account auth-flow, runtime config, Agent/Judge provider, rate-limit and model-identity probes as immutable attempts |
| `11_transport_preflight.py` | credential-free TCP, SSH-banner and `/health` reachability evidence |
| `20_audit_agent_execution_evidence.py` | verify one real request/trace pair, normal corpus retrieval, tool/SSE/backend-log capture, answer provenance and new-session isolation |
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

On the experiment server after external review:

```bash
REPO="$(git rev-parse --show-toplevel)"
HEAD="$(git rev-parse HEAD)"
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/00_repository_asset_audit.py \
  --repo-root "$REPO" \
  --expected-head "$HEAD" \
  --github-repo w1-23/QAagent \
  --require-public \
  --output-dir "/home/WZQ/citrus_experiment/evidence/stage0/$HEAD"
```

`CITRUS_EXPERIMENT_L1_ROOT` is optional. Omitting it does not block real Agent
testing. `--require-legacy-archive` is reserved for a separate archive-integrity
check and must not be used as an Agent execution gate.

Runtime corpus availability, index generation, service identity, database and
log destinations are separately captured in the Stage 0 server report.

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
  --out "/home/WZQ/citrus_experiment/evidence/stage1/$HEAD" \
  --execute
```

Each provider request is a separate immutable attempt. The evidence retains
safe request body, safe response body, actual response model, request ID,
usage, cost/list-cost/allowance when returned, HTTP status, timing and SHA.
No Authorization or Cookie value is archived.

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
  --expected-condition B2 \
  --required-tool-groups LOCAL \
  --require-corpus-access \
  --out "/home/WZQ/citrus_experiment/evidence/stage2/$RUN_ID.audit.json"
```

Optional exactness checks:

```text
--question-catalog
--expected-model-config
```

The audit requires:

1. matching request/trace identity fields and request SHA;
2. new empty session;
3. actual provider/model/response ID;
4. SSE start and terminal events;
5. normal retrieval and source locator evidence when requested;
6. tool names, arguments, statuses and control-material path protection;
7. backend log references and hashes;
8. raw Agent answer captured; final displayed answer == persisted Agent answer;
   any `done.answer` == that final answer after reviewed post-processing;
9. `answer_source=AGENT`;
10. no scoring/answer-key/other-condition material injection.

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
