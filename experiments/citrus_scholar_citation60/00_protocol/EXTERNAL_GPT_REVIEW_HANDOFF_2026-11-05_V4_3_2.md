# External GPT review handoff: v4.3.2

Review state: `PARTIALLY_SUPERSEDED_BY_V4_3_3_HISTORICAL_RECORD`
Execution authorization: `NONE`
Parent commit: `0ebdf0fddb0c12518bfdd9d638161462e388b953`
Implementation commit: the `main` commit titled
`fix: close fourth review release and execution gaps`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`

This work unit responds to the fourth external review. It does not authorize
the Agent, any Judge, a server action, Pilot, formal experiment, human scoring,
machine scoring, or manuscript result.

## 1. Release and security corrections

- removed every unreviewed citation60 benchmark/run-plan asset from the latest
  public tree; retained 16 protected assets outside Git and declared their
  bytes/SHA values in `L1_CONTROLLED_ASSET_MANIFEST.json`;
- retained only a public-safe citation60 notice in the repository;
- reduced the public validity packet to QA60 only: 120 review rows for 60
  questions and 60 adjudication rows;
- changed all execution entry points to require `status=READY`,
  `execution_authorized=true`, and an approved review status;
- limited retries to content-free transport failures and defined HTTP service
  errors, with no retry for malformed JSON/schema or model-identity failure;
- forced judge aggregates and human materials into protected `L1` storage and
  rejected public-tree output or overwrite;
- expanded secret scanning to every file, including `.env`, `.env.*`, keys,
  PEM private keys, and authorization headers.

## 2. Judge and blinding corrections

- actual response `model` must match the role allowlist;
- missing or mismatched identity blocks strict aggregation;
- Judge 3 accepts exactly the five frozen top-level fields and no full-answer
  replacement;
- Judge 3 evidence locators are retained in the aggregate;
- formal blind packet generation requires 180 runs, exact B0/B1/B2 sets,
  identical per-question prompts, identical canonical frozen evidence, and
  Pilot support through an explicit 36-run plan;
- raw-run SHA values stay only in the protected blind map;
- blind packets, mappings, reviewer orders, aggregate tables, validation
  reports, scores, and corrections must remain outside the public Git tree.

## 3. Human scoring and statistics

Implemented candidate scripts for:

- condition-blinded expert directories and XLSX/CSV score sheets;
- immutable primary-score validation;
- append-only correction validation;
- third-expert field adjudication;
- deterministic human consensus and agreement;
- strict analysis with protected inputs and outputs.

The confirmatory endpoint is now explicitly `egts_system_outcome` over all 60
planned slots per condition. `human_consensus_egts` is a separate semantic
endpoint limited to authentically scorable answers, with coverage and
infrastructure-failure counts reported. Expert agreement excludes
infrastructure failures and NA pairs from binary agreement where specified.

## 4. What was not done

- no SSH, server, database, service, or environment command;
- no Agent or Judge API call;
- no credential creation, loading, rotation, or deletion;
- no real blind packet, expert packet, Pilot run, formal run, score, analysis,
  figure, or manuscript result;
- no gate was promoted to `PASS`;
- no force push, history rewrite, or repository-visibility change.

## 5. Remaining blockers

| Blocker | State |
|---|---|
| Historical Git objects contain prior citation60 content | `P0_OPEN_PI_DECISION` |
| citation60 G12 source/licence review | `PENDING` |
| Server SSH, isolation, and G01 evidence | `BLOCKED` |
| Actual Agent and Judge model identities | `UNCONFIGURED` |
| Validity, Pilot, human scoring, and LLM calibration | `NOT_RUN` |
| Final statistical/paper outputs | `NO_RESULTS` |

The PI/repository owner must decide whether to restrict repository access
and/or request GitHub history cleanup. This package contains no authority to
rewrite history.

## 6. Requested fifth review

Please review the latest `main` commit against the parent above, focusing on:

1. public-tree removal and protected-asset SHA/byte coverage;
2. authorization, retry, and model-identity fail-closed behavior;
3. `L1` storage boundaries and secret scanning;
4. blind-packet and reviewer-order integrity;
5. human correction/adjudication/consensus/agreement chain;
6. system-versus-semantic endpoint separation;
7. consistency of active protocol, README, prompts, templates, and code;
8. any remaining issue that must block Pilot.

Do not approve Pilot merely because offline tests pass. The next executable
work remains blocked until the external review and server-only G01 checks are
complete.

## 7. Pre-push validation

The following local, offline checks were run before the implementation commit:

```text
python -m compileall -q experiments/citrus_scholar_citation60/scripts
PASS

python -m pytest -q experiments/citrus_scholar_citation60/scripts
53 passed in 2.99s

python experiments/citrus_scholar_citation60/scripts/check_package.py \
  --l1-root "$CITRUS_EXPERIMENT_L1_ROOT"
PASS; 16/16 declared L1 assets verified; citation60 60 questions / 180 runs

python experiments/citrus_scholar_citation60/scripts/audit_static_package.py
PASS; 256 files secret-scanned; release and gate boundaries clean

git diff --check
PASS
```

These are offline package checks only. They do not establish server access,
provider connectivity, model identity in a live response, Pilot readiness, or
any G01-G15 result.
