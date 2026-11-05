# External GPT review handoff: v4.4 execution-evidence hardening

Protocol version: `4.4.0`
Review parent: `8491db28ebe69876d4345b459f07909e67670d3f`
Parent tree: `1da32136eac726ac40f5af523e8caaaabb4efc86`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_HARDENING`
Execution authorization in this work unit: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`
Pilot: `NOT_STARTED`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`

This work unit responds only to the external review of commit `8491db2`.
The v4.4.0 research design passed that review and is not reopened here.
Questions remain normal Agent input, full registered-corpus retrieval remains
allowed and logged, and `CITRUS_EXPERIMENT_L1_ROOT` remains unnecessary for
real Agent testing.

## 1. P0 corrections

### Stage 0 reviewed repository identity

- `00_repository_asset_audit.py` now requires explicit
  `--reviewed-head` and `--reviewed-tree`.
- The script verifies local `HEAD`, local tree, remote `main`, remote tree,
  clean worktree and `git fsck`.
- `git fsck` failure is a total-audit blocker.
- An attached `main` branch or detached `HEAD` is accepted only when both
  reviewed SHA values match; other attached branches are blocked.
- The Stage 0 command no longer derives the expected commit from `HEAD`.

### Stage 0 server isolation report

- Added `01_preflight/G01_SERVER_ISOLATION_REPORT_TEMPLATE.json`.
- It covers deployment SHA/tree and file manifest, service/database/session/
  upload/artifact separation, production write denial and credential
  unreadability, five corpora, index generation/source hashes, all 17 tools,
  log destinations and the Python/dependency/system/runtime snapshot.
- Stage 1 loads the completed report, verifies its approved SHA and requires
  `status=PASS` before creating any account or network request.

### Stage 1 API probe

- Provider PASS now requires accepted HTTP status, non-empty generated
  content, response ID, actual response model, frozen-allowed model identity,
  structurally valid usage when returned, and JSON object/key checks for
  Judge calls.
- The Agent call's `CITRUS_AGENT_MODEL_ID` must equal the frozen model.
- `/api/v2/config` is checked against the frozen Provider and model.
- Stage 1 output records the application configuration and Agent/Judge
  identity for later Stage 2 cross-binding.
- The experiment origin allowlist and approved Stage 0 report SHA are hard
  execution preconditions. App probes must use
  `CITRUS_EXPERIMENT_BASE_URL`.
- An EnvironmentFile is accepted only when its resolved path is outside the
  Git checkout in addition to mode/ownership checks.
- Rate-limit stress testing is explicitly `DEFERRED`, not passed. Normal
  throttling metadata may be retained; retry logic is tested offline; live
  throttling requires a disposable endpoint and approved budget.
- `/api/v2/config` does not expose tool names, so the 17-tool registration
  assertion is bound to the approved G01 report and its evidence SHA rather
  than inferred from model fields.

### Stage 2 execution-evidence audit

- Removed the hardcoded `cross_condition_contamination=false`.
- Added a global session-registry check with `NOT_ASSESSED`,
  `VERIFIED_CLEAN` and `BLOCKED` outcomes.
- Raw Agent response, raw SSE, retrieval, tool result/package, backend log and
  artifact references now resolve to actual files under `--evidence-root`;
  their bytes must match the recorded SHA-256.
- Model identity now requires the frozen Provider, requested model and an
  actual model from the frozen allowlist, cross-bound to the saved Stage 1
  manifest.
- Control-material paths are checked in tool arguments, inline tool results
  and tool-result files. Normal corpus chunk text and locators remain allowed.
- Stage 2 acceptance requires one real `completed` B2 run with normal
  retrieval/tool execution. Failed and cancelled runs remain retained and
  auditable but cannot establish Stage 2 success.
- Added negative tests for forged evidence, invalid SHA, wrong model/provider,
  duplicate session, control material in a tool result, failed run presented
  as success, `done.answer` mismatch and a missing registry.

## 2. P1 corrections

- Active expert-scoring, rubric, analysis and unified-judge documents now
  reference the v4.4.0 execution decision and this hardening handoff rather
  than naming v4.3.3 as the current decision.
- The paper-writing plan is now version 4.4.0.
- Rate-limit deferral is consistent between the probe plan and protocol.
- The G01 isolation/runtime report schema and global session-registry template
  are now checked-in review artifacts.
- Public Git-history source-text redistribution remains `PENDING` for
  PI/repository-owner disposition. No history rewrite or force push was
  performed.

## 3. Verification

The complete offline verification was run on 2026-11-05. This section does not
assert GitHub CI or server execution.

Required checks:

```text
python -m compileall -q experiments/citrus_scholar_citation60/scripts
python -m pytest -q experiments/citrus_scholar_citation60/scripts
python experiments/citrus_scholar_citation60/scripts/check_package.py
python experiments/citrus_scholar_citation60/scripts/audit_static_package.py
git diff --check
credential-pattern scan
```

Observed results:

```text
compileall: PASS
pytest: 90 passed, 5 skipped
check_package.py: PASS
audit_static_package.py: PASS
git diff --check: PASS
credential-pattern scan: PASS
```

Skipped tests for protected assets remain skipped and are not reported as
passed.

## 4. Gate and stop state

No account was registered, no `.env` or secret was created, no server was
started or queried, no Bailian/OpenCode Go endpoint was called, no question was
run, no Pilot/formal data was generated, and no gate was promoted.

This work unit stops after ordinary push to GitHub `main`. The next action is
external GPT review of the new commit/tree. Stage 0-2 execution remains
unauthorized until that review explicitly approves the hardened evidence
chain.
