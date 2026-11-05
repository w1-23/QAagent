# External GPT review handoff: v4.3.3

Review state: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`
Parent commit: `d45bf93897a4305b734a9a3d2abdfc0da03eb600`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This commit responds to the fifth external review. It does not run a server or
model and does not create experimental results.

## 1. Corrected P0 interpretation

The fifth review established that the frozen question set is a test input and
may be available for audit. The actual hidden boundary is narrower:

- no target chunk ID or target text injected into Agent input;
- no answer key, scoring mapping, expert score, or judge score in Agent input;
- no other-condition answer or shared session state;
- no direct Agent access to the protected experiment, scoring, blind-map, or
  raw-run directories.

Normal retrieval of an original source through the platform is permitted.
`ASSET_INTEGRITY_REPORT.json` therefore distinguishes source redistribution
from Agent input isolation instead of treating them as one phenomenon.

## 2. Repository visibility

`w1-23/QAagent` is now Private. The authenticated verification recorded
`private=true`, `admin=true`, `default_branch=main`, a readable `main` commit,
`AUDIT_INDEX.md` blob, recursive tree, and commit history. The exact evidence is
in `01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05.json`.

Historical objects remain in Git history. The repository is restricted, but no
history rewrite or deletion occurred. Those actions remain owner decisions.

## 3. Implemented audit code

`scripts/A00_execution_readiness/00_repository_asset_audit.py` emits:

- `REPOSITORY_AUDIT.json`;
- `ASSET_INTEGRITY_REPORT.json`;
- `DATA_LINEAGE_MANIFEST.json`;
- a write-once `STAGE0_AUDIT_MANIFEST.json`.

It checks local Git HEAD/tree/branch/worktree, optional GitHub private and
readability state, expected HEAD, 16 declared L1 byte/SHA values, public-tree
protected-file absence, QA60 counts/strata, 180 QA run slots, 120 QA validity
slots, citation60 counts when protected inputs are supplied, and role/flow
lineage. It never treats `L1_ROOT_NOT_PROVIDED` as asset verification.

`scripts/A00_execution_readiness/10_server_api_probe.py` implements a
declarative, fail-closed runner for:

- server resources and required Python distribution versions;
- environment presence plus `EnvironmentFile`/parent-directory permission
  checks without reading file content;
- server health;
- registration;
- login;
- OpenCode Go provider calls;
- Agent provider calls;
- admin configuration;
- bounded rate-limit/timeout observation with one retained record per request;
- actual model identity.

Execution requires all three signals:

```text
status == READY
execution_authorized == true
review_status in {APPROVED, APPROVED_FOR_EXECUTION}
```

The checked-in plan is intentionally `UNCONFIGURED`; without `--execute` the
script only validates its schema and reports the missing authorization. No
credential value is accepted as a command-line argument.

`scripts/A00_execution_readiness/20_audit_agent_input_boundary.py` enforces:

- one frozen question;
- required `session_id`, `request_id`, and `run_id`;
- `condition` in B0/B1/B2;
- `session_mode=NEW`;
- empty conversation history;
- exact frozen prompt equality when a catalog is supplied;
- frozen model generation settings;
- no forbidden hidden-reference keys or target text;
- no filesystem access under the Git checkout or protected `L1` root;
- only declared tool groups;
- exact raw request and trace SHA binding;
- exact request/session/run/question/condition identity across the trace;
- mandatory catalog, hidden-reference, L1-root, repository-root, condition,
  model-config, and tool-group arguments.

## 4. Offline tests

The new tests include:

- L1 assets are not reported verified when no protected root is supplied;
- all 16 missing L1 assets fail verification;
- lineage marks scoring assets as non-Agent-visible;
- the checked-in probe plan validates but is not execution-authorized;
- passwords and Authorization values are absent from probe evidence;
- EnvironmentFile probes do not read file content;
- rate-limit attempts and `Retry-After` values are retained one file per request;
- provider token counts are retained while credential-like values are redacted;
- missing provider/rate-limit body environment values fail closed;
- model identity mismatch blocks a provider probe;
- normal platform retrieval containing the target chunk ID is allowed;
- target locator/text injection is blocked;
- reused sessions, multiple questions, mismatched request/session SHA binding,
  extra model settings, and hidden scoring-file access are blocked;
- all audit outputs are write-once.

The local static result is:

```text
compileall: PASS
pytest: 67 passed, 5 skipped
package check: PASS
static package audit: PASS; 265 files secret-scanned
git diff --check: PASS
credential-prefix scan: PASS
```

The five skips require protected citation60 inputs. No L1 root was supplied, so
Stage 0 declares 16 protected assets and verifies zero. These are static tests,
not server or experiment evidence.

## 5. Requested sixth review

Please review the latest `main` commit against the parent above, focusing on:

1. correctness of the corrected leakage definition;
2. private-repository verification and historical-object disclosure;
3. Stage 0 output completeness and fail-closed behavior;
4. probe-plan realism, credential redaction, rate-limit safety, and identity
   checks;
5. one-question/new-session and tool-path enforcement;
6. consistency of all active protocol documents;
7. whether any remaining issue blocks Stage 1 server probing.

Approval of this offline code does not automatically authorize Pilot or formal
experiments. Stage 1 results require a separate reviewed server work unit.
