# Server-only experiment execution and data-classification protocol

Version: `4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`
Current decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_3.md`
Execution evidence: `00_protocol/EXECUTION_ADDENDUM_2026-11-05_STAGE0_1_2.md`
Historical decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_1.md`
Experiment root: protected server path selected by the operator
Planned bind address: `127.0.0.1:8012`
Production root: operator-defined and outside experiment write scope

No command in this document is authorized by this plan. The sixth-round
credential-free transport probe opened TCP but received neither an SSH banner
nor an experiment `/health` response within eight seconds. No credential or
model request was made. G01 remains blocked until a fresh identity, permission,
and isolation check succeeds.

## 1. Execution boundary

All executable experiment work runs on the isolated server:

- package and source checks;
- database integrity checks;
- model and judge connectivity probes;
- Pilot and formal runs;
- SSE, artifact, history, and download verification;
- expert packet generation;
- machine judging and aggregation;
- statistical analysis, figures, and manuscript data generation.

The local workstation is limited to source/protocol editing, static review,
and Git operations. It must not run experiment models, databases, Pilot,
formal runs, judging, scoring, or analysis.

Do not start, stop, update, or write to the production service. Do not use
production accounts, sessions, uploads, databases, logs, caches, or artifact
stores for the experiment.

## 2. Mandatory isolation controls

Isolation must be enforced by system controls, not only operator discipline:

1. a dedicated OS service user for the experiment;
2. a separate service unit and process name;
3. separate environment file, database, session store, uploads, artifact
   store, logs, and model cache;
4. loopback-only bind on `127.0.0.1:8012`, exposed only through an approved
   authenticated tunnel/proxy;
5. filesystem ownership/ACLs preventing the experiment user from writing the
   production root;
6. read-only mount or equivalent permission for immutable inputs and any
   shared model cache;
7. explicit verification that production secrets and credential files are
   unreadable to the experiment user;
8. resource limits that prevent experiment runs from exhausting production.

Capture commands, exit status, ownership, permissions, mount options, and
hashes as G01 evidence. A documentation-only statement is not evidence.

## 3. Directory layout

```text
<experiment-root>/
  checkout/                         # explicit reviewed commit only
  02_config/                        # non-secret environment/config snapshots
  agent/state/                      # isolated database and sessions
  agent/data/                       # frozen authorized inputs and index snapshots
  agent/workspace/                  # experiment uploads and generated artifacts
  agent/logs/                       # experiment service logs
  agent/.hf_cache/                  # isolated or verified read-only cache
  protected/L1/                     # CITRUS_EXPERIMENT_L1_ROOT
    06_runs/raw/                    # authentic raw runs and failed attempts
    judge_packets/                  # condition-blinded machine packets
    judge_requests/                 # redacted request bodies and attempt hashes
    judge_raw/                      # safe raw responses and judgments
    judge_blinding/                 # blind map and reviewer-specific order files
  experiments/citrus_scholar_citation60/
    ...                             # tracked protocol, code, redacted/L2 evidence
  secrets/                          # mode 700 directory, mode 600 files
```

The protected `L1` store remains outside Git. Public derivatives in the Git
checkout must follow `PUBLICATION_SCOPE.md` and record source SHA, locator,
access class, redaction log, and derivative SHA.

All protected assets declared in
`12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json` must be stored below
`CITRUS_EXPERIMENT_L1_ROOT` at their declared `protected_relative_path`.
Examples:

```text
$CITRUS_EXPERIMENT_L1_ROOT/03_benchmarks/citation60/chunks_FROZEN.jsonl
$CITRUS_EXPERIMENT_L1_ROOT/03_benchmarks/citation60/question_authoring_packet.jsonl
```

The repository-safe controlled-asset manifest records their expected bytes and
SHA-256 without source text. The server verification must fail if any protected
file is missing or differs.

## 4. Credentials

Provision only rotated credentials after external review and the exposed-key
issue is resolved. Store them outside the Git checkout in a systemd
`EnvironmentFile` or protected credential manager with directory mode `700`
and file mode `600`.

The Agent uses only:

```text
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
```

The judges use only:

```text
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
```

Do not pass secrets through command-line arguments, shell history, source
files, prompts, packets, screenshots, request archives, logs, notebook output,
or GitHub. Archived requests omit Authorization headers and record only the
endpoint host and `auth_present=true`.

Credentials may exist only in a root-owned or experiment-user-owned systemd
`EnvironmentFile`/equivalent protected environment mechanism with directory
mode `700` and file mode `600`. The experiment scripts read named environment
variables and never accept key values as arguments. No credential value is
written into a script, checker, config snapshot, fixture, test, or `.env` file
inside the Git checkout.

