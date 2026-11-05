# B0/B1/B2 fair-baseline contract

Version: `4.3.2`
Status: `AWAITING_EXTERNAL_GPT_REVIEW`
Decision record: `00_protocol/DECISION_ADDENDUM_2026-11-04_V4_3.md`
Current decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_2.md`
Historical decision: `00_protocol/DECISION_ADDENDUM_2026-11-05_V4_3_1.md`

The adapters are implementation candidates, not evidence of connectivity or
execution. Existing Python adapters and judge scripts are legacy and must not
be executed in this review unit.

## 1. Conditions

| Condition | Purpose | Permitted input and capability |
|---|---|---|
| `B0` | Model-only diagnostic baseline | Same Agent model and generation settings; no local corpus, web, attachment, parser, or research-tool access. |
| `B1` | Fair simple-RAG comparator | Same Agent model and generation settings, same permitted sources, attachment text, parsed content, access rights, snapshots, and a frozen comparable retrieval budget as B2; one retrieval-to-context-to-answer pipeline without CitrusScholar tool orchestration. |
| `B2` | Complete CitrusScholar experimental release | The commit/environment locked at G01, with its authorized retrieval, source reading, attachment, tools, and artifact delivery. |

`B2 vs B1` is the unique primary confirmatory comparison. `B1 vs B0` and
`B2 vs B0` are descriptive diagnostics. B0 cannot be described as a fair RAG
baseline, and the P stratum's B0 limitation must be disclosed.

## 2. Frozen fairness controls

All three conditions use:

- the same Agent provider, actual model ID/version, reasoning/thinking mode,
  temperature, top-p, seed policy, maximum output tokens, timeout, and retry
  policy;
- a new independent session per planned run;
- the same question text and permitted-source scope;
- the same run-order randomization seed and frozen run-plan SHA;
- the same failure classification and denominator rules.

B1 and B2 additionally share:

- the same frozen document snapshots and attachment text;
- the same parser and extraction version;
- the same permission filtering and allowed-source scope;
- the same embedding, BM25, reranker, cache, top-k/context-token budget, and
  retrieval-cutoff rules;
- an auditable retrieval budget derived from the implemented system, not an
  invented top-k value.
- the same hard per-run caps for model calls, accumulated input tokens,
  accumulated output tokens, retrieval calls, context tokens, tool calls, and
  elapsed time.

The only intended B1/B2 difference is the predefined simple-RAG orchestration
versus the complete CitrusScholar tool workflow. Any other capability
difference is disclosed as a limitation or blocks the fairness gate.

The shared caps are:

| Resource | B1/B2 per-run cap |
|---|---:|
| Model calls | 8 |
| Cumulative input tokens | 32,768 |
| Cumulative output tokens | 8,192 |
| Retrieval calls | 8 |
| Context tokens | 16,000 |
| Tool calls | 12 |
| Elapsed time | 600 seconds |

These are hard ceilings, not targets, and do not force equal internal call
counts. Every actual call, token, retrieval, context allocation, tool event,
cost, and elapsed time is retained. Exceeding a cap produces
`BUDGET_VIOLATION`, retained in the intended denominator; it cannot be treated
as a successful answer. Cost is recorded from provider returns and is
`UNKNOWN` when the provider omits it. A missing provider cost is never replaced
with zero.

## 3. Provider and environment variables

Agent credentials use only:

```text
CITRUS_AGENT_API_KEY
CITRUS_AGENT_BASE_URL
CITRUS_AGENT_MODEL_ID
```

The candidate Agent provider is Alibaba Cloud Bailian and the candidate model
ID is `deepseek-v4.1-flash`. It is not `READY` until the server probe and terms
review record the actual returned model identity/version, region, workspace,
request/response evidence, and all required configuration.

No second Agent environment-variable namespace may be active. In particular,
legacy `OPENAI_COMPAT_*` variables are not authorized for experiment runtimes.

Judge credentials use only:

```text
CITRUS_JUDGE_API_KEY
CITRUS_JUDGE_BASE_URL
```

The Judge endpoint is OpenCode Go. Judge roles are fixed as GLM, MiMo, and
MiniMax model families; no DeepSeek-family judge is permitted. Secrets remain
outside the Git checkout and are never written to prompts, packets, logs, or
request archives.

## 4. Run result contract

`run_case` returns a real result with:

```text
status
final_answer
raw_answer
tool_calls
citations
artifact_paths
artifact_sha256
model_id
response_model
response_id
tokens_in
tokens_out
api_cost
list_cost
allowance
elapsed_ms
first_token_ms
  model_calls
  total_input_tokens
  total_output_tokens
  retrieval_calls
  context_tokens_used
  tool_call_count
  budget_status
error
implementation_notes
```

Allowed terminal statuses are `completed`, `partial`, `failed`, `no_results`,
`cancelled`, and `skipped`. Fixed "success" strings or fabricated tool events
are prohibited.

Every attempt records request, SSE events, tool traces, final answer,
citations, artifacts, failure/retry lineage, time, model/config/index
identity, and SHA values. A retry is allowed at most twice only for a
content-free transport failure. A response with substantive content is never
retried because quality is poor.

## 5. Evidence and release

Raw run records default to controlled `L1` and are retained in protected
experiment storage. They do not become `L2` merely because they are JSON, CSV,
or a screenshot. Public release follows a file-specific review under
`PUBLICATION_SCOPE.md`:

- `L0` credentials and authentication data never enter Git;
- `L1` restricted full text, private SOPs, identity linkage, and raw sensitive
  logs remain server-side;
- `L2` reviewed redacted run derivatives, permitted evidence locators, hashes,
  scores, statistics, and manuscript materials may enter GitHub after release
  review.

All adapters, prompts, configs, retrieval settings, and test evidence are
frozen by SHA before formal execution. A changed parser, index generation,
retrieval budget, model ID, or tool registry requires a new version and
change-log row.

## 6. Simulated data boundary

`scripts/S00_simulated_dry_run/` is for interface validation only. Every row
must be labelled `SIMULATED_DRY_RUN` with
`not_for_publication_claims=true`. Simulation cannot enter formal
denominators, Results, abstract, figures, or claims, and cannot be used to
pretend that a model or tool executed successfully.
