# User-turn context lifecycle

Status: 2026-11-15, P3/P4 production source integration, P5 current figure assets and P7 persistent reference audits. [General Agent architecture](general_agent_architecture.md) defines the approved target; [migration evidence](agent_migration_evidence.md) records tests and remaining limits. The baseline is `a888779e17eea04ec57525fa2f7c02d53841726b`. This document does not certify a deployed service.

A turn is one real user request, including its entire tool loop and final answer. A tool result, internal control message, summary or recovered execution is not a new user turn. Recovery does not refresh authorization, quota, cumulative budget or the original deadline.

## Four different states

| State | Authority and purpose | Must not be confused with |
|---|---|---|
| Conversation history | Original user messages, accepted answers, safe action records and stable IDs for UI/search | A disposable prompt or provider-private reasoning |
| Active model context | Sending view: rules, request, relevant history, methods and observations | Authoritative evidence |
| Task/run execution | Jobs, operations, budgets, update watermarks, pending calls and executable checkpoints | Navigation summaries or last-completed-batch facts |
| Assets/evidence | Owner-scoped source/result/file/dataset/script/figure IDs, versions, hashes and dependencies | Values reconstructed from model prose |

## Production loop and durable boundaries

`src/agent/host.py` uses `HarnessRuntime` in `harness.py`, backed by public LangChain `create_agent`/`AgentMiddleware`. LangGraph owns real model/tools node transitions with synchronous checkpoint durability. The old `runtime.py` retains shared value types and baseline tests; production host does not instantiate its executor. Rejected final answers return to the same tool-capable graph.

Existing `ModelDriver` retains provider binding, streaming, admission and physical-attempt accounting. `CapabilityExecutor` and `HostTools` retain bounded parallel reads, serial writes, interaction barriers, authorization, leases, durable operations and complete result publication. Placeholder graph tools cannot directly execute functions outside that path.

`checkpoint_store.py` supplies protected SQLite persistence bound to owner/session/user turn/run/provider/model. Explicit message transformations preserve excluded private protocol fields and nested receipt tuples before authenticated encryption. Public metadata is limited to controlled source/step/parent fields. Wrong scope, missing key, tamper and plaintext fail closed. Existing SQLite files are checked without opening and closing a raw descriptor, preserving active database locks. Key rotation and Windows deployment ACL administration remain operational work.

`BatchJournal` commits ordered batch intent before dispatch, without holding a database transaction across an await. It saves bounded observations after publication. A completed batch can restore those observations without executing tools again; incomplete intent or unknown results require reconciliation. Original full results are pinned and must pass owner/session/request/tool/hash/publication validation before reuse. A bounded replay must never overwrite the original full payload. Cross-store persistence is not a global transaction or a general exactly-once guarantee.

Host state includes the original server-prepared request, current query, method state, update watermark, observation records and trace boundary. Pending instructions are read without marking them applied. The watermark is saved synchronously before `metered_model` acknowledges the applied version. A failed checkpoint therefore does not consume an unapplied instruction. Cancelled and other non-answered stops preserve current host observations and durable stop state.

## Working-set projection

P3 implements the approved same-turn compression policy in `WorkingContextProjection`; it does not replace raw graph messages. Bounded first delivery and exact result readers remain the first means of reducing request size.

Above the configured trigger, eligible completed older groups may be summarized only after originals are encrypted, committed and verified readable through a fresh connection. Public `SummarizationMiddleware` supplies the summary. Real Human/System messages, required methods and protected protocol groups remain intact; native call/result pairs stay together. The latest group remains available. An unsafe or ineffective reduction stops the request rather than dropping evidence.

Generated navigation enters the sending view as a trusted internal `SystemMessage`, never as user authorization. The summary's physical model request uses the original task budget and deadline with no hidden retry. Identical prefixes can reuse a same-run cache; rebuilding after restart is metered again. Summary prose is navigation, not scientific evidence.

`read_context_evidence(checkpoint_id, message_count, message_index, start_char, max_chars)` returns exact public message JSON in bounded pages with offsets and SHA256. The server derives owner/session/turn/run/provider scope from the current authenticated job; the model cannot choose another scope. Private protocol fields are excluded from public readback. Context events publish only before/after estimates and archive references, plus a user-safe compression notice.

`context_compression=false` disables this projection for the request. Storage failure, unavailable readback, unsafe private-state projection or summary failure preserves originals and pauses honestly. No new run, artificial turn or subagent may evade capacity or budgets.

Input-window admission, output reserve and cumulative usage remain separate. Configured defaults (128,000 input tokens; cumulative 1,000,000 tokens/64 calls) do not prove provider capacity or effective administrator overrides. Unknown physical-request usage retains reserved charges, including attempts interrupted by service restart.

## Authenticated execution recovery

`POST /api/v2/chat/jobs/{job_id}/recover-agent` accepts only `{}`; replacement query, identity, budget and thread fields are rejected. It requires the original owner, session, account lease and an explicitly startup-interrupted job. The original request comes from the protected checkpoint, with file ownership/hash revalidation. An administrator cannot execute another owner's saved run through this endpoint.

Recovery uses a durable compare-and-swap claim and an in-process fence, then the ordinary capacity/graph/session-save/SSE lifecycle. It reuses the original job/turn/run, quota, budget and wall deadline. Pending instructions discarded specifically at restart are reactivated only after authorized admission. Old events remain immutable: an `agent_recovery` marker opens a new terminal generation, so the previous `done` cannot terminate the recovered stream. The frontend reducer reopens only on sequenced recovery admission.

