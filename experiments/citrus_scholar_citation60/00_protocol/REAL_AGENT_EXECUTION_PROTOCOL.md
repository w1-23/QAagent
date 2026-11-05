# Real Agent execution and evidence-capture protocol

Version: `4.4.0`
Status: `READY_FOR_EXTERNAL_REVIEW`
Execution status in this commit: `NOT_RUN`

This protocol defines what must be captured when the real Agent execution round
starts. It does not contain a question, answer, model response, or experimental
result.

## 1. Request contract

Every independent short question has one immutable request record containing:

```text
request_id
run_id
session_id
question_id
condition
question
source_scope
tool_permissions
model_config
session_mode = NEW
question_count = 1
conversation_history = []
attachments
research_constraints
started_utc
request_sha256
```

The request must be sent through the reviewed experiment runner or normal
authenticated application endpoint. A normal question may include the task,
source scope, required deliverables, and research constraints. It must not
inject a scoring map, answer key, another condition's answer, blind map, or
Judge output.

## 2. Trace contract

The matching trace record contains at least:

```text
request_id
run_id
session_id
question_id
condition
request_sha256
provider
requested_model
actual_model
response_id
started_utc
finished_utc
status
raw_answer
raw_response_ref
raw_response_sha256
final_answer
answer_source = AGENT
persisted_answer
sse_events
sse_ref
sse_sha256
retrieval_events
retrieval_ref
retrieval_sha256
tool_events
citations
artifact_paths
artifact_sha256
backend_log_refs
usage
cost
list_cost
allowance
first_token_ms
elapsed_ms
errors
retry_attempts
previous_session_ids
```

`sse_events`, `retrieval_events`, `tool_events`, and `backend_log_refs` are
captured even when empty. Empty tool events are valid for B0, but not for a B2
Stage 2 demonstration that is supposed to prove normal retrieval.

Every SHA field for a captured object is bound to an actual file under
`--evidence-root`. The raw Agent response, raw SSE capture, retrieval capture,
each tool result/package, each backend log and each artifact must exist; its
bytes are hashed and compared with the trace. A hash-shaped string without file
content is not evidence.

## 3. Normal corpus access

The Agent may query, retrieve, read, cite, and quote the registered corpus
through CitrusScholar. A source, chunk, or locator appearing in a tool result
is valid evidence and is never treated as a leakage failure.

The trace must preserve:

- query text;
- source/corpus identifiers;
- document and chunk identifiers;
- rank/order and scores when exposed;
- returned excerpt or payload hash;
- tool name, arguments, result, status and timing;
- citation list returned to the user.

The experiment archives no full corpus copy in Git. It archives identifiers,
generation, source hashes, retrieval records, and reviewed derivatives.

## 4. Session isolation

For each independent question:

- create a new session;
- require empty conversation history;
- record prior-session IDs and prove the current ID is absent;
- use a distinct session for B0, B1, B2 and each repeat;
- never seed a session with another condition's answer.

A global session registry is mandatory for acceptance. It records `run_id`,
`request_id`, `session_id`, condition, question and creation provenance. The
auditor checks one-to-one uniqueness and rejects any session reused across
conditions or runs. Missing cross-run validation is reported as
`NOT_ASSESSED`, never as clean.

For a long workflow, create one new session for that workflow and preserve the
ordered task events inside it. The workflow session must not be reused for an
unrelated formal question.

## 5. Terminal status and failure retention

Allowed statuses are `completed`, `partial`, `failed`, `no_results`,
`cancelled`, and `skipped`. `timeout`, `transport_error`, `auth_error`,
`tool_error`, and `model_identity_mismatch` remain in the error detail and
intended denominator audit. They are not converted to successful answers or
deleted.

Retries are allowed at most twice only for content-free transport failure or
HTTP 408/429/5xx. Every attempt is stored separately. A response with actual
content is not retried because its quality is judged poor.

## 6. Stage 2 acceptance

Stage 2 for one real question passes only when:

1. request and trace identity fields match;
2. request SHA-256 matches the trace binding;
3. the session is new and empty;
4. Agent provider/model/response ID are present;
5. SSE contains a start and terminal event;
6. normal corpus retrieval is observed when `--require-corpus-access` is used;
7. requested and actual Agent models match the frozen configuration, and the
   actual model is cross-bound to the Stage 1 application/provider evidence;
8. tool events are present and statuses are not silently rewritten;
9. tool arguments, inline results and result files contain no scoring,
   answer-key, blind-map or other-condition material;
10. backend log references point to actual files whose SHA-256 values match;
11. the raw Agent answer file and raw SSE file are captured; the final displayed answer equals the
   persisted answer, and any `done.answer` payload equals that final answer
   after reviewed post-processing (such as appending a verified download
   link);
12. citations, retrieval capture and artifact files/hashes are captured;
13. no scoring/answer-key/other-condition material was injected;
14. the run is a real `completed` B2 execution with normal retrieval/tool
    execution, not merely a retained failed/cancelled attempt;
15. the global session registry is `VERIFIED_CLEAN`;
16. output is written once and retained, including failures.

The auditor command is:

```bash
python experiments/citrus_scholar_citation60/scripts/A00_execution_readiness/20_audit_agent_execution_evidence.py \
  --request "$REQUEST_JSON" \
  --trace "$TRACE_JSON" \
  --evidence-root "$RUN_EVIDENCE_ROOT" \
  --expected-model-config "05_baselines/model_config.json" \
  --stage1-manifest "$STAGE1_PROBE_MANIFEST" \
  --session-registry "$GLOBAL_SESSION_REGISTRY" \
  --expected-condition B2 \
  --required-tool-groups LOCAL \
  --require-corpus-access \
  --out "$EVIDENCE_ROOT/stage2/$RUN_ID.audit.json"
```

`expected-model-config` and the Stage 1 manifest are mandatory. The question
catalog is an optional additional exactness check. Neither an L1 root nor a
hidden-reference file is required.
