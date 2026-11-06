# DSH execution handoff: server Stage 0 recovery and gated experiment start

Handoff date: `2026-11-06`
Handoff status: `AWAITING_SERVER_RECOVERY_AND_EXTERNAL_GPT_REVIEW`
Repository: `w1-23/QAagent`
Branch: `main`
Code baseline commit:
`994a44beff5ccdf60493252ac3aaeb7c7263c794`
Code baseline tree:
`582f53c635376afe82a1330a2be94d58a1a6795f`
Code baseline parent:
`57fa07fb7d58d99cb8311bb1bb1b12ca184b94ae`
Commit author: `Codex <codex@localhost>`

This document is a handoff from the local Codex operator to `dsh`. The local
operator cannot currently reach the experiment server. The next executor must
work from GitHub and the actual server state, not from this document as evidence
that any gate has passed.

At baseline `994a44b`, local `HEAD`, `origin/main` and GitHub `main` matched.
The commit is unsigned and has no associated GitHub Actions run.

This handoff file is intended to be committed as the immediate documentation-only
child of `994a44b`. The server must record the final GitHub `main` commit/tree in
Stage 0 and verify:

```bash
git diff --name-only 994a44beff5ccdf60493252ac3aaeb7c7263c794..HEAD
```

This diff must contain only this handoff file. Any code or protocol change in
that diff means the handoff baseline has changed and execution must stop pending
review.

## 1. What was completed

The local work unit closed the two findings from the external review of
`57fa07fb7d58d99cb8311bb1bb1b12ca184b94ae`.

### 1.1 Pilot to Stage 1/G01 binding

- The Pilot runner now requires an approved Stage 1 manifest and G01 report.
- It recomputes their actual SHA-256 values.
- It verifies one common `deployment_id`.
- It verifies the Stage 1/G01 reviewed commit and tree.
- It verifies that the execution checkout commit/tree equals the frozen Pilot
  plan.
- Stage 2 and G10 reports must carry the same deployment ID and Stage 1
  manifest SHA.
- Stage 2 `request_sha256` and `trace_sha256` must correspond to files listed in
  the Stage 2 evidence manifest through `request_ref` and `trace_ref`.

### 1.2 Explicit corpus-family scope

- Pilot input now freezes `allowed_corpus_family_ids` for every question.
- `V` questions are restricted to `["ucr_varieties"]`.
- `L` questions use `["literature"]`.
- `W` and `P` questions use `[]`.
- `X` and `N` questions use the four registered local families.
- B1 passes the frozen family list to `local_hybrid_search`.
- B2 audits the actual tool arguments and returned `corpus_family_id` values.
- A local retrieval call with an unobservable or out-of-scope family fails.

### 1.3 Metrics and tests

- `context_tokens_used` is explicitly frozen as
  `sum_provider_prompt_tokens_proxy`. It is not a separately measured count of
  retrieval-evidence tokens.
- Negative tests cover wrong deployment ID, wrong Stage 1 SHA, wrong Stage 2
  request SHA, empty local allow-list, forbidden requested family, forbidden
  returned family and the B1 request argument.

### 1.4 Offline verification at the code baseline

```text
117 passed, 5 skipped in 14.36s
```

The five skips are protected-asset checks. They are not PASS.

- `python -m compileall -q` : PASS
- `python -m pytest experiments/citrus_scholar_citation60/scripts -q` : PASS
- `check_package.py` : PASS
- `audit_static_package.py` : PASS
- Current-tree credential scan: 313 files, zero findings
- Reachable-history scan: 1,141 text blobs, 1,754 objects, zero credential
  findings
- Fourteen legacy protected paths remain reachable in history and are already
  disclosed. No history rewrite and no force push were performed.

These are local/offline results. They are not independent CI or server evidence.

## 2. Workspace and server assets

