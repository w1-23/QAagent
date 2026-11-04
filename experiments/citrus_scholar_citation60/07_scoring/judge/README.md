# Machine-judge records

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Legacy status: `NOT_EXECUTABLE`

Machine judgments are an independent automated QA60 measurement. They are not
Gold, not the primary QA60 result, and not a replacement for human consensus.

The candidate roles are:

- J1: GLM `glm-5.3-flash`, all 180 answers;
- J2: MiMo `mimo-v2.6-flash`, all 180 answers;
- J3: MiniMax `minimax-m3.1-flash`, disputed fields only.

All three are called through one OpenCode Go OpenAI-compatible service entry
point. They are three model families through one service, not three independent
service providers. Actual model identity/version is frozen only after a server
probe and external review.

## Required output

J1/J2 output only:

```json
{
  "answerability_correct": true,
  "answer_correct": true,
  "evidence_traceable": true,
  "fatal_error": false,
  "reason_codes": [],
  "short_reason": "",
  "evidence_span_locators": []
}
```

For `UNANSWERABLE`, `answer_correct` and `evidence_traceable` are
`not_applicable`. A model-generated final decision, score, or PASS/FAIL is
invalid.

J3 returns only disputed field values plus reason codes, short rationale, and
locators. It cannot replace an agreed field. J3 failure leaves the run
`BLOCKED`; no majority vote, average, or fallback is allowed.

## Templates and retention

`qa_judge_TEMPLATE_180.csv` contains 180 row identities, separate human/machine
measurement fields, expected answerability placeholder, per-judge status and
four fields, J3 requirement/result, deterministic machine EGTS, strict status,
request/response SHA, actual response model, request ID, HTTP status, attempts,
tokens, cost/list_cost/allowance, elapsed time, and analysis-manifest linkage.

The blank template is not a result. For every actual call retain:

- one immutable file per attempt;
- redacted request and complete safe raw response;
- parsed JSON and schema-validation result;
- requested and actual model, version/fingerprint where available;
- region/workspace, request ID, temperature, seed, token limits, timeout;
- prompt/packet/request/response SHA;
- token usage, cost/list_cost/allowance, HTTP status, elapsed time, UTC, error;
- J1/J2/J3 raw records and explicit `NOT_REQUIRED` where applicable;
- blind mapping outside the judge packet;
- 180-row aggregation traceback and complete analysis manifest.

Missing, invalid, timeout, empty response, or schema failure is retained as
`JUDGE_ERROR`/`INVALID_SCHEMA`; it is never zero or success.

## Execution blockade

Existing Python judge scripts are legacy and are not authorized for execution.
Their output schema may still contain conflicting fields. A later reviewed
v4.3 implementation work unit must replace the scripts and tests before any
server model call.