At study close, delete the server credential files, record the audited
deletion time, and run a fresh secret scan. Retain experiment evidence.

## 5. GitHub synchronization

1. The local editor commits reviewed protocol/code changes to GitHub.
2. The server fetches `w1-23/QAagent` and checks out an explicit commit; record
   the commit and tree SHA before every server work unit.
3. Stop if the checkout is dirty, the remote parent changed, or the expected
   commit cannot be proven. Never use `git reset --hard` or force push.
4. Run checks and write outputs only on the server.
5. For each completed work unit, validate evidence and hashes, run the release
   checks, commit/push that unit, report SHA and results, then pause for GPT
   review.
6. If the server lacks a scoped safe GitHub credential, stop and configure it
   securely. Do not copy result directories to the local workstation as a
   fallback.

## 6. Work-unit sequence

### WU-00: sixth external protocol review

This v4.3.3 review unit ends after one documentation and candidate-code commit.
No server, model, database, Pilot, scoring, or experiment command runs before
GPT review.

### WU-01: G01 connectivity and isolation evidence

Recheck SSH, server identity, repository parent, service user, filesystem
permissions, mount options, port availability, database separation, environment
separation, production read-only enforcement, and GitHub access. Record exact
commands, output, UTC, and SHA. Any failure blocks G01 and all dependent work.

### WU-02: environment and source lock

Deploy the approved commit to the isolated instance. Record commit/tree,
deployed file SHA, `/health`, service config, actual model identity/version,
index generation, 17-tool registry, corpus manifest, hardware/runtime, and
source/license status.

### WU-03: provider probes

After credential rotation and terms confirmation, probe the Bailian Agent and
each OpenCode Go judge separately. Preserve redacted request metadata, complete
safe response metadata, actual returned model, HTTP status, elapsed time, usage,
cost/list-cost/allowance when returned, and SHA for every attempt. Also verify
the `EnvironmentFile` and parent directory have modes `600`/`700` without
reading credential values. A separately reviewed, bounded, disposable endpoint
may measure rate-limit/timeout behavior; no production endpoint may be used.
Connectivity alone changes status to `PILOT_READY`, never `READY`.

### WU-04: offline schemas and packet tests

The v4.3.3 readiness auditors, packet builders, judge dispatch, adjudication, aggregation, human
scoring, agreement, and strict analysis implementations are candidate code in
this review unit. Run only synthetic schema/leakage tests before real packets
exist. Do not put synthetic answers into formal directories.

### WU-05: validity and fairness gates

Lock 120/120 two-reviewer validity decisions and adjudications. Verify B1/B2
model, source, attachment/parser, permission, retrieval, and tool-budget
fairness. Run package, schema, and secret checks.

### WU-06: independent Pilot

Run 12 non-formal questions under B0/B1/B2 for 36 answers. Both experts and
J1/J2 score all 36; J3 handles four-field disagreements. Run challenge cases,
compute agreement, and report every failure. Pilot can pass only under the
rules in `07_scoring/rubric.md`.

### WU-07: formal protocol lock

After Pilot review and threshold passage, freeze actual models, prompts,
packet builder, scoring schemas, run plan, retrieval settings, analysis code,
blind maps, and source/index manifests. Record old/new SHA and a change-log row
for every post-review change.

### WU-08 onward: formal experiments

Run core QA60, citation60, Route192, Composite24, optional repeat experiment,
and WF01-WF07 as separate work units. Preserve all attempts and evidence.
After scoring and adjudication, run `strict-human`, `strict-judge`,
`strict-citation`, and `strict-all` independently. A failure of one independent
module is reported, not hidden by another.

## 7. Public-repository and restricted-data release

The GitHub repository is Public by PI decision for external review:

- `L0` credentials and authentication data are never committed;
- `L1` full text/restricted excerpts/private SOPs/identity maps/raw sensitive
  logs stay in protected server storage;
- `L2` redacted runs, permitted evidence locators, SHA values, blinded scores,
  statistics, and manuscript materials are uploaded only after the release
  review in `PUBLICATION_SCOPE.md`.

Every reviewed run record is checked for account IDs, user identifiers,
attachment content, signed URLs, Authorization headers, personal information,
and redistribution rights. Raw evidence is retained even when it cannot be
publicly released.

Public visibility does not convert `L1` source text into distributable
material. The verified public access record is
`01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json`. The
earlier Private-state record remains as historical evidence.

## 8. Gate-state rule

A written plan, template, successful script, model probe, or command attempt
does not make a gate `PASS`. Record `PENDING`, `BLOCKED`, `PASS`, or `NA` with
the exact evidence path, reviewer, UTC, and acceptance justification. Never
advance G01-G15 merely because the next plan stage is ready.