| Asset | Location or state | Handoff rule |
|---|---|---|
| Local experiment worktree | `E:\codex_WORKSPACES\_work_experiment_publish` | Branch `main`; code baseline above; do not use a different checkout |
| Experiment package | `experiments/citrus_scholar_citation60/` | Only this directory may change in the reviewed experiment commits |
| Service source | `01_src/src/` | No production source change was made in this work unit |
| Production server | `/home/WZQ/CitrusQA/` | Must not be modified, restarted, cleaned or used as experiment state |
| Experiment server root | `/home/WZQ/citrus_experiment/` | Dedicated checkout, config, evidence, logs, state and artifacts |
| Experiment service | `127.0.0.1:8012` | Isolated service; production is never reused |
| Provider capture proxy | `127.0.0.1:8013` | Loopback only; required for real B2 provider binding |
| Secrets | Server-only protected environment file, mode `600`, directory mode `700`, outside Git | Never place values in Git, command lines, logs, prompts or evidence |
| Raw server evidence | `<experiment-root>/evidence/` | Write-once; only reviewed redacted derivatives may enter Git |
| Current real Stage 0 evidence | `NONE` | The current code baseline has no server G01 evidence |
| Old local Stage 0/Stage 1 samples | `01_preflight/stage0/6979aaa...`, `01_preflight/stage1/6979aaa...` | `SUPERSEDED`; local/synthetic history only, never current server evidence |
| Current model config | `05_baselines/model_config.json` | `UNCONFIGURED`, `execution_authorized=false` |
| Current Judge config | `05_baselines/judge_config.json` | `UNCONFIGURED`, `execution_authorized=false` |
| Probe plan | `01_preflight/SERVER_API_PROBE_PLAN.json` | Still frozen to `187182b`; `UNCONFIGURED`, unauthorized; do not execute |
| Gates | `01_preflight/gates.csv` | `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0` |
| Formal runs/results | `NONE` | No QA60, Citation60, Route192, Multi24 or WF run has started |

The previous API keys pasted into chat are exposed. They must not be reused.
The PI must rotate/reprovision credentials and place new values only in the
server environment file. This handoff contains no credential value.

## 3. Current blocking evidence

On 2026-11-06 the local workstation attempted:

- `211.69.141.120:28022` SSH: TCP could be reached in previous checks, but the
  SSH banner timed out.
- `http://211.69.141.120:8012/health`: timed out.

The latest read-only attempt returned:

```text
Connection timed out during banner exchange
Connection to 211.69.141.120 port 28022 timed out
```

and the experiment health request timed out after 10 seconds.

This proves only that the local workstation cannot currently execute server
Stage 0. It does not prove the service is healthy or unhealthy from the server
console.

The following states remain unchanged:

- `G01=BLOCKED`
- Stage 1: not authorized
- Stage 2: not authorized
- Pilot: `BLOCKED`
- QA60 and Citation60 formal runs: prohibited
- No model connectivity evidence
- No expert scoring evidence
- No LLM Judge evidence
- No research result

## 4. Non-negotiable execution rules

1. GitHub `w1-23/QAagent` is the only execution ledger.
2. The server must fetch and check out an explicit reviewed commit. Never use
   `git reset --hard`, `git clean`, force push or history rewriting.
3. The server must verify local HEAD/tree, GitHub `main` and the reviewed
   commit/tree. A dirty worktree, inconsistent parent or unknown tree blocks.
4. The server is the only environment for Stage 0, model probes, real runs,
   scoring, statistics, figures and paper data.
5. The local workstation may edit code/protocol and run offline tests only.
6. The production `/home/WZQ/CitrusQA/` service, database, sessions, uploads,
   artifacts, caches, logs and backups must not be modified.
7. The experiment must use independent service state on `8012`.
8. All raw evidence is write-once. Failed, cancelled, retried and partial
   attempts remain recorded.
9. Missing or malformed evidence produces `BLOCKED`. It is never guessed,
   simulated, backfilled or converted to zero.
10. Credentials never enter Git, evidence, logs, screenshots, prompts, packet
    files or command-line arguments.
11. The previously exposed API keys are not reusable.
12. Do not mark a gate PASS because code review passed. Only real evidence may
    change a gate.
13. Do not run Stage 1 against the checked-in probe plan: it is still
    `UNCONFIGURED` and bound to an obsolete commit.
14. Do not start Pilot until Stage 1, Stage 2, G10 and reset/fairness checks
    pass.
15. Do not start formal QA60/Citation60 until Pilot and independent review
    complete.
16. Keep QA60 and Citation60 denominators separate. Never pool them.
17. Human expert scoring is primary for QA60; LLM judges are an independent
    automated measurement and disagreement analysis.
18. Citation60 uses only deterministic frozen `target_chunk_id` hit. Do not add
    semantic-support reporting.
19. Every server work unit ends with evidence, a commit/push of reviewed
    derivatives, a SHA report and a pause for external GPT review.

## 5. Immediate server recovery procedure

This part is permitted before model execution. It must not create a PASS.

1. From the server console or cloud management interface, check:

```bash
sudo ss -ltnp | grep -E ':(28022|8012|8013)\b'
curl -v --max-time 5 http://127.0.0.1:8012/health
systemctl status <ssh-service>
systemctl status <experiment-service>
journalctl -u <ssh-service> --since '-30 min'
journalctl -u <experiment-service> --since '-30 min'
```

