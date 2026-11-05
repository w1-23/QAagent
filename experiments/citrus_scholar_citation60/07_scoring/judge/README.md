# Machine-judge records

Protocol version: `4.3.3`
Status: `AWAITING_SIXTH_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`

Machine judgments are an independent automated QA60 measurement. They are not
Gold, not the confirmatory `egts_system_outcome`, and not a replacement for
human semantic consensus.

The candidate roles are:

- J1: GLM `glm-5.3-flash`, all 180 semantic slots (`completed`, `partial`, and
  `no_results`);
- J2: MiMo `mimo-v2.6-flash`, the same 180 semantic slots;
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
  "reason_codes_by_field": {
    "answerability_correct": [],
    "answer_correct": [],
    "evidence_traceable": [],
    "fatal_error": []
  },
  "short_reason": "",
  "answer_span_quote": "",
  "evidence_span_locators": []
}
```

For `UNANSWERABLE`, `answer_correct` and `evidence_traceable` are
`not_applicable`. A model-generated final decision, score, or PASS/FAIL is
invalid.

J3 returns only disputed field values plus field-scoped reason codes, a short
rationale, an answer-span quote, and locators. It cannot replace an agreed
field. J3 failure leaves the run
`BLOCKED`; no majority vote, average, or fallback is allowed.

Every substantive false field and every `fatal_error=true` needs a non-empty
short rationale and exact answer-span quote, except `NO_RESPONSE` when no text
exists. `fatal_error=false` is the ordinary no-fatal result and does not itself
require a reason or quote. A quote must never reproduce a real credential,
personal identifier, or complete private SOP.

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

The Python scripts and tests in this package now target the v4.3.2 JSON schema
but remain non-executable until server connectivity, blind-packet, retention,
and Pilot checks are separately reviewed. Static tests do not authorize a
model call.
