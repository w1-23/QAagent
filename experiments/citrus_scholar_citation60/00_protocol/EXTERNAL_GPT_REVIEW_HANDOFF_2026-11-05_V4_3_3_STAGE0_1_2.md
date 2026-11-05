# External GPT review handoff: public repository and Stage 0-2 evidence

Protocol version: `4.3.3`
Review state: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Evidence source commit: `6979aaaab9536f3eaf672c02763cafef232c8c2d`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This is the sixth-round execution handoff. It supersedes review instructions
that still describe the repository as Private, while preserving the earlier
handoff as historical evidence.

Stage 0 inspected the clean publication Git worktree. Stage 1's transport probe
was a credential-free client reachability check; no database, model, scoring,
Pilot, or experiment command was executed locally.

## 1. What was executed

| Stage | Result | Evidence |
|---|---|---|
| Public repository switch/readback | `PASS` | `01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json` |
| Git history credentials and protected paths | credentials `PASS`; legacy L1 exposure disclosed | `GIT_HISTORY_SECRET_AND_PROTECTED_PATH_AUDIT.json` |
| Stage 0 repository/Git/tree audit | `PASS` | `01_preflight/stage0/6979aaaab9536f3eaf672c02763cafef232c8c2d/REPOSITORY_AUDIT.json` |
| Stage 0 asset/lineage audit | `BLOCKED` (L1 root unavailable) | `ASSET_INTEGRITY_REPORT.json`; `DATA_LINEAGE_MANIFEST.json` |
| Stage 1 plan validation | `PASS` (not execution authorization) | `01_preflight/stage1/6979aaaab9536f3eaf672c02763cafef232c8c2d/PLAN_VALIDATION.json` |
| Stage 1 credential-free transport | `BLOCKED` | `TRANSPORT_PREFLIGHT.json` |
| Stage 1 API/model probes | `NOT_RUN` | no credential or provider request was made |
| Stage 2 input-boundary audit | `NOT_RUN` | no real request/trace pair exists |

## 2. Stage 0 facts

Local and remote `main` both resolved to:

```text
6979aaaab9536f3eaf672c02763cafef232c8c2d
```

The local tree was:

```text
571852370c0fb23960f41b7d8aec085bd9a7f441
```

The repository audit reported a clean worktree, branch `main`, complete remote
recursive tree, readable history and `AUDIT_INDEX.md`, matching local/remote
commit, and `git fsck --full --no-reflogs` exit code 0.

The follow-up history audit scanned 915 text blobs, found no credential
pattern, found no protected citation60 path in the current tree, and found 14
protected L1 paths reachable in legacy history. The PI authorized Public
visibility without history rewrite. This is a known source-redistribution
exposure that requires explicit review; it is not a credential leak and is not
rewritten or hidden.

Asset checks found:

| Check | Result |
|---|---:|
| QA60 unique questions | 60 |
| Strata `L/V/W/X/P/N` | 12/10/10/12/8/8 |
| Validity review slots | 120 unique |
| QA core run rows | 180 |
| B0/B1/B2 rows | 60/60/60 |
| Protected citation60 files in current tree | 0 |
| L1 assets declared | 16 |
| L1 assets verified | 0 |
| Semantic-support outputs | 0 |

Stage 0 asset acceptance is `BLOCKED` because the protected `L1` root was not
available. The missing root is not replaced by a guess.

## 3. Stage 1 facts

The credential-free transport evidence shows:

- TCP connect: open for both configured endpoints;
- SSH banner: timeout after 8 seconds;
- experiment `/health`: timeout after 8 seconds, zero response bytes;
- credentials used: false;
- Authorization Headers used: false;
- raw target recorded in Git: false.

The probe plan validates structurally with 10 probes. The checked-in plan still
correctly refuses `--execute` because it lacks all three authorization signals.
The attempted refusal and exit codes are retained beside the transport record.

This is a real negative result, not a simulated success. No model identity,
usage, cost, or provider response was produced.

## 4. Stage 2 facts

The strict auditor requires:

```text
request
trace
question catalog
hidden reference
L1 root
repository root
condition
frozen model config
allowed tool groups
write-once output
```

No real Agent request or trace exists. The only committed run records are
`SIMULATED_DRY_RUN` interface fixtures. Stage 2 was therefore not invoked, and
the synthetic fixtures were not promoted into evidence.

## 5. Review request

Please inspect the actual repository and evidence, focusing on:

1. whether the public-history credential/L1 scan is sufficient and accurately
   scoped, including the 14 legacy L1 paths now disclosed;
2. whether `11_transport_preflight.py` preserves the no-credential boundary and
   records the transport failure without exposing server access details;
3. whether the public/private visibility audit code fails closed in both modes;
4. whether Stage 0 correctly distinguishes repository `PASS` from overall
   `BLOCKED` due to unavailable `L1`;
5. whether Stage 1 refusal and Stage 2 non-execution are evidence-based rather
   than hidden as skipped success;
6. which party must repair the SSH/HTTP transport path and when a fresh rotated
   provider credential may be provisioned.

Approval of this evidence does not authorize Pilot or formal experiments.
