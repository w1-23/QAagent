# Decision addendum v4.4.0: real-agent experiment execution

Decision version: `v4.4.0`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4`
Decision date: `2026-11-05`
Execution in this review unit: `NONE`
Supersedes: conflicting execution-boundary clauses in v4.3.0-v4.3.3

## 1. Decision

CitrusScholar is evaluated as a real agricultural research question-answering
system. The study tests the complete platform, including its normal access to
the registered corpus, source reading, retrieval, tools, citations, generated
artifacts, and long workflows.

The previous requirement that the Agent be prevented from reading questions or
the Citation60 corpus is revoked. A protected L1 root is no longer a
precondition for real Agent API testing. Questions may be sent normally to the
Agent. The Agent may retrieve and read the full registered corpus through the
platform.

This decision does not change the study endpoint, question counts, B0/B1/B2
fair-baseline contract, human/LLM scoring rules, statistical analysis, or
credential boundary.

## 2. Authoritative execution principles

| Item | Rule |
|---|---|
| Agent provider | Alibaba Cloud Bailian |
| Judge provider | OpenCode Go |
| Agent tests | Real Agent API and real platform execution |
| Judge tests | Real OpenCode Go API |
| Corpus | Not redistributed through GitHub; normally available to the platform |
| Questions | Normal test input and sent directly to the Agent |
| Accounts | Dedicated accounts registered on the experiment server |
| Independent question | One question, one new session |
| Long workflow | One new workflow session containing the related multi-step task |
| Evidence | Real request, SSE, tool trace, retrieval record, answer, logs, model identity, timing, usage, cost, failures |
| Synthetic data | Interface-test fixtures only; never formal evidence |

## 3. Revoked controls

The following are no longer Stage 0, Stage 1, or Stage 2 acceptance
requirements:

- existence of `CITRUS_EXPERIMENT_L1_ROOT` before an Agent request;
- hiding questions from the Agent;
- hiding `chunk_text` as an access-control condition;
- denying the Agent access to `03_benchmarks/citation60` or the registered
  corpus through normal platform retrieval;
- treating a normal platform-retrieved source, chunk, or locator as leakage;
- proving validity by disabling the system's real retrieval and source-reading
  functions.

The old boundary audit
`scripts/A00_execution_readiness/20_audit_agent_input_boundary.py` is
`SUPERSEDED`. Its replacement is
`scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py`.

## 4. Retained validity and isolation controls

The following controls remain mandatory because they prevent execution errors,
not because they restrict corpus access:

1. An independent question uses a new session and an empty conversation history.
2. Different conditions use different sessions and must not inherit another
   condition's answer.
3. Repeated runs use new sessions and new immutable run IDs.
4. A long workflow receives its own session; unrelated questions are not
   concatenated into that request.
5. The final Agent answer must be traceable to the Agent response and request
   ID, not substituted by a Judge response or a synthetic fixture.
6. All failed, cancelled, empty, retried, and partial attempts remain recorded.
7. Scoring, answer keys, blind maps, and another condition's answer are not
   injected into an Agent request.
8. Credentials, passwords, Cookie values, and Authorization values never enter
   logs, evidence, prompts, or Git.

## 5. Runtime data path

The preferred server evidence directory is outside the Git checkout:

```text
<experiment-root>/
  checkout/                 # exact reviewed commit
  config/                   # non-secret frozen configuration
  evidence/
    stage0/
    stage1/
    stage2/
    pilot/
    formal/
    judge/
    expert/
    analysis/
  logs/                     # service, API, task, and tool logs
  secrets/                  # protected environment files, never evidence
```

Raw experiment evidence remains on the server until release review. Reviewed,
redacted derivatives may be committed to GitHub. The full corpus is never
copied into GitHub merely to make the experiment reproducible; its source
registry, generation, hashes, access record, and retrieval evidence are
recorded instead.

## 6. Required observation chain

Each real answer record must connect:

```text
user request
  -> request_id
  -> account/session_id/run_id
  -> Agent provider and actual response model
  -> SSE events and first-token/final timing
  -> retrieval queries, source IDs, chunk IDs, ranks and scores
  -> tool calls with arguments, status, result and timing
  -> backend/API/task logs
  -> raw answer and final answer
  -> citations and source locators
  -> artifacts and SHA-256 values
  -> errors, retries and terminal status
  -> Judge request/response and score provenance
```

The trace must bind to the exact request SHA-256. Missing evidence is recorded
as `BLOCKED` or `INCOMPLETE`; paths must never be filled with invented values.

## 7. Revised Stage 0-2 definitions

### Stage 0: repository, runtime, corpus and observability

Verify the reviewed commit/tree, clean checkout, deployed environment,
service/database separation, actual registered corpora, index generation,
source availability, experiment directories, log destinations, and SHA-256
capture. L1 archive verification may be reported separately but is not an
execution prerequisite.

### Stage 1: server, account and provider execution

Verify server reachability, health, registration/login, authenticated session,
actual Agent model identity, three actual Judge model identities, API usage,
OpenCode Go response metadata, and rate-limit/retry behavior. Every request is
stored as a separate attempt.

### Stage 2: real end-to-end execution and observation

Run at least one real question through the complete platform in a new session.
Confirm normal corpus retrieval and source reading, capture SSE, tool calls,
retrieval hits, backend logs, final answer, citations, timing, and model
identity. Confirm the session contains no other question or condition. Confirm
the final answer is the Agent output matching the returned and persisted text.

## 8. Authorization state

The PI has authorized real API execution for the next execution round. This
review commit does not exercise that authorization. Before execution:

1. external GPT must read and confirm the updated repository baseline;
2. the reviewed probe/run configuration must be frozen with
   `status=READY`, `execution_authorized=true`, and
   `review_status=APPROVED_FOR_EXECUTION`;
3. credentials are provisioned only in the server environment file;
4. all execution occurs on the dedicated experiment server.

The keys previously pasted into chat are exposed and must not be reused.
