# External GPT review handoff: v4.4.0 real-Agent execution

Protocol version: `4.4.0`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
GitHub baseline before this revision:
`c64d8df1e8ab53dc986371dc99c7735a5fa19357`
Execution in this revision: `NONE`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`

## 1. Review request

Review the exact `main` commit/tree containing this file, rather than relying on
this written status. Confirm that the repository now describes and audits a real
CitrusScholar experiment without disabling its corpus, retrieval, source
reading, citations, tools, SSE, artifacts, or long workflows.

Do not approve an execution unit from this handoff alone. Inspect the current
commit, decision record, gate ledger, candidate probe plan, Stage 2 auditor,
tests, and absence of credentials or real results.

## 2. Authoritative correction

The following v4.3.x controls are revoked:

- an L1 root must exist before real Agent testing;
- questions must be hidden from the Agent;
- `chunk_text` must be hidden from the Agent;
- the Agent must be denied normal access to the registered corpus or Citation60
  source material;
- a normal retrieved source, chunk, locator, title, or excerpt is a validity
  leak;
- experiment validity is established by disabling retrieval and source reading.

The v4.4.0 rule is:

- the Agent receives normal test questions;
- the Agent uses the real CitrusScholar corpus and tools through the platform;
- retrieved source IDs, chunk IDs, chunk text, titles, excerpts, citations, and
  locators are valid evidence;
- one independent question uses one new session;
- different conditions and repeats use different new sessions;
- a related long workflow uses one dedicated new session;
- scoring maps, answer keys, blind maps, expert/Judge scores, and another
  condition's answer may not be injected into the Agent;
- simulated fixtures never substitute for real execution evidence;
- every failed, retried, cancelled, partial, or empty attempt is retained.

The authoritative decision is
`00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`.

## 3. Files most relevant to review

Protocol and decisions:

- `AUDIT_INDEX.md`
- `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_4_REAL_AGENT_EXECUTION.md`
- `00_protocol/REAL_AGENT_EXECUTION_PROTOCOL.md`
- `00_protocol/SERVER_EXECUTION_PROTOCOL.md`
- `00_protocol/02_实验执行与论文写作总计划.md`
- `00_protocol/04_需要PI确认与人工执行的清单.md`
- `00_protocol/change_log.csv`
- `PUBLICATION_SCOPE.md`

Configuration and gates:

- `01_preflight/gates.csv`
- `01_preflight/SERVER_API_PROBE_PLAN.json`
- `05_baselines/model_config.json`
- `05_baselines/judge_config.json`
- `05_baselines/baseline_contract.md`

Stage 2 evidence contract:

- `01_preflight/AGENT_EXECUTION_REQUEST_TEMPLATE.json`
- `01_preflight/AGENT_EXECUTION_TRACE_TEMPLATE.json`
- `scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py`
- `scripts/A00_execution_readiness/tests/test_execution_readiness.py`
- `scripts/A00_execution_readiness/README.md`

Legacy-history disclosure:

- `01_preflight/LEGACY_CITATION60_SOURCE_TEXT_EXPOSURE_AUDIT.json`

## 4. Model and provider contract

| Role | Provider | Candidate model | Required evidence |
|---|---|---|---|
| Agent | Alibaba Cloud Bailian | `deepseek-v4.1-flash` | requested and actual response model, response ID, region/workspace, usage, status |
| Judge 1 | OpenCode Go | `glm-5.3-flash` | actual model identity, complete safe response, usage/cost, attempts |
| Judge 2 | OpenCode Go | `mimo-v2.6-flash` | independent full scoring, actual model identity |
| Judge 3 | OpenCode Go | `minimax-m3.1-flash` | only four-field disagreement adjudication |

The Agent and Judges use different provider credentials. No Judge may be from
the DeepSeek family, reuse the Agent endpoint, or reuse the Agent runtime key.
Candidate model IDs are not represented as available until a real Stage 1
probe records the actual response model.

Both checked-in configuration files and the probe plan remain:

```text
status = UNCONFIGURED
execution_authorized = false
review_status = AWAITING_EXTERNAL_GPT_REVIEW_V4_4
```

## 5. Stage 2 answer-provenance correction

The previous candidate auditor required `raw_answer == final_answer`. That
would reject valid reviewed post-processing such as appending a verified
download link.

The revised invariant is:

```text
raw_answer is retained
answer_source == AGENT
final_answer == persisted_answer
done.answer, when present, == final_answer
request SHA-256 and request/session/run/question/condition IDs are bound
```

The audit continues to require SSE start/terminal events, retrieval and source
locators when corpus access is required, tool names/statuses/arguments, backend
log references and hashes, new-session evidence, model identity, citations and
artifact-hash containers, and control-material path protection.

Normal `chunk_id`, `chunk_text`, source locators, titles, and excerpts are not
flagged as leakage.

## 6. Current gate state

```text
G01 = BLOCKED
G02-G15 = PENDING
PASS = 0
```

G01 remains blocked because the last server reachability evidence predates
v4.4.0 and no current reviewed server/environment/corpus probe has run. No
gate is promoted by these offline edits.

## 7. Offline verification performed

Working directory:

```text
E:\codex_WORKSPACES\_work_experiment_publish
```

Commands and results:

```text
python -m compileall -q scripts
PASS

