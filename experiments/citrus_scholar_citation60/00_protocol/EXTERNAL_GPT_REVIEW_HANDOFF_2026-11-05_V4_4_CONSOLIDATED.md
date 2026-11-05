# External GPT review handoff: consolidated v4.4 evidence hardening

Protocol version: `4.4.0`
Reviewed parent commit: `f1dbbe0cf96eed2cee4f2f6e4bb53b49b16ab073`
Reviewed parent tree: `b015b55beb4f6bcf82de24439b5d23e0500f3e5a`
Grandparent commit: `8491db28ebe69876d4345b459f07909e67670d3f`
Status: `AWAITING_EXTERNAL_GPT_REVIEW_V4_4_CONSOLIDATED`
Execution authorization in this code-review unit: `NONE`
Gate state: `G01=BLOCKED`, `G02-G15=PENDING`, `PASS=0`
Pilot: `NOT_STARTED`
Formal experiment: `NOT_STARTED`
Formal results: `NO_RESULTS`

This is the single consolidated code correction requested after review of
`f1dbbe0`. It does not reopen the accepted v4.4.0 research design. Questions
remain normal Agent input, registered-corpus retrieval remains allowed, and
scoring/control material remains outside Agent input.

## 1. Stage 2 evidence binding

- Raw API response content is extracted from the captured provider response
  (`choices[0].message.content`, compatible content parts, or accepted output
  equivalents) and must equal `raw_answer`.
- The captured raw response `id` and `model` must equal the trace
  `response_id` and `actual_model`.
- SSE capture must parse as an event list or JSONL/SSE data records. Malformed
  capture is blocked, and each captured event is compared with the trace.
- Retrieval capture is also parsed strictly and compared event-by-event with
  `retrieval_events`; evidence-file fragments are not accepted as substitutes.
- Each tool-result file must equal the inline tool result exactly, and the file
  is separately scanned for control-material access.
- B2 acceptance always requires at least one completed `LOCAL` tool event.
  Failed, cancelled, skipped, empty or missing tool events cannot satisfy the
  requirement.
- Backend logs must be object references with an actual in-root file path and
  valid SHA-256. String-only log references are rejected. Each log must contain
  the request, run or response identity, and at least one log must contain the
  request ID.

## 2. Judge identity binding

The Stage 1 probe refuses to execute when:

- the Judge configuration is still `UNCONFIGURED`;
- a configured `candidate_model_id` differs from the probe request model;
- configured allowed models differ from the probe allowlist or model-identity
  allowlist;
- a candidate is missing from its allowed set or is duplicated across roles;
- a non-null actual response model is outside the configured allowlist;
- a Judge role belongs to the forbidden DeepSeek family.

The probe manifest retains role, requested model, actual model, response ID,
request SHA, usage, status and attempt path for each Judge call.

## 3. G01 hard gates

The Stage 1 guard now verifies:

- the reviewed/deployed commit and tree match the externally reviewed values;
- deployment and production identity evidence have valid SHA-256 values and
  actual files whose bytes match;
- experiment and production roots are absolute and distinct;
- production write-denial and production-credential-unreadability evidence
  files exist and match their declared SHA-256;
- all five corpus registrations are unique, non-empty, accessible from the
  locked account, have positive source counts, generation IDs, and real
  source-manifest/access evidence files;
- exactly 17 tool names are present, non-empty, unique and syntactically
  valid, with a real registration evidence file;
- index generation, manifest file and SHA are present;
- API/task/retrieval/tool log destinations are real path+SHA objects;
- dependency-lock and service-config files exist and match their SHA-256.

The checked-in G01 JSON remains a blank template, not evidence.

## 4. Review binding

The reviewed parent for this patch is `f1dbbe0`, tree `b015b55`. The current
probe, model and Judge configurations remain `UNCONFIGURED` and unauthorized.
Before any Stage 1 execution, they must be copied to a final frozen execution
configuration and bind the exact externally approved commit/tree. They must not
silently reuse this review-parent SHA as evidence of a later deployment.

## 5. Two-round execution strategy

Round 1 is this consolidated code correction plus, when server access permits,
non-destructive Stage 0 environment evidence. Round 2 reviews the resulting
code, Stage 0-2 evidence, tool-family evidence, Pilot runs, expert/LLM
agreement and B0/B1/B2 fairness before approving formal QA60 and Citation60
execution.

No gate is promoted by this file. The target is approval to start the formal
runs in round 2, not completion of the formal runs in round 2.

## 6. Verification

The consolidated patch was checked locally only; no server, provider, account,
Pilot or formal experiment was run.

- `python -m compileall -q experiments/citrus_scholar_citation60/scripts`: PASS.
- `python -m pytest -q experiments/citrus_scholar_citation60/scripts`:
  `98 passed, 5 skipped in 10.50s`. The five skips are the preserved protected
  asset checks and are not passes.
- `python experiments/citrus_scholar_citation60/scripts/check_package.py`:
  PASS; 60 QA questions, 180 QA rows, 192 route cases, 24 composite cases and
  7 workflows verified; citation60 remains unavailable in the public tree and
  no semantic-support metric is reported.
- `python experiments/citrus_scholar_citation60/scripts/audit_static_package.py`:
  PASS; JSON/JSONL/CSV/Markdown structure and the current-tree credential scan
  passed.
- `git diff --check`: PASS.
- The read-only reachable-history scan completed with 1,029 text blobs and
  1,563 reachable objects, zero credential findings, zero protected paths in
  the current tree, and 14 legacy protected-history paths disclosed without
  history rewrite.
- The per-file register was refreshed to 294 files. No result or evidence
  artifact was fabricated.

