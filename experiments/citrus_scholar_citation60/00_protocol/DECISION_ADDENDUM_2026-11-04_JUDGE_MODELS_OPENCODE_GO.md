# Decision addendum: agent provider, low-cost judges, expert scoring, and server execution

Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Decision version: `v4.2`
Current amendment: `v4.3.2` (`DECISION_ADDENDUM_2026-11-05_V4_3_2.md`)
Date: `2026-11-04`
Repository baseline: `w1-23/QAagent` `main` at `b6bd31327b34584b0f3b5ec7ac6a7974d508efb2`
Scope: design and audit materials only; no model calls, pilot, formal run, or production change is authorized by this addendum.

This addendum supersedes conflicting judge-provider and human-scoring decisions in earlier planning documents. Earlier files and Git history remain unchanged as historical records. The experiment package and its research outputs are included in the GitHub audit/reproducibility record under this decision; credentials and material that cannot legally be redistributed remain excluded.

## 1. Provider and model roles

| Role | Provider | Candidate model ID | Planned use |
|---|---|---|---|
| Agent for B0/B1/B2 | Alibaba Cloud Model Studio (Bailian) | `deepseek-v4.1-flash` | Same model and generation settings for all three conditions |
| Judge 1 | OpenCode Go | `glm-5.3-flash` | Independent scoring of all 180 core QA60 answers |
| Judge 2 | OpenCode Go | `mimo-v2.6-flash` | Independent scoring of all 180 core QA60 answers |
| Judge 3 | OpenCode Go | `minimax-m3.1-flash` | Adjudication only when J1/J2 differ on one or more rubric dimensions |

The IDs above are candidates until a real request succeeds on the experiment server. Freeze the selected endpoint, provider region/workspace, request model, response model, version/fingerprint when available, and configuration SHA. An unavailable model or absent required access blocks the gate; operators must not substitute another model without a new decision-log entry and review.

Agent and judges use separate providers, environment variables, and credentials. The experiment must fail closed if a judge resolves to the DeepSeek family, if the Agent and a judge share a runtime credential, or if endpoint routing does not match the configured role. B0/B1/B2 share the Agent model, temperature, top-p, token limit, timeout, and thinking/reasoning setting.

The OpenCode Go dispatcher will use its supported OpenAI-compatible interface, a stable non-secret User-Agent, and a session header derived from the judge role plus blinded-packet SHA. The server probe must verify that the endpoint accepts the required request behavior; archive only redacted request metadata. For each attempt retain the sanitized request body, complete raw response body, actual response model, request ID, HTTP status, token usage, `cost`, `list_cost`, allowance, latency, error/retry state, request/response SHA, and timestamp. Never archive Authorization headers.

Judge lifecycle is `UNCONFIGURED -> PILOT_READY -> READY`, with `BLOCKED` entered on any required failure. Connectivity alone can establish only `PILOT_READY`; `READY` requires successful Pilot calibration, expert agreement thresholds, blind-packet validation, and complete retention checks.

## 2. Human scoring supplies the semantic QA60 measurement

Two domain experts independently score every Pilot output (36 records) and every formal core QA60 output (180 records). A third domain expert adjudicates any disagreement on the four rubric fields. The v4.3.2 amendment defines `egts_system_outcome` over all 60 planned slots as the confirmatory system endpoint and `human_consensus_egts` as the separately reported semantic endpoint with explicit coverage. The three LLM judges remain an independent automated measurement used for agreement, error analysis, cost, and calibration reporting; they do not replace human semantic consensus.

Experts do not write reference answers. Each receives the question, answer, normalized citations, a common evidence packet extracted independently from the frozen permitted sources, and the scoring rubric. Expert packets must not expose condition, provider/model, run ID, question ID, LLM judgments, or another expert's ratings. The separate reviewer mapping and packet mapping are retained for audit but never distributed to scorers.

The exact packet contents, row schema, adjudication method, and human agreement outputs are specified in `EXPERT_SCORING_PROTOCOL.md`. The same source context is used for all three conditions of a question. If permission does not allow a source excerpt to be placed in a public repository, preserve its SHA and locator in GitHub and provide the evidence through an authorized, access-controlled server-side packet.

## 3. Credentials and security

The two credentials previously pasted into the conversation are considered exposed and must be revoked and rotated before any model call. They must not be copied into a file, command, issue, log, screenshot, Git object, or model packet. New credentials are supplied only through protected server-side environment variables or a permission-restricted credential file:

- Agent: `CITRUS_AGENT_API_KEY`, `CITRUS_AGENT_BASE_URL`, `CITRUS_AGENT_MODEL_ID`.
- Judges: `CITRUS_JUDGE_API_KEY`, `CITRUS_JUDGE_BASE_URL`.

Only the server hostname, `auth_present`, and redacted request body may be archived. Never archive an Authorization header or secret hash. At experiment close, the PI removes the server-side credentials and records the completion and secret-scan evidence.

The PI/credential owner must confirm that OpenCode Go permits the planned research judging workload under current terms. No inference about batch-use permission, model privacy, or data retention is made from a subscription name alone.

## 4. Scoring and analysis boundary

Core QA60 remains 60 questions: 52 `ANSWERABLE` and 8 `UNANSWERABLE`; B0/B1/B2 produce 180 runs. Two human experts score each semantically scorable run; a third adjudicates disagreements. J1 and J2 also independently score each semantic slot; J3 is invoked only on disagreement across the four rubric fields. The deterministic aggregation script calculates semantic EGTS from the locked question label and final human fields; `egts_system_outcome` retains all 60 planned slots and records infrastructure failure as system-task failure.

The supplementary citation60 experiment remains separate: 60 frozen target chunks and questions, 180 runs, and a deterministic final-citation `target_chunk_id` match as its only primary endpoint. No human or LLM semantic-support rate is collected or reported. Core QA60 and citation60 never share a denominator.

Report human-human agreement, LLM-LLM agreement, LLM-to-human-consensus agreement, third-expert adjudication rate, J3 invocation rate, missing/invalid records, token use, provider-reported cost, list cost, allowance, latency, and retries. Provider-reported zero cost under an allowance is not a failed request; preserve both cost and allowance fields.

## 5. Server-only execution and review checkpoints

All API probes, integration tests, SQLite checks, Pilot, formal runs, score aggregation, analysis, and figure generation run on the dedicated server experiment instance described in `SERVER_EXECUTION_PROTOCOL.md`. No experiment or model test is run on the local workstation. Each completed work unit is committed to GitHub with evidence and then paused for external GPT review before the next unit begins.

The repository plan submission itself is the current work unit. Its handoff is in `EXTERNAL_GPT_REVIEW_HANDOFF_2026-11-04.md`. Server connectivity was not verified for that work unit; G01 and server execution remain blocked until identity and isolation are verified. This design decision does not change any gate to `PASS` and does not authorize production changes.

## 6. Superseded decisions and traceability

- The earlier Qwen/DeepSeek/GLM flagship judge selection is `SUPERSEDED` by this addendum; retain the earlier decision record and configuration snapshot for provenance.
- Earlier instructions that excluded human scoring of the 180 formal core QA60 answers are `SUPERSEDED`; two experts now score all 180 and a third adjudicates field disagreements.
- Existing question-validity review remains a separate task: two independent reviewers certify all 120 formal questions. This is not answer scoring and does not create a Gold Answer.
- Record this change as a new append-only row in `00_protocol/change_log.csv`; do not rewrite historical rows or mark G11 ready before connectivity, calibration, and expert materials are complete.
