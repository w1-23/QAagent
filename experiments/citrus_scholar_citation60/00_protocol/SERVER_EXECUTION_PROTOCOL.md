# Server-only experiment execution protocol

Version: `1.0` (submitted for external review)
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Experiment server target: `/home/WZQ/citrus_experiment/`
Planned bind port: `127.0.0.1:8012`
Production path: `/home/WZQ/CitrusQA/` (read-only for this experiment)

## 1. Execution boundary

Run all experiment-dependent commands on the server: source and index checks, SQLite checks, package and unit tests, model connectivity probes, Pilot, formal runs, expert packet building, judging, aggregation, statistical analysis, and figure generation. The local workstation is used only to edit/review code and protocol files and to prepare Git commits; it does not run experiment, model, or data-analysis commands.

The experiment server uses an isolated instance and data paths. Do not start, stop, update, or write to the production service as part of this protocol. Do not use production accounts, session databases, uploads, logs, or artifact stores as the experiment store.

## 2. Isolated instance layout

Create the instance only after an authorized server work unit begins and the external GPT review of this plan is complete:

```text
/home/WZQ/citrus_experiment/
  checkout/                 # clone/fetch from w1-23/QAagent at an explicit commit
  02_config/                # experiment-only non-secret config snapshot
  agent/state/               # experiment-only DB, provider config, and sessions
  agent/data/                # authorized corpus and frozen index snapshots
  agent/workspace/           # experiment-only uploads and artifacts
  agent/logs/                # experiment-only application and run logs
  agent/.hf_cache/           # isolated or verified read-only model cache
  experiments/.../           # tracked plan, raw runs, scores, analysis, and paper data
  secrets/                   # credentials with directory mode 700 and file mode 600
```

Bind the experimental service to loopback port `8012`; use an authenticated SSH tunnel or approved HTTPS access for browser checks. Give it a distinct service unit/process name and environment file. Confirm it cannot write to production paths. Any reused immutable model cache must be verified read-only; corpus/index snapshots must be copied or mounted read-only and their generation and SHA recorded.

## 3. GitHub synchronization and immutable work units

1. The local editor commits only reviewed plan/code changes to GitHub; no experiment is run locally.
2. The server fetches from `w1-23/QAagent` and checks out a named commit. Record commit and tree SHA before each server work unit.
3. Before syncing, require a clean experiment checkout and confirm the expected parent SHA. If a checkout has local changes or the remote parent differs, stop and preserve the state; do not reset or force-push.
4. Save run outputs and evidence directly under the experiment package on the server. For each completed work unit, validate files and hashes on the server, commit/push that work unit, report SHA, tests/evidence and gate state, then wait for GPT review.
5. If GitHub credentials are unavailable on the server, stop and arrange a scoped secure credential. Do not copy experiment result directories to the local workstation as a fallback.
6. GitHub stores the experiment protocol, code, reproducibility inputs, permitted raw results, expert score records, judge outputs, analysis and paper artifacts. Credentials, reviewer identity linkage, and source materials that cannot be redistributed stay in protected storage with public-side hashes/locators and a redaction record.

## 4. Secrets and provider separation

Provision only rotated credentials after the secret-exposure issue is resolved. Store them in a server-only systemd `EnvironmentFile` outside the Git checkout or a protected credential manager. Restrict the directory/file to the service owner (`700`/`600`). Do not put secrets in shell history, command arguments, notebook output, request archives, screenshots, reviewer packets, or GitHub.

The Agent uses only `CITRUS_AGENT_API_KEY`, `CITRUS_AGENT_BASE_URL`, and `CITRUS_AGENT_MODEL_ID`. Judges use only `CITRUS_JUDGE_API_KEY` and `CITRUS_JUDGE_BASE_URL`. Request archiving must remove Authorization headers and record only endpoint host and `auth_present=true`. Scan before every push. At study close, remove credential files and record an audited deletion event; retain experiment records.

## 5. Work-unit sequence and stops

1. **Connectivity and isolation:** verify SSH, server identity, path permissions, port availability, GitHub read/write access, and that production DB/files are not shared. Record evidence. The last known read-only SSH probe to `211.69.141.120:28022` timed out; until rechecked successfully, G01 is blocked.
2. **Version/environment lock:** deploy the explicitly approved service commit into the isolated instance, capture `/health`, exact commit/tree, configuration/model IDs, corpus/index generations, tool registry, host/runtime, and file hashes. Do not update production.
3. **Provider probes:** after PI rotates credentials and confirms OpenCode Go research use, test the Bailian Agent candidate and each judge model separately. Save redacted request metadata and complete safe response metadata; verify response model IDs and provider-family separation.
4. **Server checks:** run package, schema, security, regression, SSE, SQLite, and artifact checks on the server and retain command/result logs. A failed check stays in the ledger and blocks dependent steps.
5. **Expert packet dry validation:** generate only test fixtures or packet-schema checks until the 120 question-validity reviews are locked and the 36 Pilot outputs exist. Do not expose formal answer records early.
6. **Pilot:** run the independent 12-question/36-condition batch. Both experts score all 36, judges 1/2 score all 36, judge 3 handles four-field disagreements, and challenge cases exercise the fatal categories. Pilot never enters formal denominators.
7. **Formal gates and collection:** after review of Pilot evidence and explicit gates, lock the Agent/judge configs, prompts, score sheets, packet maps, run plans, and input/source manifests. Then run the formal experiments in their separate directories.
8. **Analysis and manuscript:** score, adjudicate, analyze and generate figures on the server; preserve every raw input and derived output. Submit one auditable GitHub work unit at a time and pause for GPT review after each.

No gate is `PASS` because a plan was written or a command was attempted. Record `BLOCKED` with the command/evidence and specific blocker when a required external state is unavailable.