python -m pytest -q -rs scripts
76 passed, 5 skipped in 7.48s

python scripts/check_package.py
PASS; 60 QA questions, 120 validity rows, 180 Judge rows,
192 routing cases, 24 composite cases, 7 workflows

python scripts/audit_static_package.py
PASS; 81 JSON, 7 JSONL, 35 CSV, 92 Markdown,
292 files secret-scanned, zero errors

git diff --check
PASS
```

The five skipped tests require protected Citation60 inputs. They are not
reported as passed. They do not block real Agent access to the registered
corpus; they require the separate protected archive needed for release and
freeze verification.

An offline Stage 0 attempt during the uncommitted review build correctly
returned `git_worktree_not_clean`. It is not treated as Stage 0 acceptance
evidence. Stage 0 must be rerun against the clean reviewed commit on the
dedicated server.

## 8. Actual execution status

This review unit did not:

- create or edit a `.env` or credential file;
- register an experiment account;
- start or restart production or experiment services;
- read or reuse the credentials pasted into prior chat;
- call Alibaba Cloud Bailian;
- call OpenCode Go;
- create real request/trace evidence;
- run Stage 0 on the experiment server;
- run Stage 1 or Stage 2;
- execute an Agent question;
- run Pilot or formal experiments;
- score answers;
- compute research statistics;
- create research figures or manuscript results.

The keys previously pasted into chat are treated as compromised. New keys must
be created/provisioned by the PI outside Git and outside all evidence.

## 9. External GPT decisions requested

Please explicitly answer:

1. Is the real-Agent evaluation principle now coherent across the active
   protocol, gates, configuration, templates, and candidate code?
2. Does any active v4.4 requirement still incorrectly deny normal Agent corpus
   access or require a hidden input boundary?
3. Does Stage 2 fail closed on simulation, cross-session contamination,
   non-Agent answer substitution, missing request/trace binding, or missing
   retrieval/log evidence?
4. Is the Agent/Judge provider and model-family separation sufficient before
   Stage 1?
5. Is it correct to retain the legacy `chunks_FROZEN.jsonl` exposure as a
   separate release-rights finding without making it an Agent-access blocker?
6. Are the test counts, remaining skips, gate states, and no-execution claims
   accurately represented?
7. What exact corrections, if any, are required before the next server Stage
   0-2 work unit may begin?

## 10. Stop condition

This revision ends after an ordinary push to public `main`. Do not proceed to
server execution until external GPT has reviewed the exact pushed commit/tree
and returned an explicit decision. If the repository owner changes `main`
during this review, fetch and re-audit the new head before acting.
