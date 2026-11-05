# External GPT review handoff: Stage 0 gate closure

Protocol version: `4.4.0`
Hardening revision: `4.4.1`
Reviewed parent commit: `2d8be31760bfef476cc5df95af42524b6ffbdf13`
Reviewed parent tree: `59e20190d32b81f8ac55c33d34e6d117cc76a21f`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_1`
Execution authorization in this code-review unit: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`
Pilot: `NOT_STARTED`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`

This patch implements the four non-blocking findings from the external review
of `2d8be31`. It does not reopen the accepted research design and does not
promote any gate.

## 1. Repository remote credential protection

- Every `git remote -v` URL is redacted before report construction.
- Embedded user information, passwords, tokens, and sensitive query
  parameters are replaced with `[REDACTED]`.
- A remote containing embedded credentials adds the blocker
  `git_remote_contains_embedded_credentials`; the audit cannot report PASS.
- The report records only sanitized remote lines, whether credentials were
  detected, and the affected remote names.

## 2. G01 evidence-root and deployment binding

- The report and every referenced evidence file must resolve inside the
  normalized experiment root.
- Experiment and production roots are resolved before comparison. Equal,
  nested, or aliased roots fail the isolation gate.
- The deployment file manifest must be a JSON object with:
  - `git_commit` equal to the report deployment SHA;
  - `git_tree` equal to the report deployment tree;
  - non-empty `files`;
  - unique paths and a SHA-256 for every file entry.
- Tool registration evidence must parse and list the same 17 names as the
  report.
- Five corpus access records must contain their corpus ID, and each source
  manifest must contain exactly the declared number of sources.
- Index evidence must parse and match the declared generation with non-empty
  source versions.
- Evidence files, including logs, must be non-empty and content-SHA-bound.

## 3. Stage 2 tool registry binding

- The Stage 1 manifest retains the G01 tool-registration SHA and all 17 tool
  names in `execution_guard`.
- Stage 2 rejects missing, empty, duplicate, malformed, or unbound registry
  entries.
- Every trace tool name must belong to the frozen registry.
- B2 needs at least one `completed` `LOCAL` event whose tool name belongs to
  that registry. Failed, unknown, or self-reported tools cannot satisfy B2.

## 4. Global session registry

- Every historical row requires non-empty `run_id`, `request_id`, and
  `session_id`.
- All three identifiers are checked for global uniqueness.
- The registry file SHA-256 is recorded in the registry audit result and the
  Stage 2 report.
- A missing or invalid registry still blocks Stage 2 acceptance.

## 5. Review baseline

The externally approved Stage 0 read-only baseline remains `2d8be31`. This
patch only closes findings that were allowed to be addressed before Stage 1
and Stage 2. Server Stage 0 evidence must bind the exact checked-out deployment
commit and tree. A code review PASS is not a G01 PASS.

## 6. Local verification

- `python -m compileall -q experiments/citrus_scholar_citation60/scripts`: PASS.
- `python -m pytest -q experiments/citrus_scholar_citation60/scripts`:
  `102 passed, 5 skipped in 10.49s`. The five skips are the preserved
  protected-asset checks and are not passes.
- `check_package.py`: PASS; 60 QA questions, 180 QA rows, 192 route cases,
  24 composite cases, seven workflows, and the simulated schema were verified.
- `audit_static_package.py`: PASS; 297 files were credential-scanned.
- Reachable-history audit: 1,045 text blobs and 1,590 reachable objects,
  zero credential findings, zero protected paths in the current tree, and the
  previously disclosed 14 legacy protected paths in history. No history
  rewrite or force push was performed.
- `git diff --check`: PASS.

These are local engineering checks, not independent CI or server evidence.

## 7. Server Stage 0 transport

The current review unit rechecked transport without reading credentials:

- TCP 28022: open;
- TCP 8012: open;
- SSH banner exchange: timed out;
- `http://211.69.141.120:8012/health`: timed out after 12 seconds with zero
  response bytes.

No server checkout, Stage 0 evidence run, account registration, provider call,
Pilot, formal run, production modification, session deletion, database change,
or credential read occurred. `G01` therefore remains `BLOCKED`.

No production source, credential, server session, database, upload, cache,
backup, model output, Pilot result, or formal result was modified.
