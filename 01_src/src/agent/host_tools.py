"""Production operation/publishing adapters for the generic capability scheduler.

These adapters reuse account permissions, durable operation leases, artifact
verification and full-result storage. They do not own a model loop.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from langchain_core.messages import ToolMessage

from .execution import ToolExecution
from .identity import ExecutionIdentity


@dataclass
class HostTools:
    identity: ExecutionIdentity
    state: dict
    records: list[dict] = field(default_factory=list)
    messages: dict[str, ToolMessage] = field(default_factory=dict)
    dispatched: dict[str, str] = field(default_factory=dict)
    main_results: list = field(default_factory=list)
    web_results: list = field(default_factory=list)
    pending_question: dict | None = None
    allowed_names: set[str] = field(default_factory=set)

    def assert_context(self):
        from src.core import jobs, tracing
        from src.core.task_events import TaskConflict
        job = jobs.get_job(self.identity.run_id)
        if (tracing.get_job_id() != self.identity.run_id
                or tracing.get_user_id() != self.identity.owner_user_id
                or tracing.get_session_id() != self.identity.session_id
                or not job or job["owner_user_id"] != self.identity.owner_user_id
                or job["session_id"] != self.identity.session_id
                or job["request_id"] != self.identity.user_turn_id
                or job["status"] not in {"running", "detached"}):
            raise TaskConflict("agent_execution_identity_conflict")

    async def invoke(self, tool, call, identity):
        if identity != self.identity:
            raise ValueError("operation_identity_mismatch")
        self.assert_context()
        from src.tools.registry import _run_single_tool
        from src.core.execution_ledger import current_execution_ledger
        from src.core.progress_bus import emit_tool_call_start, emit_tool_executing
        from src.tools.interaction import bind_question_context, reset_question_context
        ledger = current_execution_ledger()
        if ledger:
            ledger.start(call["id"], call["name"], call["args"], source="provider")
        emit_tool_call_start(call["name"], call["args"], call["id"])
        emit_tool_executing("正在执行所选操作", call["name"], call["id"])
        token = bind_question_context(self.question_context())
        def dispatched():
            self.dispatched[call["id"]] = call["name"]
        try:
            message = await _run_single_tool(tool, call, require_durable=True,
                                             on_dispatch=dispatched)
        finally:
            reset_question_context(token)
        # Preserve the real observation even if publishing subsequently fails.
        self.messages[call["id"]] = message
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        question = artifact.get("user_question")
        if call["name"] == "request_user_input" and isinstance(question, dict):
            self.pending_question = question
        return ToolExecution(message,
            operation_id=str(message.additional_kwargs.get("operation_id") or ""),
            uncertain=bool(message.additional_kwargs.get("operation_uncertain")),
            pending_question=self.pending_question is not None)

    async def publish(self, call, execution, identity):
        if identity != self.identity:
            raise ValueError("publication_identity_mismatch")
        from src.graph.agent_operations import _emit_completed_tool_step, _capture_interaction_refs
        from src.core.tool_result_view import first_delivery
        message = execution.message
        self.messages[call["id"]] = message
        _emit_completed_tool_step(call, message, self.records)
        storage = message.additional_kwargs.get("result_storage") or {}
        if not storage.get("complete"):
            # Do not keep working from an observation we cannot read back.
            raise RuntimeError("operation_result_not_persisted")
        _capture_interaction_refs(self.state, [message])
        artifact = message.artifact if isinstance(message.artifact, dict) else {}
        self.main_results.extend(artifact.get("main_results") or [])
        self.web_results.extend(artifact.get("web_results") or [])
        delivered = first_delivery(message, call=call,
                                   reader_available="read_tool_result" in self.allowed_names)
        self.messages[call["id"]] = delivered
        return delivered

    def working_state(self):
        """References survive; historical queries do not become permanent constraints."""
        from src.graph.agent_operations import _task_working_state
        working = _task_working_state(self.state, str(self.state.get("query") or ""), self.records, [])
        # Legacy state already contains whole queries disguised as constraints.
        # Keep real scoped constraints, but don't perpetuate the legacy ':user'
        # entries; their original text remains in searchable session history.
        previous = self.state.get("context_working_state") or {}
        working["retired_constraint_ids"] = [item["source_id"] for item in previous.get("constraints", [])
            if isinstance(item, dict) and str(item.get("source_id") or "").endswith(":user")]
        working["constraints"] = [item for item in previous.get("constraints", [])
            if isinstance(item, dict) and not str(item.get("source_id") or "").endswith(":user")]
        return working

    def question_context(self):
        working = self.working_state()
        return {"query": str(self.state.get("query") or ""),
            "context_working_state": working,
            **{key: working.get(key, []) for key in ("source_refs", "dataset_refs", "script_refs", "run_refs")},
            "attachment_refs": self.state.get("attachment_refs") or [],
            "visualization_enabled": bool(self.state.get("visualization_enabled")),
            "tool_status": [dict(r) for r in self.records], "no_replay": True}
