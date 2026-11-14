"""Typed request plan: user constraints, environment facts, and decisions.

This is a pure composition layer over the existing request parsers. It does not
read provider credentials, attach a graph, or execute a tool. ``RequestPlan``
makes the distinction between *what the user required*, *what the environment
can offer*, and *what the model is merely advised to do* explicit and testable.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from src.graph.retrieval_router import (
    RetrievalDecision, RequestConstraints, decide_retrieval,
    parse_request_constraints, required_route_groups, source_scope,
)
from src.graph.tool_availability import (
    Env, available_tool_names, explain, runtime_env,
)
from src.graph.tool_policy import ToolPolicy, resolve_tool_policy

SCHEMA_VERSION = 1

# A note write is the only declared side effect that needs an explicit request.
# This parser is pure and lives here so the request plan, the visibility layer,
# and the executor all read the same fact.
_NOTE_EXPORT = re.compile(
    r"(?:导出|下载|export|download).{0,12}(?:课题|项目|研究)(?:记录|笔记)|"
    r"(?:课题|项目|研究)(?:记录|笔记).{0,12}(?:导出|下载|export|download)",
    re.I)
_NOTE_SAVE = re.compile(
    r"(?:保存|记录|记住|记下来|加入课题记录|save).{0,18}(?:课题|项目|研究|note|notes)|"
    r"(?:课题|项目|研究|note|notes).{0,18}(?:保存|记录|记住|save)", re.I)


def explicit_note_save_request(query: str) -> bool:
    """True only when the user asked to write a project note this turn."""
    text = str(query or "")
    if _NOTE_EXPORT.search(text):
        return False
    return bool(_NOTE_SAVE.search(text))


def executor_authorizations(query: str) -> tuple[str, ...]:
    """Tools whose side effect the user explicitly requested in this turn."""
    if explicit_note_save_request(query):
        return ("save_research_note",)
    return ()


@dataclass(frozen=True)
class RequestFacts:
    session_id: str = ""
    request_id: str = ""
    job_id: str = ""
    client_request_id: str = ""
    attachments: int = 0
    account_files: int = 0
    has_paper_attachment: bool = False
    network: bool = True
    local_corpus: bool = True
    research_tools: bool = True
    visualization: bool = True
    has_session: bool = True
    has_history_evidence: bool = False
    recent_turn_seqs: tuple[int, ...] = ()
    local_corpus_match: bool = False
    # Set by the supervisor after the request-scoped prefetch finished. It is a
    # runtime fact, not a topic guess: candidate tools cannot run without a pool.
    has_candidate_pool: bool = False


@dataclass(frozen=True)
class RequestPlan:
    schema_version: int = SCHEMA_VERSION
    query: str = ""
    textual_scope: str = "auto"
    selected_scope: str = "auto"
    constraints: dict[str, Any] = field(default_factory=dict)
    retrieval_decision: dict[str, Any] = field(default_factory=dict)
    tool_policy: dict[str, Any] = field(default_factory=dict)
    visible_tools: tuple[str, ...] = ()
    required_tool_groups: tuple[tuple[str, ...], ...] = ()
    provider_denials: tuple[str, ...] = ()
    capability_denials: tuple[str, ...] = ()
    executor_authorizations: tuple[str, ...] = ()
    deliverables: tuple[dict[str, Any], ...] = ()
    availability_reasons: dict[str, str] = field(default_factory=dict)
    legacy_router_mode: str = "advisory"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_request_plan(
    query: str,
    facts: RequestFacts | None = None,
    *,
    selected_scope: str = "auto",
    paper_only: bool = False,
    provider_denials: frozenset[str] | set[str] = frozenset(),
    denied_tools: frozenset[str] | set[str] = frozenset(),
) -> RequestPlan:
    """Compose one deterministic request plan from parsed constraints + Env."""
    facts = facts or RequestFacts()
    q = str(query or "")
    textual_scope = source_scope(q)
    effective_scope = (textual_scope if textual_scope != "auto"
                       else str(selected_scope or "auto"))
    constraints: RequestConstraints = parse_request_constraints(
        q, effective_scope, has_table_attachment=facts.attachments > 0)
    decision: RetrievalDecision = decide_retrieval(
        q, effective_scope,
        has_history_evidence=facts.has_history_evidence,
        recent_turn_seqs=facts.recent_turn_seqs,
        local_corpus_match=facts.local_corpus_match,
    )
    policy: ToolPolicy = resolve_tool_policy(q, effective_scope,
                                             paper_only=paper_only)
    capability_denials: set[str] = set()
    if (constraints.reason_code == "data_audit_only"
            or constraints.source_policy == "deny"
            or not policy.allow_retrieval):
        capability_denials.update({"network", "local_corpus"})
    if not facts.visualization:
        capability_denials.add("visualization")
    env: Env = runtime_env(
        source_scope=effective_scope,
        attachments=facts.attachments,
        account_files=facts.account_files,
        session=facts.has_session,
        research_tools=facts.research_tools,
        network=facts.network,
        local_corpus=facts.local_corpus,
        retrieval=policy.allow_retrieval,
        capability_denials=frozenset(capability_denials),
        denied=frozenset(denied_tools) | policy.deny
        | frozenset(provider_denials),
        candidate_pool=facts.has_candidate_pool,
    )
    required = tuple(tuple(sorted(group))
                     for group in required_route_groups(decision))
    from src.graph.export_contract import deliverable_contract
    deliverables = tuple(deliverable_contract(q))
    return RequestPlan(
        query=q,
        textual_scope=textual_scope,
        selected_scope=effective_scope,
        constraints=asdict(constraints) | {
            "required_tool_groups": [sorted(group)
                                     for group in constraints.required_tool_groups],
        },
        retrieval_decision=decision.as_diagnostic(),
        tool_policy={"allow_retrieval": policy.allow_retrieval,
                     "deny": sorted(policy.deny), "reason": policy.reason},
        visible_tools=tuple(available_tool_names(env)),
        required_tool_groups=required,
        provider_denials=tuple(sorted(provider_denials)),
        capability_denials=tuple(sorted(capability_denials)),
        executor_authorizations=executor_authorizations(q),
        deliverables=deliverables,
        availability_reasons=explain(env),
        legacy_router_mode=decision.router_mode,
    )


def bounded_tool_context(messages: list, *, max_tokens: int, output_tokens: int = 0) -> tuple[list, dict]:
    """Bound each model view while preserving user constraints and recent steps.

    Full tool outputs stay in the ledger/result store. Only older tool bodies
    may shrink; ids and source locators survive. If protected content alone is
    too large, refuse another call rather than silently lose the requirements.
    """
    from src.core.context_budget import ContextBudget, _keep_identifiers
    from langchain_core.messages import ToolMessage
    budget = ContextBudget()
    limit = max(1, int(max_tokens) - max(0, int(output_tokens)))
    before = budget.estimate_tokens(messages)
    view = list(messages)
    if before > int(limit * 0.85):
        for index, message in enumerate(view[:-4]):
            if isinstance(message, ToolMessage) and len(str(message.content)) > 2500:
                view[index] = message.model_copy(update={
                    "content": _keep_identifiers(str(message.content), max_chars=1500)})
    after = budget.estimate_tokens(view)
    return view, {"estimated_input_tokens": after, "original_input_tokens": before,
                  "input_limit": limit, "tool_results_shortened": after < before,
                  "fits": after <= limit}


def merge_task_instructions(query: str, updates: list[dict]) -> str:
    """Keep task changes as explicit versioned user instructions, in order.

    Completed operation facts are retained by the execution ledger. The caller
    rebuilds the one RequestPlan before proposing any unfinished operation.
    """
    merged = str(query or "")
    for update in updates:
        merged += f"\n\n用户补充要求（任务版本 {int(update['version'])}）：\n{update['instruction']}"
    return merged


__all__ = [
    "SCHEMA_VERSION", "RequestFacts", "RequestPlan", "build_request_plan",
    "executor_authorizations", "explicit_note_save_request",
]
