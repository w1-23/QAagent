# Execution addendum: public repository and Stage 0-2 evidence

Decision version: `v4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Evidence source commit: `6979aaaab9536f3eaf672c02763cafef232c8c2d`
Execution authorization: `NONE` for Pilot and formal experiments
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This addendum records the PI-authorized sixth-round transition from repository
access preparation to real Stage 0-2 execution. It does not change the locked
research design, endpoint, denominators, models, scoring rules, or gate
acceptance criteria.

## 1. Repository visibility

The PI explicitly approved restoring `w1-23/QAagent` to Public so the external
reviewer can read the current evidence without relying on chat summaries.

Before the visibility change:

- the current tree was scanned for credential patterns;
- all 910 text blobs reachable from all local Git references were scanned for
  Alibaba Cloud keys, OpenCode Go keys, Bearer Header values, and assigned API
  keys;
- no credential-pattern match was found;
- no history rewrite, force push, or protected-material deletion was performed.

A separate protected-path audit at `6979aaa` scanned 915 text blobs and found
no credentials. The current tree contains zero protected citation60 paths, but
legacy history still reaches 14 protected L1 paths. The PI authorized Public
visibility without a history rewrite. This known exposure is disclosed here and
must be reviewed explicitly; it is not described as a clean history.

After the change, unauthenticated GitHub API, raw-file, and Git-ref reads
succeeded. Evidence is in
`01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json`.
The earlier Private-repository evidence remains unchanged as historical
evidence.

Public visibility is an audit-access decision. It does not authorize
redistribution of protected `L1` material and does not make any future run a
public result.

## 2. Stage 0 result

The source commit and remote tree were verified:

```text
commit 6979aaaab9536f3eaf672c02763cafef232c8c2d
tree   571852370c0fb23960f41b7d8aec085bd9a7f441
```

The repository audit passed. The public tree contained 60 QA60 questions with
the locked six-stratum distribution, 120 unique validity-review slots, 180 QA
run rows with 60 rows for each of B0/B1/B2, and no protected citation60 file in
the current repository tree.

Stage 0 remains `BLOCKED` overall because `CITRUS_EXPERIMENT_L1_ROOT` was not
available. The 16 declared protected assets have zero verified bytes/SHAs; the
script explicitly reports `NOT_RUN_L1_ROOT_NOT_PROVIDED`. No missing protected
asset was treated as a pass.

## 3. Stage 1 result

Credential-free transport checks found that TCP connections could be opened to
the configured SSH endpoint and experiment HTTP endpoint, but both the SSH
banner and the `/health` response timed out. The endpoint is represented by
SHA-256 in committed evidence rather than by its raw host and port.

The declarative Stage 1 probe plan passed schema validation but remained
`UNCONFIGURED`, `execution_authorized=false`, and
`review_status=AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`. An attempted `--execute`
was refused before any network or model request. No credential was read, no
Authorization Header was created, and no Agent or Judge API call occurred.

Stage 1 is `BLOCKED` on transport reachability. Fresh rotated credentials are
still required after connectivity is restored. The credentials previously
pasted in chat must not be reused.

## 4. Stage 2 result

Stage 2 was not executed because no real Agent request/trace pair exists. The
repository contains only `06_runs/simulated_dry_run/`, which is an explicitly
labelled interface fixture and is never admissible Stage 2 evidence.

The strict boundary auditor remains uninvoked. Its mandatory inputs include the
real request, exact matching trace, frozen question catalog, hidden reference,
protected `L1` root, frozen model configuration, tool-group allowlist, and
write-once output path.

## 5. Gate and stop state

Stage 0 partial evidence does not pass G01. The server identity, isolation,
deployment SHA, service health, credentials, model identity, and account/access
checks were not observed. G02-G15 remain pending.

This work unit stops before Pilot, formal runs, scoring, analysis, figures, or
paper results. The next action is external review, followed by repair of the
server transport path and Stage 1 execution under a separately reviewed plan.