2. Determine separately:

- Is the SSH daemon accepting protocol connections?
- Is the experiment API healthy on loopback?
- Is external access blocked by firewall, security group or reverse proxy?
- Is the experiment service using a separate process, state and port?

3. Do not fix an experiment problem by editing production databases, sessions,
   uploads, caches or service state.

4. Preserve the actual commands, outputs, timestamps, host identity and
   evidence file SHA-256 values. Redact credentials and personal information.

## 6. Exact gated execution order after recovery

### Gate 0: baseline acceptance

Before real evidence generation:

1. External GPT or the designated reviewer must read the final GitHub `main`
   containing this handoff.
2. The reviewer must accept the final commit/tree or explicitly authorize
   Stage 0-only execution.
3. Record the accepted commit/tree in the Stage 0 output path and in the
   execution log.

Do not treat a verbal "continue" as a gate PASS.

### Gate 1: server checkout

Use a clean checkout under the experiment root:

```bash
EXPERIMENT_ROOT=/home/WZQ/citrus_experiment
CHECKOUT="$EXPERIMENT_ROOT/checkout"
git -C "$CHECKOUT" fetch origin main
git -C "$CHECKOUT" checkout --detach <FINAL_REVIEWED_COMMIT>
test -z "$(git -C "$CHECKOUT" status --porcelain)"
test "$(git -C "$CHECKOUT" rev-parse HEAD)" = "<FINAL_REVIEWED_COMMIT>"
test "$(git -C "$CHECKOUT" rev-parse 'HEAD^{tree}')" = "<FINAL_REVIEWED_TREE>"
```

Also verify the remote URL does not contain embedded credentials and the
checkout is not inside the production root.

### Gate 2: Stage 0 and G01

Run the repository/asset audit against the exact reviewed commit/tree:

```bash
cd "$CHECKOUT"
REVIEWED_HEAD="$(git rev-parse HEAD)"
REVIEWED_TREE="$(git rev-parse 'HEAD^{tree}')"
mkdir -p "$EXPERIMENT_ROOT/evidence/stage0/$REVIEWED_HEAD"
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/00_repository_asset_audit.py \
  --repo-root "$CHECKOUT" \
  --experiment-root "$CHECKOUT/experiments/citrus_scholar_citation60" \
  --reviewed-head "$REVIEWED_HEAD" \
  --reviewed-tree "$REVIEWED_TREE" \
  --github-repo w1-23/QAagent \
  --require-public \
  --output-dir "$EXPERIMENT_ROOT/evidence/stage0/$REVIEWED_HEAD"
```

Then complete
`01_preflight/G01_SERVER_ISOLATION_REPORT_TEMPLATE.json` from real server
observations. It must include and bind:

- exact Git commit/tree and deployment ID;
- deployed file manifest with recomputed file SHA-256 values;
- `/health` result and isolated service identity;
- production write-denial proof and credential-unreadability proof;
- five registered corpora and their actual accessibility;
- index generation, source manifest and retrieval smoke evidence;
- exactly 17 registered tools with names, groups, categories and evidence SHA;
- API, task, retrieval and tool-log destinations;
- dependency lock, service configuration and runtime snapshot;
- experiment/production root separation after path normalization;
- evidence paths contained under the approved experiment root.

All failure, missing and unverifiable fields remain explicit. Do not fill them
by inspection of source alone.

Stage 0 output is incomplete until it is committed as reviewed redacted
derivatives with a manifest and SHA report. Raw protected material remains on
the server.

**Stop point:** commit/push the Stage 0 evidence package and request review.
Do not proceed merely because the repository audit returned PASS.

### Gate 3: Stage 1

Only after G01 is actually PASS:

1. Build a new probe plan from the final reviewed commit/tree.
2. Set the G01 report SHA and the exact experiment origin allowlist.
3. Configure the Agent and Judge model IDs, Base URLs and environment-variable
   names without storing keys.
4. Execute separate minimal attempts for Agent, Judge 1, Judge 2 and Judge 3.
5. Preserve actual response model, response/request IDs, HTTP status, usage,
   cost/list-cost/allowance, timing, errors, redacted request and raw response
   with SHA.
6. Verify J1/J2/J3 are not DeepSeek and are not the Agent provider.

The checked-in plan currently remains `UNCONFIGURED`; never run it unchanged.

**Stop point:** commit/push the Stage 1 manifest and request review.

### Gate 4: Stage 2 and G10

1. Execute one real B2 task with a new session and empty history.
2. Capture provider request/response, SSE, history, citations, retrieval events,
   tool events, result files and backend logs.