Unknown operations, incomplete journal intents, cancellation, unsafe terminal states and missing ephemeral candidate pools block execution recovery. A saved complete/answered checkpoint can instead finish session persistence without another model call. Recovery skips normal session-load/prefetch and enters the restored supervisor, then session-save. These restrictions are intentional; a navigation checkpoint or a file on disk alone does not establish recoverability.

Actual child-process termination/reopen tests cover a completed batch before graph commit and a side effect before receipt: the former does not repeat the action; the latter refuses replay. Actual FastAPI ASGI/SSE tests cover authentication, request-field rejection, original identity, save-only recovery and append-only terminal generations. They use temporary data and synthetic identities, not deployed user accounts.

## P5 figure evidence across turns

A script figure retains its source version, original input snapshots, frozen typed result tables, independent renderer and numerical receipt. Style edits run only that renderer over frozen tables; the host compares renderer/data/geometry hashes before saving. Original analysis is provenance, never a style-replay input. Unsupported numerical geometry permits generation but does not claim safe style editing.

Only the original plotting source, necessary frozen data, current overrides/manifest and current exports are retained for a figure. Canvas and natural-language edits share the same overrides and CAS save path; `render_version` prevents concurrent overwrite and is not browsable history. New dependencies are pinned before CAS publication, obsolete pins are released afterward, and legacy snapshot tables are retired only after current dependencies are protected. Migration deletes no artifact files. Unsaved drafts remain separate from current downloads. Initial Nature receipts do not certify later edited bytes.

`research_workspace(task_state)` reads current durable job status and operations even without a legacy coverage snapshot. In that case state and snapshot sequence are null; it does not invent a task contract or remaining obligations. Code-owned readonly operation classification preserves failed reads without treating them as uncertain writes. Known output-contract rejection before script-run reservation is not_run and retains the acknowledged source reference. A restricted scientific worker failure remains failed only after invocation-scoped host evidence confirms process exit, no publication attempt, acknowledged durable failed run, matching owner/session/source/result and verified frozen input bytes. This lets the same Agent repair with the original task budget; it does not turn failure into success or not_run. Worker JSON cannot grant this exception. Timeout, unconfirmed exit, missing finalization acknowledgment, altered inputs and publication uncertainty still remain unknown.

## Between user turns and reusable results

After an authenticated completed/partial answer and trace are saved, account-leased background maintenance may generate a separate fixed-schema navigation summary. Pending questions and cancelled tasks are not completed turns for this purpose. Maintenance has its own metered allowance; failure preserves the accepted answer.

Exact source quotations must occur once in their authenticated source before publication. Revision/message-watermark CAS and inventory validation protect updates. Legacy records remain readable without making missing old evidence a permanent barrier to new turns. History-load degradation stays explicit. Source readers such as `research_workspace(operation='context_source', message_id=..., start_char=...)` preserve original input.

Tool results retain up to 800 unpinned payloads per session; required checkpoint/work dependencies are pinned. Session history, working state and maintenance are not one cross-store transaction: `done` can still report `history_saved=false`.

`research_workspace(operation='task_state')` reads event facts; it does not execute recovery or certify arbitrary model claims. Export uses exact dataset/result references and rejects mixed replacement data or oversized projections. Long integers remain text when needed for precision. Script version conflicts require read/reconcile before retry; explicit scientific-script repair is distinct from generic crash recovery.

P4 adds authoritative Skill receipts bound to identity, package version/hash, exact resource ranges and delivered message bytes. Typed load_skill/read_skill observations pass through HostTools and full-result publication; selected roots/dependencies preload as host control messages. The original selected_skills metadata survives recovery and question continuation. Context projection protects required method message IDs and revalidates actual bytes before the model call; legacy loaded flags and partial entrypoint reads grant no specialist access. Request-local Skill context is bound/reset with ContextVar, not captured in globally registered tool closures.

P4 also adds StoreBackend-backed, session-owned UTF-8 workspace files. Immutable versions reuse session_files; version index and resource pins commit together, with version/hash CAS for updates. Known CAS rejection reports not_run without overwriting bytes; unknown dispatched effects still stop. File persistence, method loading and execution checkpoints remain distinct. The production graph now goes session_load → supervisor → session_save; recovery enters supervisor directly. No implicit candidate prefetch or separate hints model runs. Actual local/web retrieval coverage and generic command execution remain unverified or blocked as recorded in migration evidence. P5 keeps the original drawing source, frozen dependencies and current figure state/exports; render_version/CAS prevents concurrent overwrites and does not expose restorable figure history.

P6 numerical audits retain per-rule execution receipts and a separate annotation layer. P7 reference audits use one owner/session-bound audit_id for extraction inventory, per-record durable results, cancellation, continuation and complete exports. Reading later pages or resuming does not rebuild the inventory, refill the persisted network budget or select the latest successful batch. Extraction coverage and record verification coverage remain separate; a partial parser cannot certify an entire document.

## Private provider continuity and terminal outcomes

The DeepSeek adapter sends opaque state only to the matching supported endpoint/model within the active turn. Protected checkpoint transformation now preserves live-message and chunk private fields through reopen; ordinary dump/repr, public history, summary and logs exclude them. Offline tests exercise this roundtrip and mismatch rejection. Real P3 requests used the configured `opencode_go / deepseek-v4.1-flash` gateway; they do not certify real official-DeepSeek traffic.

Parameter fallback still requires a specific unsupported thinking-option error and cannot replace an already-started stream. Cancellation, timeout, budget exhaustion, truncated output, uncertain effects and unavailable evidence remain visible even when generated prose sounds confident. There is no global scientific stagnation counter. Completion and delivery claims must be supported by stored observations and validated assets.
