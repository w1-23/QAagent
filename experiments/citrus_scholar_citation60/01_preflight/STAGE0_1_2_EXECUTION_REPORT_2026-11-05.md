# Stage 0-2 execution report

Protocol version: `4.3.3`
Evidence source commit: `6979aaaab9536f3eaf672c02763cafef232c8c2d`
Execution state: Stage 0 partial; Stage 1 blocked; Stage 2 blocked

Stage 0 inspected the clean publication Git worktree. The Stage 1 transport
record is a credential-free client reachability check; it did not execute an
experiment, database, model, or scoring command locally. All actual experiment
execution remains server-only.

## Commands

Repository and clean-worktree baseline:

```powershell
git status --short --untracked-files=all
git rev-parse HEAD
git branch --show-current
git remote -v
git ls-remote origin refs/heads/main
```

Public visibility:

```powershell
gh repo edit w1-23/QAagent --visibility public --accept-visibility-change-consequences
gh repo view w1-23/QAagent --json name,visibility,url,defaultBranchRef,viewerPermission,isPrivate
curl.exe -fsSL https://api.github.com/repos/w1-23/QAagent
git ls-remote https://github.com/w1-23/QAagent.git refs/heads/main
```

Stage 0:

```powershell
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/00_repository_asset_audit.py `
  --repo-root "E:\codex_WORKSPACES\_work_experiment_publish" `
  --expected-head "6979aaaab9536f3eaf672c02763cafef232c8c2d" `
  --github-repo "w1-23/QAagent" `
  --require-public `
  --require-l1 `
  --output-dir "E:\codex_WORKSPACES\_work_experiment_publish\experiments\citrus_scholar_citation60\01_preflight\stage0\6979aaaab9536f3eaf672c02763cafef232c8c2d"
```

Supplementary Git-history audit:

```powershell
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/01_git_history_audit.py `
  --repo-root "E:\codex_WORKSPACES\_work_experiment_publish" `
  --protected-manifest "...\12_reproducibility\L1_CONTROLLED_ASSET_MANIFEST.json" `
  --out "...\01_preflight\stage0\6979aaaab9536f3eaf672c02763cafef232c8c2d\GIT_HISTORY_SECRET_AND_PROTECTED_PATH_AUDIT.json"
```

Stage 1 safe plan validation and refusal:

```powershell
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py `
  --plan experiments/citrus_scholar_citation60/01_preflight/SERVER_API_PROBE_PLAN.json

python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/10_server_api_probe.py `
  --plan experiments/citrus_scholar_citation60/01_preflight/SERVER_API_PROBE_PLAN.json `
  --execute `
  --out "<stage1-output>/UNAUTHORIZED_EXECUTION"
```

Stage 1 credential-free transport:

```powershell
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/11_transport_preflight.py `
  --host "<configured-experiment-host>" `
  --ssh-port "<configured-ssh-port>" `
  --http-port 8012 `
  --timeout-seconds 8 `
  --out "<stage1-output>/TRANSPORT_PREFLIGHT.json"
```

The committed JSON replaces the host/port with hashes.

## Results

| Stage | Result | Reason |
|---|---|---|
| Repository public switch and anonymous readback | `PASS` | GitHub API, raw `AUDIT_INDEX.md`, and unauthenticated `git ls-remote` succeeded |
| Git history credential scan | `PASS` | 910 blobs at visibility switch and 915 at follow-up; zero matching credential patterns |
| Legacy Git-history protected paths | `DISCLOSED` | current tree has 0; 14 protected L1 paths remain reachable in legacy history; no rewrite |
| Stage 0 repository audit | `PASS` | commit/tree, branch, clean worktree, remote tree/history, and fsck passed |
| Stage 0 asset audit | `BLOCKED` | protected L1 root absent; 16 declared, 0 verified |
| Stage 1 plan schema | `PASS` | 10 probes validate structurally |
| Stage 1 execution authorization | `BLOCKED` | plan is unconfigured and lacks all three authorization signals |
| Stage 1 transport | `BLOCKED` | SSH banner and experiment `/health` timed out after 8 seconds |
| Stage 1 Agent/Judge calls | `NOT_RUN` | transport failed before credentials/API calls |
| Stage 2 | `BLOCKED` | no real request/trace pair; synthetic fixtures are inadmissible |

## Evidence hashes

```text
0ce9b659e9df1fc926f4d6839abc5d864916d3e5ff4498fadf1a141125955534
  01_preflight/stage0/6979aaaab9536f3eaf672c02763cafef232c8c2d/STAGE0_AUDIT_MANIFEST.json

c72a5dc0cfadb06e81d40f1ef526082525387fff0e29647a20e96604c8ae43c9
  01_preflight/stage1/6979aaaab9536f3eaf672c02763cafef232c8c2d/TRANSPORT_PREFLIGHT.json

d1c4f49e048ea2dcf69d0f2bc56c4b3e4b3bbabed6960dce74bfb411c6d46c78
  01_preflight/stage2/STAGE2_PRECONDITION_REPORT_6979aaa.json

29d417743948d2ba8582c1a8a2c4b216b81ac904fe7e6d7d9ac2e3287d328825
  01_preflight/REPOSITORY_ACCESS_VERIFICATION_2026-11-05_PUBLIC.json

fc3f3fd4c2d9f51cf0452c3e04bcd37425d28ecab2b07c1daa0e097c97e651fe
  01_preflight/stage0/6979aaaab9536f3eaf672c02763cafef232c8c2d/GIT_HISTORY_SECRET_AND_PROTECTED_PATH_AUDIT.json

3177fea47e82796956456fa345a0931c1ca155694367b5e98bdecfb5e184f820
  01_preflight/stage0/6979aaaab9536f3eaf672c02763cafef232c8c2d/STAGE0_SUPPLEMENTAL_MANIFEST.json

d43aac7871c0ef771ca68cbf5e6475b022fd3c179b8ef76236da7ac8d0eb6dad
  01_preflight/stage1/6979aaaab9536f3eaf672c02763cafef232c8c2d/STAGE1_EVIDENCE_MANIFEST.json

b27935b9f6f8812522c674047368b13cf4fac06fbea17bf3d5dd365714b5e9ed
  01_preflight/stage2/STAGE2_EVIDENCE_MANIFEST.json
```

## Stop condition

No Pilot, formal run, scoring, statistical result, figure, or paper result was
created. G01 remains `BLOCKED`; G02-G15 remain `PENDING`; no gate was promoted.
