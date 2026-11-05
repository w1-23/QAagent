# A01 machine judge candidate implementation

Protocol version: `4.3.2`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Execution authorization: `NONE`

This directory contains candidate code and offline tests only. No model call,
Pilot, formal run, blind packet, or score has been produced.

## Frozen roles

| Role | Family | Candidate model | Scope |
|---|---|---|---|
| `judge_1` | GLM | `glm-5.3-flash` | all semantically evaluable core QA60 slots |
| `judge_2` | MiMo | `mimo-v2.6-flash` | independent scoring of the same slots |
| `judge_3` | MiniMax | `minimax-m3.1-flash` | only field-level J1/J2 disagreements |

DeepSeek is prohibited for every judge role because it is the Agent family.
Candidate IDs are not frozen actual identities. The server probe must record the
response `model`; aggregation accepts only the configured role allowlist and
blocks missing or mismatched identities.

## Data flow

1. `10_build_blind_packets.py` resolves the formal 60 x B0/B1/B2 plan, verifies
   identical prompts and canonical reference evidence across conditions, writes
   condition-blinded packets, and keeps the run mapping and raw-run SHA values
   in protected `L1`.
2. `20_dispatch_judge_1_and_2.py` sends each packet independently to Judge 1
   and Judge 2, preserving every attempt and safe raw response.
3. `30_adjudicate_disagreements.py` calls Judge 3 only when any of the four
   decision fields differ.
4. `40_aggregate_decisions.py` is a compatibility wrapper for deterministic
   field-level consensus.
5. `../aggregate_llm_judges.py` produces the 180-row traceback table in
   protected `L1`; strict mode fails closed for missing, invalid, mismatched, or
   unadjudicated records.

## Execution guards

Every `--execute` path requires all three independent signals:

```text
status == READY
execution_authorized == true
review_status in {APPROVED, APPROVED_FOR_EXECUTION}
```

Only content-free transport failures and HTTP `408/429/500/502/503/504` may be
retried, with at most two retries. Malformed JSON, invalid schema, empty model
identity, and model mismatch are terminal invalid results, not transport
retries. Authorization headers are never archived.

Judge 3 returns exactly:

```text
disputed_values
reason_codes_by_field
short_reason
answer_span_quote
evidence_span_locators
```

Any extra top-level field invalidates the response. Locators are retained in
the aggregation CSV.

## Current stop

Do not run `--execute`, create real packets outside protected server storage,
or treat the offline tests as Pilot evidence. G01 remains `BLOCKED`; G02-G15
remain `PENDING`; execution authorization remains `NONE`.
