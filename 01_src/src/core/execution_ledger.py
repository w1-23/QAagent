"""One ledger for every tool execution in a request.

Provider tool calls, DSML-recovered calls and delivery-gate calls all converge
on ``general_agent_graph._emit_completed_tool_step``.  That makes it the one
place where a terminal state can be registered without inventing a second
execution path.  The ledger keeps the identity (``tool_call_id``,
``logical_key``, ``attempt``), the terminal status and the artifact evidence,
and it can materialize the paired ``AIMessage.tool_calls`` + ``ToolMessage``
that the session store needs to reproduce a delivery-gate step after refresh.

It is deliberately request-scoped: the graph binds one ledger per request, and
no state survives into another request or another session.
"""
from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any

TERMINAL_STATUSES = frozenset({
    "complete", "partial", "no_results", "failed",
    "not_run", "skipped", "cancelled",
})
VALID_STATUSES = TERMINAL_STATUSES | {"running"}


def _now_ms() -> int:
    return int(time.time() * 1000)


@dataclass
class ExecutionRecord:
    """One tool call, from start to exactly one terminal state."""

    tool_call_id: str
    tool_name: str
    source: str = "provider"
    logical_key: str = ""
    attempt: int = 1
    args: dict[str, Any] = field(default_factory=dict)
    status: str = "running"
    reason_code: str = ""
    result_chars: int = 0
    artifact_validations: tuple[dict[str, Any], ...] = ()
    started_at_ms: int = 0
    completed_at_ms: int = 0
    persisted: bool = False
    duplicate_terminals: int = 0

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class ExecutionLedger:
    """Request-scoped registry with one terminal state per tool-call ID."""

    def __init__(self, *, session_id: str = "", request_id: str = ""):
        self.session_id = str(session_id or "")
        self.request_id = str(request_id or "")
        self._records: dict[str, ExecutionRecord] = {}
        self._order: list[str] = []
        self._lock = threading.Lock()

    # -- registration -----------------------------------------------------
    def start(self, tool_call_id: str, tool_name: str,
              args: dict | None = None, *, source: str = "provider",
              logical_key: str = "", attempt: int = 1) -> ExecutionRecord | None:
        call_id = str(tool_call_id or "")
        if not call_id:
            return None
        with self._lock:
            return self._start_locked(
                call_id, tool_name, args or {}, source=source,
                logical_key=logical_key, attempt=attempt)

    def _start_locked(self, call_id: str, tool_name: str, args: dict, *,
                      source: str, logical_key: str,
                      attempt: int) -> ExecutionRecord:
        existing = self._records.get(call_id)
        if existing is not None:
            # A duplicate start must not reset a call that already has a
            # terminal state; the first registration owns the identity.
            return existing
        record = ExecutionRecord(
            tool_call_id=call_id,
            tool_name=str(tool_name or ""),
            source=str(source or "provider"),
            logical_key=str(logical_key or ""),
            attempt=max(1, int(attempt or 1)),
            args=dict(args or {}),
            status="running",
            started_at_ms=_now_ms(),
        )
        self._records[call_id] = record
        self._order.append(call_id)
        return record

    def terminal(self, tool_call_id: str, status: str, *,
                 tool_name: str = "", reason_code: str = "",
                 result_chars: int = 0,
                 artifact_validations: tuple[dict, ...] | list[dict] = (),
                 source: str = "") -> ExecutionRecord | None:
        """Register the one terminal state; later calls are counted, not applied."""
        call_id = str(tool_call_id or "")
        if not call_id:
            return None
        normalized = status if status in TERMINAL_STATUSES else "failed"
        with self._lock:
            record = self._records.get(call_id)
            if record is None:
                record = self._start_locked(
                    call_id, tool_name, {}, source=source or "provider",
                    logical_key="", attempt=1)
            if record.status != "running":
                record.duplicate_terminals += 1
                return record
            record.status = normalized
            record.reason_code = str(reason_code or "")
            record.result_chars = max(0, int(result_chars or 0))
            record.artifact_validations = tuple(
                dict(item) for item in (artifact_validations or ())
                if isinstance(item, dict))
            if tool_name and not record.tool_name:
                record.tool_name = str(tool_name)
            record.completed_at_ms = _now_ms()
            return record

    # -- inspection -------------------------------------------------------
    def record(self, tool_call_id: str) -> ExecutionRecord | None:
        with self._lock:
            return self._records.get(str(tool_call_id or ""))

    def records(self) -> tuple[ExecutionRecord, ...]:
        with self._lock:
            return tuple(self._records[call_id] for call_id in self._order)

    def pending_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(call_id for call_id in self._order
                         if self._records[call_id].status == "running")

    def close(self, *, status: str = "failed",
              reason_code: str = "terminal_missing") -> tuple[str, ...]:
        """Give every still-running call one honest terminal state.

        This is the last-resort path for a request that ended (user stop,
        cancellation or an unexpected exception) after a start event but
        before any result existed.  It never invents a successful outcome.
        """
        normalized = status if status in TERMINAL_STATUSES else "failed"
        closed: list[str] = []
        with self._lock:
            for call_id in self._order:
                record = self._records[call_id]
                if record.status != "running":
                    continue
                record.status = normalized
                record.reason_code = str(reason_code or "")
                record.completed_at_ms = _now_ms()
                closed.append(call_id)
        return tuple(closed)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            records = [self._records[call_id] for call_id in self._order]
            counts: dict[str, int] = {}
            for record in records:
                counts[record.status] = counts.get(record.status, 0) + 1
            return {
                "session_id": self.session_id,
                "request_id": self.request_id,
                "total": len(records),
                "counts": counts,
                "duplicate_terminals": sum(
                    record.duplicate_terminals for record in records),
                "pending": [record.tool_call_id for record in records
                            if record.status == "running"],
            }

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "request_id": self.request_id,
            "records": [record.as_dict() for record in self.records()],
        }

    # -- persistence ------------------------------------------------------
    def persist_pair(self, record: ExecutionRecord | None, *,
                     content: str, artifact: dict | None = None) -> list:
        """Build the paired history messages for a call that has no AI row.

        A delivery-gate call is executed by the graph, not proposed by the
        model, so the turn trace contains no ``AIMessage.tool_calls`` for it.
        Without this pair ``_validate_trace`` drops the result as an orphan and
        a refresh loses a step that really ran.  The pair is generated once per
        ledger record.
        """
        if record is None or record.persisted:
            return []
        from langchain_core.messages import AIMessage, ToolMessage

        call = {
            "id": record.tool_call_id,
            "name": record.tool_name,
            "args": dict(record.args or {}),
            "type": "tool_call",
        }
        ai_message = AIMessage(content="", tool_calls=[call])
        tool_message = ToolMessage(
            content=str(content or ""),
            tool_call_id=record.tool_call_id,
            name=record.tool_name,
            artifact=dict(artifact or {}),
        )
        record.persisted = True
        return [ai_message, tool_message]


_ledger_var: contextvars.ContextVar[ExecutionLedger | None] = contextvars.ContextVar(
    "citrus_execution_ledger", default=None)


def bind_execution_ledger(ledger: ExecutionLedger):
    return _ledger_var.set(ledger)


def reset_execution_ledger(token) -> None:
    try:
        _ledger_var.reset(token)
    except (ValueError, RuntimeError):
        _ledger_var.set(None)


def current_execution_ledger() -> ExecutionLedger | None:
    return _ledger_var.get()
