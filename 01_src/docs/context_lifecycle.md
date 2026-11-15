# User-turn context lifecycle

A turn is one real user request, including its entire tool loop and final answer. A completed tool action, an internal control message, or a recovery run is not a new user turn.

Migration note (2026-11-15): [General Agent architecture](general_agent_architecture.md) is the design authority. The storage and projection details below describe the existing implementation being migrated, not a requirement to retain its separate task-understanding call, tool-disabled final synthesis, or all-history checkpoint dependency. The new domain-independent loop uses explicit execution identity and returns failed final checks to the same tool-capable loop. It has not yet replaced the deployed runtime. No in-turn compression remains an explicit user constraint, with finite-window admission and honest incomplete outcomes.

## Active turn

- Keep the assembled user request, source restrictions, tool calls and returned evidence unchanged throughout execution. The message-admission function estimates the actual request and rejects a hard overflow; it does not summarize, fold completed calls, shorten evidence or replace a prefix.
- Final synthesis is part of the active turn. It receives the full transcript, task request, operation receipts and remaining gaps. It has no execution tools. Its budget reservation uses the same final-message builder and one final call, rather than a small artificial context cap that requires dropping evidence.
- Task understanding uses its own system instruction because it only describes obligations. It keeps the full user request and contextual messages; execution-only manuals remain in the execution prompt. Initial figure generation still receives the pinned Nature core.
- Large sources use tool-level pagination, dataset/file references and sandbox computation. New tool results are saved completely, with immutable call identity and SHA256. Only after a successful verified write can a result exceeding 16,000 characters first enter model context as a reference, exact head/tail previews, and readable field index. Storage failure keeps the original result; reader/skill pages are not folded again. This first-delivery interface never rewrites evidence already in the active turn.
- Active input admission defaults to 128,000 tokens (`context_budget.active_max_input_tokens`), additionally bounded by the configured model window less output and safety reserve. Both transport and loop admission use it. The 64,000-token historical working threshold is separate; neither configured value claims a verified provider window.
- Physical model calls, including retries, share the request's cumulative budget (default 1,000,000 tokens, configurable via `agent.max_model_tokens`; explicit administrator limits are retained). Actual reported usage settles the reservation. Unknown use is not invented as zero. Input-window, output and cumulative-call limits remain separate, configurable constraints; no claim of unlimited single-turn work is made.

## Between user turns

After an authenticated completed/partial answer and its trace are saved, an account-leased background job requests a fixed-schema navigation summary. Immediate answers use the same boundary as long tasks. Pending questions and cancelled tasks are not compressed. The independent maintenance allowance defaults to 48,000 cumulative tokens, at most two physical attempts sharing 30 seconds; all usage is metered without consuming the exhausted execution allowance. Failure does not delay or discard the answer.

The model proposes goal/decisions/unfinished navigation and exact source quotations. It does not calculate character offsets: the host requires each proposed quote to occur exactly once in its authenticated original source, locates its span, and verifies the extracted text before saving. Host state supplies completion and obligations, verifies complete tool pairs, and pins result references. Existing precision excerpts carry forward even when the next summary omits them. Long user inputs remain intact in original message storage and readable with `research_workspace(operation='context_source', message_id=..., start_char=...)`; navigation does not become a substitute for original numbers. Tool-result reads remain authenticated to the current account/session. Unpinned results rotate after 800 retained payloads; checkpoint-pinned results survive until session/account deletion. Legacy clipped evidence remains explicitly incomplete.

Checkpoint publication uses the existing revision/message-watermark CAS. Invalid schema, wrong quotes/status, missing evidence, cancellation or concurrent history changes leave the previous checkpoint and raw history intact. Loading a structured checkpoint checks its source inventory, quotes and pinned evidence again and never invokes a model. Legacy history retains its boundary-only compatibility path. Trusted server metadata identifies user instructions and internal controls; literal tags in user text are not authority. History-load failure remains visible.

`research_workspace(operation='task_state')` reads the same task contract, coverage gaps and actual operation receipts from the existing job event journal. It cannot execute or replay work, expand source authorization, or mark anything complete.

## Subtasks and handoff

A user request can contain multiple obligations and stages. Saving occurs more often than summarizing: each completed tool execution stores its raw result and terminal operation receipt before the next model step; accepted datasets, script versions, published figures and verification records retain their existing versioned identities. After each completed tool batch, the host journals the current contract/coverage/gaps. These are durable intermediate facts, not a new conversational turn or authorization to compress the active transcript.

Subsequent stages reuse exact references rather than recomputing or reconstructing values from a summary. Cross-turn source access uses the same authenticated readers and account-owned assets. A task-state snapshot describes the last completed batch; an interrupted or uncertain write is not considered complete and is never automatically replayed merely because a later stage needs it. Full task completion still requires all user-requested obligations and validated artifacts.

## Model preparation and reusable results

Each normal execution-loop model call can interpret the goal and prior observations, organize source-backed records, and prepare the next tool arguments or script. This does not require a separate mandatory preprocessing call before every tool. Model-produced organization and judgments remain proposals with source references; stored measurements and computed values remain their own immutable assets. Public work summaries describe observations and actions, not private reasoning.

`deliver_research_artifact(kind='table', dataset_ref=...)` materializes an authenticated, version/hash-checked dataset directly into CSV, TSV or XLSX without retyping its cells. It rejects mixed replacement rows, append requests and oversized projections rather than silently truncating. XLSX integers longer than 15 digits become text to preserve their digits; dates use ISO text and missing values become empty cells. Dataset source bytes remain unchanged.

A rejected script CAS write reports `script_version_conflict`, `write_applied=false` and an authorized, integrity-checked `latest_ref`. Read that source and reconcile before saving; the rejection does not apply any proposed edit. `repair_from_run_id` on the existing script runner accepts a retained failed/partial run and a revised script/ref. It revalidates and reuses that run's immutable input snapshots, parameters, output contract and render options in a new run, retaining the original repair-budget identity. Other execution arguments must be omitted. Running/successful runs, inaccessible or corrupted snapshots, and older receipts without complete replay metadata are rejected. This is explicit repair of existing scientific scripts, not a general shell interface or automatic crash resume.

## Private provider continuity

The DeepSeek Chat Completions adapter retains required opaque state only on live, serialization-excluded message fields. It returns that state to the matching official endpoint/model within the active user turn, and never publishes it as progress or source text. Other providers/proxies are not implicitly opted in. Parameter fallback requires a specific unsupported thinking-option error and cannot mix a new response into an already-started stream. Persisted-session recovery of opaque provider state is not implemented; the current adapter does not claim interrupted-loop resume.

## Clarification continuations

A user answer to a pending clarification starts a new execution segment of the same logical task. The host restores matching ancestor receipts into coverage and safe dedup state without replaying their actions or emitting new execution events. Attachment-backed checks require the same current source bytes. Published artifacts are revalidated for session, ownership, bytes and format before reuse; missing exports cannot satisfy figure coverage. Inherited records retain their originating job identity.

## Terminal outcomes

Completion depends on structured obligations, real coverage and validated delivery. Output-length termination, timeouts, budget exhaustion and missing evidence remain incomplete even if the response contains confident prose. An operation deferred by the per-batch limit keeps its identity, so a later successful execution can resolve that earlier not-run attempt. A transient read failure may receive a bounded safe retry; uncertain writes are not implicitly repeated.
