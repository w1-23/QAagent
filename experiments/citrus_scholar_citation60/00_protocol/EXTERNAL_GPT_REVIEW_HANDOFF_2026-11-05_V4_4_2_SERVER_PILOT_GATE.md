# External GPT review handoff: server/Pilot gate

Protocol version: `4.4.2`
Reviewed parent commit: `79a700c6b4c3ae582b980d68f3b4eca4b102267d`
Reviewed parent tree: `af3631d5ed768e4d2aa53e16da6b1dbe3bd0edb9`
Review state: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_2`
Execution authorization in this code-review unit: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`
Pilot: `NOT_STARTED`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`

This handoff reports the code and offline-test changes made after the external
review of `79a700c`. It does not claim server availability, model connectivity,
Pilot completion, or any experimental result.

## 1. Remaining P0 findings from the 79a700c review

### 1.1 Deployment-file SHA verification

Stage 1 now requires the deployment manifest to contain a non-empty
`deployment_id`, reviewed `git_commit` and `git_tree`, and a non-empty file
list. Every file path must be a safe relative path. The auditor resolves each
path below the checkout, requires the file to exist, recomputes SHA-256 from
the actual bytes, and rejects any mismatch. Commit/tree identity is checked
again against the G01 report and reviewed baseline.

### 1.2 Tool identity and group binding

The G01 tool-registration evidence must contain exactly 17 non-empty, unique
tools. Stage 1 freezes each tool's name, group, category and registration
version plus the registration evidence SHA. Stage 2 validates every event
against that registry. A B2 event can no longer satisfy the gate merely by
self-reporting `tool_group=LOCAL`; the frozen registry must itself assign that
group to the completed tool.

### 1.3 Unified execution authorization

QA, Citation, Pilot, J1/J2 and J3 entry points now use explicit authorization
modes. Normal execution requires all three signals:

```text
status = READY
execution_authorized = true
review_status in {APPROVED, APPROVED_FOR_EXECUTION}
```

Pilot may use the narrower `PILOT_READY` state, but only through the Pilot
authorization function. `UNCONFIGURED`, `BLOCKED`, a missing approval, or any
combination that lacks a signal fails closed.

### 1.4 Server and client request IDs

The request, trace, session registry and Stage 2 audit now carry:

```text
server_request_id
client_request_id
session_id
run_id
job_id
```

The server ID and client ID must both be non-empty, distinct and mutually
bound to the same run. The session registry rejects blank identifiers and
checks global uniqueness for run, request and session IDs. Its actual file
SHA-256 is recorded and bound into the Stage 2 report.

## 2. Real baseline adapters

The active adapters are no longer placeholders:

- `B0.py`: direct frozen-model diagnostic with no retrieval or tools;
- `B1.py`: one-pass RAG using the real CitrusScholar corpus registry,
  local/web retrieval, parser and attachment preparation, followed by one
  deterministic model call;
- `B2.py`: real isolated-service login, new session, SSE chat, history,
  citation and tool-result readback, artifact download, provider-response
  binding and backend-log binding.

All conditions use the same Agent model and generation settings. B1 and B2
share document/corpus access and resource counters; the intended difference is
the B2 Agent's full tool orchestration. No synthetic result is used as adapter
evidence.

## 3. Provider-response capture

`provider_capture.py` is a loopback-only recording proxy. The isolated service
points `CITRUS_AGENT_BASE_URL` at `127.0.0.1:8013`; the proxy forwards to the
real provider and saves:

- the exact request body and SHA-256;
- the raw provider response and SHA-256;
- a parsed JSON/SSE response envelope;
- provider response ID and requested/actual model;
- non-secret run/client/server/session/job IDs;
- HTTP status, timing and parse result.

`provider_capture_sitecustomize` is loaded only by the isolated experiment
process. It adds the non-secret request-context IDs to HTTPX requests sent to
the proxy origin. Authorization and Cookie values remain in memory and are
never written to evidence. The production service must not load this shim.

`capture_schema.py` cross-checks the adapter record, provider raw request,
provider raw response, capture-index record, SSE, `done.answer`, persisted
history, citations, tool-result files, backend logs and artifact files by
content and SHA. Forged, missing, mismatched, outside-root or empty references
fail closed.

## 4. Pilot gates

The active Pilot source is `03_benchmarks/pilot/pilot12_v4_4_2.csv`. The older
`pilot12.csv` is retained as history and is not active.

`10_make_pilot_runplan.py` requires exactly 12 unique questions, two per
`L/V/W/X/P/N` stratum, and deterministically expands them to 36
question-condition rows. It refuses overwrite.

The current v4.4.2 questions are material declarations with
`availability_status=NOT_VERIFIED`. Their evidence excerpts are deliberate
placeholders. A plan can be generated and dry-run for schema validation, but
`20_run_pilot.py --execute` rejects every placeholder or unverified material
before loading an adapter, Stage 2 report or G10 report. Real Pilot execution
therefore remains blocked until source/chunk locators and excerpts are
verified and frozen.

The real runner additionally requires approved Stage 2 and G10 reports,
execution authorization, a non-DeepSeek Judge configuration, complete capture
schema, and write-once raw files.

## 5. Expert and Judge scoring changes

- The blind-packet builder supports both 60-question/180-run formal plans and
  the 12-question/36-run Pilot.
- Exact three conditions per question, identical question prompts and
  identical same-question reference evidence are checked before packet
  creation.
- The expert-material builder now emits version `4.4.2` materials and accepts
  a 36-packet Pilot set.
- QA analysis now emits the four protocol dimensions, 52/8 strata and separate
  denominators rather than an aggregate-only result.
- Judge execution uses the same fail-closed authorization checks as the
  system runners. J1/J2 remain full scorers and J3 remains disagreement-only.

## 6. Files changed in this review unit

The change set is restricted to
`experiments/citrus_scholar_citation60/`. Principal files:

- `05_baselines/adapters/{adapter_common,B0,B1,B2}.py`;
- `scripts/capture_schema.py`;
- `scripts/A00_execution_readiness/provider_capture.py`;
- `scripts/A00_execution_readiness/provider_capture_sitecustomize/`;
- `scripts/A00_execution_readiness/{10_server_api_probe.py,20_audit_agent_execution_evidence.py}`;
- `scripts/execution_authorization.py`;
- `scripts/{run_matrix.py,protocol_lock.py}`;
- `scripts/E01_chunk_citation/35_run_citation.py`;
- `scripts/A01_llm_judge/` and `scripts/A02_human_scoring/`;
- `scripts/P00_pilot/10_make_pilot_runplan.py`;
- `scripts/P00_pilot/20_run_pilot.py`;
- `03_benchmarks/pilot/pilot12_v4_4_2.csv`;
- request, trace, probe-plan, model and Judge configuration templates;
- `AUDIT_INDEX.md`, this handoff, protocol documentation and test files.

No production source, database, session, upload, cache, backup or credential
file is part of this change.

## 7. Offline verification

- `python -m compileall -q experiments/citrus_scholar_citation60/scripts`:
  PASS.
- `python -m pytest experiments/citrus_scholar_citation60/scripts -q`:
  `108 passed, 5 skipped in 12.89s`.
- The five skips are the preserved protected-asset checks; they are not
  passes.
- New negative tests cover forged capture and backend-log paths, malformed
  SSE, request/model mismatches, unknown tool groups, incomplete authorization,
  placeholder Pilot evidence and the 36-run Pilot matrix.

These are local engineering results, not independent CI or server evidence.
The final package, static, JSON/CSV/Markdown, credential and Git checks are
recorded after these tests in the final delivery report.

## 8. Server status and next action

No server checkout, Stage 0 evidence run, account registration, Agent API
call, Judge API call, Pilot run or formal run has occurred for this revision.
The final local credential-free transport check found:

- TCP `28022`: open;
- TCP `8012`: open;
- SSH banner exchange: timed out after approximately 10 seconds;
- `http://211.69.141.120:8012/health`: timed out after 12 seconds with no
  response.

This remains a server transport blocker and is not Stage 0 evidence.

After external review of this commit:

1. restore/recheck SSH and the isolated experiment `/health`;
2. check out the exact reviewed commit/tree;
3. verify the Git remote has no embedded credential;
4. run Stage 0 and require a real G01 report;
5. run Stage 1 Agent/J1/J2/J3 probes;
6. run Stage 2 B2 with provider capture and actual file-SHA evidence;
7. verify the 12 Pilot materials and replace every placeholder with frozen
   source/chunk evidence;
8. run the 36 Pilot answers and expert/Judge calibration;
9. submit the real evidence bundle for the second-round formal-launch review.

Formal QA60 and Citation60 remain unauthorized.
