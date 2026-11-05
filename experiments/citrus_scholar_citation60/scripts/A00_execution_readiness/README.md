# A00 execution-readiness audits

Protocol version: `4.3.3`
Status: `CANDIDATE_IMPLEMENTATION_AWAITING_REVIEW`

This directory contains fail-closed, server-only checks that must run before the
Pilot. They are not experiment entry points and no script calls a model unless
its command explicitly requests a reviewed probe.

| Script | Purpose | Required outputs |
|---|---|---|
| `00_repository_asset_audit.py` | verify Git identity, public/private repository access, protected assets, frozen-input consistency, lineage, and non-overwriting evidence storage | `REPOSITORY_AUDIT.json`, `ASSET_INTEGRITY_REPORT.json`, `DATA_LINEAGE_MANIFEST.json` |
| `01_git_history_audit.py` | scan every reachable history blob for credential patterns and distinguish legacy protected paths from the current tree | one immutable `GIT_HISTORY_SECRET_AND_PROTECTED_PATH_AUDIT` JSON |
| `10_server_api_probe.py` | execute configured server, account, environment, Agent, Judge, admin, rate-limit, and model-identity probes from a declarative plan | one immutable attempt record per request plus `PROBE_MANIFEST.json` |
| `11_transport_preflight.py` | record credential-free TCP, SSH banner, and experiment `/health` reachability before any authenticated probe | one immutable `STAGE1_TRANSPORT_PREFLIGHT` JSON with a hashed endpoint |
| `20_audit_agent_input_boundary.py` | verify one-question/new-session requests and reject hidden locators, reference text, scores, other-condition answers, or protected-file tool access | one boundary audit JSON tied to request and trace SHA-256 |

## Agent boundary

The Agent is allowed to receive exactly one frozen question, its condition
policy, source-scope constraints, legal tool permissions, and the shared model
generation configuration. It may retrieve source material through the platform.

The following never enter an Agent request, context, prompt, attachment path, or
tool argument:

- `target_chunk_id`, target chunk text, or a target reference injected outside
  the normal platform search path;
- Gold/reference answers, scoring mappings, expert or machine judgments;
- the answer or trace from another condition or another question;
- files under the protected `L1` root, scoring, blind-map, or raw-run areas.

A normal platform search returning the target paper or chunk is allowed. The
boundary audit evaluates request inputs and tool arguments, not the legitimacy
of a search result.

## Execution rules

1. Outputs are write-once. Existing files are never overwritten.
2. Real probes run only on the isolated experiment server after authorization.
3. Credentials come only from protected environment variables/files outside
   the Git checkout. They are never printed or written to evidence.
4. A missing endpoint, credential, identity, or schema is `BLOCKED`; it is not
   replaced with a guess or a simulated success.
5. Synthetic tests exercise code paths only and never enter formal evidence.

## Server execution commands

These commands are templates for the isolated server after the corresponding
external review. Do not run them on the local workstation or copy credential
values into a shell history, plan, evidence file, or Git.

### Stage 0: repository and protected-asset audit

```bash
REPO="$(git rev-parse --show-toplevel)"
HEAD="$(git rev-parse HEAD)"
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/00_repository_asset_audit.py \
  --repo-root "$REPO" \
  --expected-head "$HEAD" \
  --github-repo w1-23/QAagent \
  --require-public \
  --l1-root "$CITRUS_EXPERIMENT_L1_ROOT" \
  --require-l1 \
  --output-dir "/home/WZQ/citrus_experiment/evidence/stage0/$HEAD"
```

Without `--l1-root` the script deliberately reports
`NOT_RUN_L1_ROOT_NOT_PROVIDED`; that is a scoped audit, not Stage 0
acceptance. `--require-l1` makes omission a blocker. The output directory must
not exist or must be empty, and every generated file is write-once.

### Stage 1: server and API probes

Run the credential-free transport check first. The endpoint is represented only
by SHA-256 in the committed evidence:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/11_transport_preflight.py \
  --host "$CITRUS_EXPERIMENT_SSH_HOST" \
  --ssh-port "$CITRUS_EXPERIMENT_SSH_PORT" \
  --http-port 8012 \
  --timeout-seconds 8 \
  --out "/home/WZQ/citrus_experiment/evidence/stage1/$HEAD/TRANSPORT_PREFLIGHT.json"
```

If the SSH banner or experiment health response times out, stop before writing
credentials or invoking `10_server_api_probe.py --execute`.

Schema validation is safe before authorization:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py \
  --plan experiments/citrus_scholar_citation60/01_preflight/SERVER_API_PROBE_PLAN.json
```

After the sixth review approves a separate Stage 1 work unit, the reviewed plan
must be frozen with `status=READY`, `execution_authorized=true`, and
`review_status=APPROVED_FOR_EXECUTION`. Only then may the server run:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py \
  --plan "/home/WZQ/citrus_experiment/config/SERVER_API_PROBE_PLAN_FROZEN.json" \
  --out "/home/WZQ/citrus_experiment/evidence/stage1/$HEAD" \
  --execute \
  --allow-rate-limit
```

Every HTTP/provider attempt is a separate immutable JSON file. The engine
retains safe response bodies, actual model identity, status, timing, usage,
cost/list-cost/allowance when returned, and request/response SHA-256 values.
It redacts credential values and never archives `Authorization`.

### Stage 2: one-question input-boundary audit

All arguments are mandatory. The audit refuses an unpinned question catalog,
hidden-reference source, L1 root, repository root, condition, model config, or
tool-group allowlist.

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/20_audit_agent_input_boundary.py \
  --request "/home/WZQ/citrus_experiment/raw/REQUEST.json" \
  --trace "/home/WZQ/citrus_experiment/raw/TRACE.json" \
  --question-catalog "/home/WZQ/citrus_experiment/L1/frozen_question_catalog.csv" \
  --hidden-reference "/home/WZQ/citrus_experiment/L1/hidden_reference.jsonl" \
  --l1-root "/home/WZQ/citrus_experiment/L1" \
  --repo-root "$REPO" \
  --expected-condition B1 \
  --expected-model-config "/home/WZQ/citrus_experiment/config/request_model_config_FROZEN.json" \
  --allowed-tool-groups "LOCAL" \
  --out "/home/WZQ/citrus_experiment/evidence/stage2/REQUEST.boundary.json"
```

The trace must carry the exact `request_sha256`, `request_id`, `session_id`,
`run_id`, `question_id`, and `condition` from the request. A normal tool result
may contain the target chunk ID after legitimate platform retrieval; only
direct injection into request fields or protected tool arguments is rejected.