3. Run:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py \
  --request "$EXPERIMENT_ROOT/evidence/raw/$RUN_ID/request.json" \
  --trace "$EXPERIMENT_ROOT/evidence/raw/$RUN_ID/trace.json" \
  --out "$EXPERIMENT_ROOT/evidence/stage2/$RUN_ID.audit.json" \
  --expected-model-config experiments/citrus_scholar_citation60/05_baselines/model_config.json \
  --stage1-manifest "$EXPERIMENT_ROOT/evidence/stage1/$REVIEWED_HEAD/PROBE_MANIFEST.json" \
  --evidence-root "$EXPERIMENT_ROOT/evidence/raw/$RUN_ID" \
  --session-registry "$EXPERIMENT_ROOT/evidence/global_session_registry.csv" \
  --expected-condition B2 \
  --required-tool-groups LOCAL \
  --require-corpus-access
```

4. Require at least one actual completed LOCAL tool from the frozen 17-tool
   registry; a failed tool alone is not sufficient.
5. Execute the six G10 categories: local, web, attachment, audit, verification
   and export. Synthetic fixture output is not G10 evidence.

**Stop point:** commit/push Stage 2/G10 reports and request review.

### Gate 5: Pilot

1. Freeze the 12 Pilot questions and 36 run plan. The source CSV is
   `03_benchmarks/pilot/pilot12_v4_4_2.csv`.
2. Verify every row has an explicit `allowed_corpus_family_ids` value.
3. Execute 12 questions x B0/B1/B2 = 36 real runs.
4. Save all raw evidence and failures.
5. Two experts independently score all 36 answers.
6. J1/J2 score all 36; J3 only arbitrates disagreements.
7. Run challenge cases for correct/incorrect refusal, fabricated citation or
   data, source mismatch, unsupported evidence, over-extrapolation, empty answer
   and execution failure.
8. Verify raw agreement and kappa/AC1 thresholds before formal qualification.

The Pilot runner requires Stage 1 manifest/G01 paths and SHA values, the Stage 2
and G10 report SHA values, the frozen reviewed commit/tree, execution
authorization and a Judge configuration that excludes DeepSeek.

**Stop point:** commit/push Pilot evidence, expert and Judge calibration and
request the second external review.

### Gate 6: formal batches

Only after the Pilot review:

1. QA60: 60 questions x B0/B1/B2 = 180 runs.
2. Citation60: 60 frozen target chunks x B0/B1/B2 = 180 runs.
3. Route192: 192 runs.
4. Multi24: 24 runs.
5. WF01-WF07: seven workflows.

Measure:

- QA60: EGTS, answerability, answer correctness, evidence use and fatal error;
- Citation60: deterministic frozen `target_chunk_id` hit only;
- Route192: executable tool-route compliance, not generic classification
  accuracy;
- Multi24 and workflows: real system capability and artifact evidence.

QA60 and Citation60 remain separate. Do not pool denominators.

## 7. Required server-side checks before any run

Run from the exact reviewed checkout:

```bash
python -m compileall -q experiments/citrus_scholar_citation60/scripts \
  experiments/citrus_scholar_citation60/05_baselines/adapters
python -m pytest experiments/citrus_scholar_citation60/scripts -q
python experiments/citrus_scholar_citation60/scripts/check_package.py
python experiments/citrus_scholar_citation60/scripts/audit_static_package.py
git diff --check
```

Expected baseline:

```text
117 passed, 5 skipped
```

The five skips are not PASS. A changed test count must be explained in the
server report.

Also run a protected-asset/credential scan and verify:

- no credential value in current files or reachable history;
- no `.env` or private key in the checkout;
- no raw participant, account, Cookie or Authorization data in evidence;
- no production path in the experiment manifest.

## 8. Evidence commit rules

The server may commit only reviewed, redacted derivatives. Each evidence
commit must include:

- work unit ID;
- final reviewed commit/tree;
- deployment ID;
- command lines with secrets removed;
- result table with PASS/BLOCKED/FAILED as actually observed;
- evidence file paths and SHA-256 manifest;
- test outputs;
- gate state changes with evidence path;
- blockers and next step.

Raw provider bodies, credentials, Cookies, account passwords, personal data and
unlicensed full text remain outside Git until release approval.

## 9. Current handoff conclusion

The code review work for the two remaining Pilot blockers is complete and
published. The next productive work is not another protocol rewrite. It is:

1. restore and diagnose SSH/8012/8013 on the server;
2. obtain external review of the final GitHub `main`;
3. run Stage 0/G01 against that exact commit/tree;
4. submit the actual server evidence for review.

No gate is promoted by this handoff.
