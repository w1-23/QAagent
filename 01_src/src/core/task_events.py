"""Durable task protocol on the existing jobs database and coordinator.

Events are facts, queues are bounded wakeups. Recovery of a terminal view never
reopens a job; resuming one explicitly safe operation is a separate action.
"""
from __future__ import annotations

import asyncio
import contextvars
import hashlib
import json
import time
import uuid
from contextlib import closing, contextmanager

PROTOCOL = "citrus-task/1"
LIVE_QUEUE_SIZE = 128
_active_model_attempt = contextvars.ContextVar("task_model_attempt", default="")
_checkpoint_scope = contextvars.ContextVar("turn_checkpoint_scope", default=None)


class TaskConflict(ValueError):
    pass


def init_schema(conn):
    from src.core.user_questions import init_schema as init_questions
    init_questions(conn)
    for statement in (
        "CREATE TABLE IF NOT EXISTS task_events (job_id TEXT NOT NULL, seq INTEGER NOT NULL, event TEXT NOT NULL, data TEXT NOT NULL, created_at REAL NOT NULL, PRIMARY KEY(job_id,seq))",
        "CREATE INDEX IF NOT EXISTS idx_task_events_type ON task_events(job_id,event)",
        "CREATE TABLE IF NOT EXISTS task_updates (job_id TEXT NOT NULL, version INTEGER NOT NULL, instruction TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, applied_at REAL, PRIMARY KEY(job_id,version))",
        "CREATE TABLE IF NOT EXISTS task_approvals (approval_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, tool_name TEXT NOT NULL, args_hash TEXT NOT NULL, status TEXT NOT NULL, created_at REAL NOT NULL, decided_at REAL)",
        "CREATE TABLE IF NOT EXISTS task_operations (operation_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, owner_user_id INTEGER NOT NULL, tool_name TEXT NOT NULL, input_hash TEXT NOT NULL, inputs TEXT NOT NULL, input_refs TEXT NOT NULL, recovery_mode TEXT NOT NULL, status TEXT NOT NULL, lease TEXT NOT NULL, lease_until REAL NOT NULL, attempts INTEGER NOT NULL, result TEXT NOT NULL DEFAULT '', result_refs TEXT NOT NULL DEFAULT '[]', outcome TEXT NOT NULL DEFAULT '{}', updated_at REAL NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_task_operations_job ON task_operations(job_id)",
        "CREATE TABLE IF NOT EXISTS task_budgets (job_id TEXT PRIMARY KEY, deadline REAL NOT NULL, max_calls INTEGER NOT NULL, max_tokens INTEGER NOT NULL, calls INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0)",
        "CREATE TABLE IF NOT EXISTS task_model_attempts (attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, purpose TEXT NOT NULL, status TEXT NOT NULL, reserved_tokens INTEGER NOT NULL, usage TEXT NOT NULL DEFAULT '{}', error TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL)",
    ):
        conn.execute(statement)
    attempt_columns = {r[1] for r in conn.execute("PRAGMA table_info(task_model_attempts)")}
    if "budget_scope" not in attempt_columns:
        conn.execute("ALTER TABLE task_model_attempts ADD COLUMN budget_scope TEXT NOT NULL DEFAULT 'execution'")
    budget_columns = {r[1] for r in conn.execute("PRAGMA table_info(task_budgets)")}
    for name in ("final_tokens", "final_calls"):
        if name not in budget_columns:
            conn.execute(f"ALTER TABLE task_budgets ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0")


@contextmanager
def transaction():
    from src.core import jobs
    with closing(jobs._connect()) as conn, conn:
        conn.row_factory = __import__('sqlite3').Row
        conn.execute("BEGIN IMMEDIATE")
        yield conn


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def input_hash(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def _job(conn, job_id):
    row = conn.execute("SELECT * FROM task_jobs WHERE job_id=?", (job_id,)).fetchone()
    if row is None:
        raise TaskConflict("job_not_found")
    return row


def _append(conn, job_id, event, data):
    _job(conn, job_id)
    terminal = conn.execute("SELECT seq,event,data,created_at FROM task_events WHERE job_id=? AND event='done' ORDER BY seq LIMIT 1", (job_id,)).fetchone()
    if terminal is not None and event != "operation":
        # One terminal frame. Explicit safe-step recovery has its own operation
        # events and never alters the completed/interrupted turn's envelope.
        return {"seq": terminal[0], "event": terminal[1], "data": json.loads(terminal[2]), "created_at": terminal[3]}
    seq = conn.execute("SELECT COALESCE(MAX(seq),0)+1 FROM task_events WHERE job_id=?", (job_id,)).fetchone()[0]
    from src.core.progress_bus import stamp_sse_event, _encode_event
    frame = stamp_sse_event(_encode_event(event, data), seq)
    payload = json.loads(frame["data"])
    now = time.time()
    conn.execute("INSERT INTO task_events VALUES (?,?,?,?,?)", (job_id, seq, event, canonical(payload), now))
    if event == "done" and isinstance(payload.get("turn_envelope"), dict):
        conn.execute("UPDATE task_jobs SET terminal_snapshot=? WHERE job_id=?", (canonical(payload["turn_envelope"]), job_id))
    return {"seq": seq, "event": event, "data": payload, "created_at": now}


def append_event(job_id, event, data):
    with transaction() as conn:
        return _append(conn, job_id, event, data)


def _latest_context_snapshot(conn, job_id):
    row = conn.execute("SELECT seq,data FROM task_events WHERE job_id=? AND event='context_snapshot' ORDER BY seq DESC LIMIT 1", (job_id,)).fetchone()
    if not row:
        return None
    return dict(json.loads(row["data"]), seq=row["seq"])


def latest_context_snapshot(job_id):
    """Read the same identified event consumed by live SSE and replay."""
    with transaction() as conn:
        _job(conn, job_id)
        return _latest_context_snapshot(conn, job_id)


def replay(job_id, after=0, limit=200):
    after, limit = max(0, int(after)), min(500, max(1, int(limit)))
    with transaction() as conn:
        job = dict(_job(conn, job_id))
        rows = conn.execute("SELECT * FROM task_events WHERE job_id=? AND seq>? ORDER BY seq LIMIT ?", (job_id, after, limit + 1)).fetchall()
        events = [dict(row) | {"data": json.loads(row["data"])} for row in rows[:limit]]
        from src.core.jobs import decode_terminal_snapshot
        return {"protocol": PROTOCOL, "job_id": job_id, "events": events,
                "next_cursor": events[-1]["seq"] if events else after,
                "has_more": len(rows) > limit, "status": job["status"],
                "terminal_snapshot": decode_terminal_snapshot(job),
                "context_snapshot": _latest_context_snapshot(conn, job_id)}


def projection(job_id):
    with transaction() as conn:
        job = _job(conn, job_id)
        updates = [dict(r) for r in conn.execute("SELECT * FROM task_updates WHERE job_id=? ORDER BY version", (job_id,))]
        operations = []
        for row in conn.execute("SELECT * FROM task_operations WHERE job_id=? ORDER BY updated_at", (job_id,)):
            item = dict(row)
            item["result_available"] = bool(item.get("result"))
            item["result_url"] = f"/jobs/{job_id}/operations/{item['operation_id']}/result" if item["result_available"] else ""
            for key in ("inputs", "lease", "result"):
                item.pop(key, None)
            for key in ("input_refs", "result_refs", "outcome"):
                item[key] = json.loads(item[key])
            item["resumable"] = (job["current_step"] == "interrupted"
                                 and job["status"] == "failed"
                                 and item["recovery_mode"] != "never" and item["status"] == "interrupted")
            operations.append(item)
        budget_row = conn.execute("SELECT * FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        budget = dict(budget_row) if budget_row else {}
        if budget:
            budget.update(_usage_totals(conn, job_id))
        if budget and not __import__('math').isfinite(budget["deadline"]):
            budget["deadline"] = None
        return {"event_cursor": conn.execute("SELECT COALESCE(MAX(seq),0) FROM task_events WHERE job_id=?", (job_id,)).fetchone()[0],
                "task_version": updates[-1]["version"] if updates else 0,
                "applied_version": max((u["version"] for u in updates if u["status"] == "applied"), default=0),
                "pending_updates": [u for u in updates if u["status"] == "pending"],
                "operations": operations,
                "context_snapshot": _latest_context_snapshot(conn, job_id),
                "approvals": [dict(r) for r in conn.execute("SELECT * FROM task_approvals WHERE job_id=? ORDER BY created_at", (job_id,))],
                "budget": budget}


def submit_update(job_id, expected_version, instruction):
    instruction = str(instruction).strip()
    if not instruction or len(instruction) > 8000:
        raise ValueError("instruction must contain 1 to 8000 characters")
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["status"] not in {"running", "detached"} or job["current_step"] == "finalizing":
            raise TaskConflict("task_not_running")
        version = conn.execute("SELECT COALESCE(MAX(version),0) FROM task_updates WHERE job_id=?", (job_id,)).fetchone()[0]
        if expected_version != version:
            raise TaskConflict("task_version_conflict")
        version += 1
        conn.execute("INSERT INTO task_updates VALUES (?,?,?,'pending',?,NULL)", (job_id, version, instruction, time.time()))
        _append(conn, job_id, "task_update", {"version": version, "status": "pending", "instruction": instruction})
        return {"job_id": job_id, "version": version, "status": "pending"}


def apply_updates(job_id):
    if not job_id:
        return []
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["status"] not in {"running", "detached"}:
            return []
        rows = [dict(r) for r in conn.execute("SELECT * FROM task_updates WHERE job_id=? AND status='pending' ORDER BY version", (job_id,))]
        for row in rows:
            conn.execute("UPDATE task_updates SET status='applied',applied_at=? WHERE job_id=? AND version=?", (time.time(), job_id, row["version"]))
            _append(conn, job_id, "task_update", {"version": row["version"], "status": "applied", "instruction": row["instruction"]})
        return rows


def create_approval(job_id, tool_name, args):
    approval_id = uuid.uuid4().hex
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["status"] not in {"running", "detached"}:
            raise TaskConflict("task_not_running")
        conn.execute("INSERT INTO task_approvals VALUES (?,?,?,?,'pending',?,NULL)", (approval_id, job_id, tool_name, input_hash(args), time.time()))
    return approval_id


def decide_approval(job_id, approval_id, decision):
    if decision not in {"granted", "rejected", "expired", "cancelled"}:
        raise ValueError("invalid approval decision")
    with transaction() as conn:
        job = _job(conn, job_id)
        row = conn.execute("SELECT * FROM task_approvals WHERE approval_id=? AND job_id=?", (approval_id, job_id)).fetchone()
        if row is None:
            raise TaskConflict("approval_not_found")
        if row["status"] != "pending":
            if row["status"] != decision:
                raise TaskConflict("approval_already_decided")
            return dict(row)
        if decision == "granted" and job["status"] not in {"running", "detached"}:
            raise TaskConflict("task_not_running")
        conn.execute("UPDATE task_approvals SET status=?,decided_at=? WHERE approval_id=?", (decision, time.time(), approval_id))
        _append(conn, job_id, "permission_decision", {"approval_id": approval_id, "tool_name": row["tool_name"], "status": decision})
        return dict(row) | {"status": decision}


def approval_status(approval_id):
    with transaction() as conn:
        row = conn.execute("SELECT status FROM task_approvals WHERE approval_id=?", (approval_id,)).fetchone()
        return row[0] if row else "missing"


def start_operation(job_id, owner, tool_name, args, *, recovery_mode="never", input_refs=None, lease_seconds=600, tool_call_id="", attempt=1):
    refs = input_refs or []
    operation_id, lease = uuid.uuid4().hex, uuid.uuid4().hex
    digest = input_hash({"tool": tool_name, "args": args, "refs": refs})
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["owner_user_id"] != owner or job["status"] not in {"running", "detached"}:
            raise TaskConflict("operation_owner_or_state_conflict")
        conn.execute("INSERT INTO task_operations (operation_id,job_id,owner_user_id,tool_name,input_hash,inputs,input_refs,recovery_mode,status,lease,lease_until,attempts,updated_at) VALUES (?,?,?,?,?,?,?,?,'running',?,?,1,?)",
                     (operation_id, job_id, owner, tool_name, digest, canonical(args), canonical(refs), recovery_mode, lease, time.time()+lease_seconds, time.time()))
        _append(conn, job_id, "operation", {"operation_id": operation_id, "tool_name": tool_name, "status": "running", "input_hash": digest, "tool_call_id": tool_call_id, "attempt": attempt})
    return operation_id, lease


def result_details(content, artifact):
    """Carry adapter-owned structured facts; never derive facts from prose."""
    sources = [artifact] if isinstance(artifact, dict) else []
    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict):
            sources.append(payload)
    details = {}
    for source in sources:
        explicit = source.get("_tool_outcome")
        for data in (source, explicit if isinstance(explicit, dict) else {}):
            if isinstance(data.get("facts"), (dict, list)):
                details["facts"] = data["facts"]
            if isinstance(data.get("result_refs"), list):
                details["result_refs"] = data["result_refs"]
    return details


def finish_operation(operation_id, lease, message, *, attempt=1):
    from src.tools.outcome import safe_tool_step
    content = str(message.content or "")
    artifact = message.artifact if isinstance(message.artifact, dict) else {}
    outcome = safe_tool_step(message.name or "", content, artifact)
    outcome.update(result_details(content, artifact))
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_operations WHERE operation_id=?", (operation_id,)).fetchone()
        if row is None or row["lease"] != lease or row["status"] != "running" or row["lease_until"] < time.time():
            raise TaskConflict("operation_lease_lost")
        job = _job(conn, row["job_id"])
        # Resume commits are independent of the original interrupted terminal view.
        resuming = (job["status"] == "failed" and job["current_step"] == "interrupted"
                    and row["attempts"] > 1 and row["recovery_mode"] != "never")
        if job["status"] not in {"running", "detached"} and not resuming:
            raise TaskConflict("operation_cancelled_or_terminal")
        status = str(outcome.get("status") or "failed")
        result = canonical({"content": content, "artifact": artifact})
        conn.execute("UPDATE task_operations SET status=?,result=?,result_refs=?,outcome=?,lease_until=0,updated_at=? WHERE operation_id=?", (status, result, canonical(outcome.get("result_refs") or outcome.get("artifacts") or []), canonical(outcome), time.time(), operation_id))
        _append(conn, row["job_id"], "operation", {"operation_id": operation_id, "tool_name": row["tool_name"], "status": status, "outcome": outcome, "tool_call_id": message.tool_call_id, "attempt": attempt})
    return outcome


def abandon_operation(operation_id, lease):
    with transaction() as conn:
        conn.execute("UPDATE task_operations SET status='interrupted',lease_until=0,updated_at=? WHERE operation_id=? AND lease=? AND status='running'", (time.time(), operation_id, lease))


def operation_result(job_id, operation_id):
    with transaction() as conn:
        _job(conn, job_id)
        row = conn.execute("SELECT result,outcome,input_hash,result_refs FROM task_operations WHERE job_id=? AND operation_id=?", (job_id, operation_id)).fetchone()
        if row is None or not row["result"]:
            raise TaskConflict("operation_result_unavailable")
        return {"job_id": job_id, "operation_id": operation_id,
                "input_hash": row["input_hash"], "result": json.loads(row["result"]),
                "outcome": json.loads(row["outcome"]), "result_refs": json.loads(row["result_refs"])}


def claim_resume(job_id, operation_id, owner):
    with transaction() as conn:
        job = _job(conn, job_id)
        row = conn.execute("SELECT * FROM task_operations WHERE operation_id=? AND job_id=? AND owner_user_id=?", (operation_id, job_id, owner)).fetchone()
        if (row is None or job["owner_user_id"] != owner or job["current_step"] != "interrupted"
                or row["recovery_mode"] == "never" or row["status"] != "interrupted"):
            raise TaskConflict("unsafe_or_unavailable_resume")
        lease = uuid.uuid4().hex
        conn.execute("UPDATE task_operations SET status='running',lease=?,lease_until=?,attempts=attempts+1,updated_at=? WHERE operation_id=?", (lease, time.time()+600, time.time(), operation_id))
        _append(conn, job_id, "operation", {"operation_id": operation_id, "status": "resuming", "recovery": "safe_step"})
        return dict(row) | {"lease": lease, "inputs": json.loads(row["inputs"]), "input_refs": json.loads(row["input_refs"])}


def recover_interrupted(conn):
    """Called inside jobs' startup reconciliation transaction."""
    for job_id, approval_id, tool_name in conn.execute("SELECT job_id,approval_id,tool_name FROM task_approvals WHERE status='pending'").fetchall():
        _append(conn, job_id, "permission_decision", {"approval_id": approval_id, "tool_name": tool_name, "status": "expired"})
    for job_id, version in conn.execute("SELECT job_id,version FROM task_updates WHERE status='pending'").fetchall():
        _append(conn, job_id, "task_update", {"version": version, "status": "not_applied", "reason": "service_restart"})
    conn.execute("UPDATE task_updates SET status='not_applied' WHERE status='pending'")
    conn.execute("UPDATE task_operations SET status='interrupted',lease_until=0 WHERE status='running'")
    conn.execute("UPDATE task_approvals SET status='expired',decided_at=? WHERE status='pending'", (time.time(),))
    from src.core.turn_envelope import build_turn_envelope
    for (job_id,) in conn.execute("SELECT job_id FROM task_jobs WHERE current_step='interrupted' AND status='failed' AND error='service_restart' AND NOT EXISTS (SELECT 1 FROM task_events e WHERE e.job_id=task_jobs.job_id AND e.event='done')").fetchall():
        envelope = build_turn_envelope(requested_status="failed", display_status="failed",
            job_status="failed", stop_reason="service_restart", history_saved=False,
            notices=["服务进程在任务完成前停止；可恢复已验证的安全步骤。"])
        _append(conn, job_id, "done", {"job_id": job_id, "turn_envelope": envelope.as_dict(),
                                       "recovery": "interrupted_terminal"})


class DurableEventQueue(asyncio.Queue):
    """Persist before notification; bounded queues never hold execution hostage."""
    def __init__(self, job_id, maxsize=LIVE_QUEUE_SIZE):
        super().__init__(maxsize=maxsize)
        self.job_id = job_id
        try:
            self._event_loop = asyncio.get_running_loop()
        except RuntimeError:
            self._event_loop = None

    def put_nowait(self, item):
        if item is not None and "id" not in item:
            data = item.get("data") or {}
            if isinstance(data, str):
                data = json.loads(data)
            record = append_event(self.job_id, str(item.get("event") or "message"), data)
            from src.core.progress_bus import _encode_event
            item = _encode_event(record["event"], record["data"]) | {"id": str(record["seq"])}
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None
        if self._event_loop is not None and current_loop is not self._event_loop:
            if not self._event_loop.is_closed():
                self._event_loop.call_soon_threadsafe(self._notify, item)
            return
        self._notify(item)

    def _notify(self, item):
        if self.full():
            self.get_nowait()
        super().put_nowait(item)

    async def put(self, item):
        self.put_nowait(item)


async def run_model_attempt(model, messages, invoke, *, purpose="supervisor", output_tokens=None, usage_session_id=None):
    from src.core.tracing import get_job_id, get_session_id
    from src.core.cache_metrics import extract_usage, emit_usage_from_response
    from src.core.request_context import model_request, _observation, _epoch, _logical_call
    from src.config import settings
    from src.core.context_snapshot import build_context_snapshot, emit_context_snapshot
    phase = _epoch.get()
    parent = _observation.get()
    # A fallback is a new physical attempt of the same logical call. Publish
    # the rejected parent before the fallback, not after its successful result.
    if parent and parent.get("redirected") and not parent.get("context_settled"):
        publisher = parent.get("publish_context")
        if publisher:
            publisher(status="failed", error=parent.get("transport_error", ""))
            parent["context_settled"] = True
    logical_call_id = _logical_call.get() or (parent or {}).get("logical_call_id") or uuid.uuid4().hex
    payload, estimate = model_request(model, messages, output_tokens=output_tokens)
    limit = max(0, min(int(settings.CONTEXT_ACTIVE_MAX_INPUT_TOKENS),
                int(settings.RESOLVED_CONTEXT_BUDGET_MAX_TOKENS) - estimate["output_tokens"] - int(settings.CONTEXT_SAFETY_MARGIN_TOKENS)))
    scope = {"purpose": purpose, "attempt_id": "", "logical_call_id": logical_call_id,
             "epoch": phase.number if phase else 0,
             "change_reason": "protocol_fallback" if parent and parent.get("redirected") else phase.reason if phase else "initial",
             "history_load_status": phase.history_load_status if phase else "unknown",
             "estimate": estimate, "model": str(payload.get("model", "")),
             "parent_purpose": parent.get("purpose", "") if parent else ""}

    def publish_context(*, usage=None, status="running", error=""):
        snapshot = build_context_snapshot(scope["estimate"],
            logical_call_id=logical_call_id, attempt_id=scope["attempt_id"], purpose=purpose,
            model=scope["model"], epoch=scope["epoch"], change_reason=scope["change_reason"],
            history_load_status=scope["history_load_status"], input_limit=limit,
            usage=usage, status=status, error=error)
        snapshot["parent_purpose"] = scope["parent_purpose"]
        scope["context_snapshot"] = snapshot
        emit_context_snapshot(snapshot)

    scope["publish_context"] = publish_context
    try:
        if estimate["estimated_input_tokens"] > limit:
            raise TaskConflict("context_budget_exhausted")
        attempt_id = reserve_attempt(get_job_id(), purpose, estimate["estimated_total_tokens"])
    except TaskConflict as exc:
        publish_context(status="not_admitted", error=str(exc))
        raise
    scope["attempt_id"] = attempt_id or uuid.uuid4().hex
    publish_context()
    scope_token = _active_model_attempt.set(attempt_id)
    observation_token = _observation.set(scope)
    response, error = None, ""
    try:
        maintenance = _checkpoint_scope.get()
        remaining = (max(0.0, maintenance["deadline"] - time.monotonic())
                     if maintenance else remaining_seconds(get_job_id()))
        response = (await invoke() if remaining == float("inf") else
                    await asyncio.wait_for(invoke(), timeout=remaining))
        return response
    except BaseException as exc:
        error = type(exc).__name__
        raise
    finally:
        redirected = scope.get("redirected", False)
        usage = {} if redirected else extract_usage(response) or {}
        error = scope.get("transport_error", error)
        finish_attempt(attempt_id, usage, error=error)
        if response is not None and not redirected:
            emit_usage_from_response(usage_session_id or get_session_id(), purpose, response,
                attempt_id=scope["attempt_id"], logical_call_id=scope["logical_call_id"],
                epoch=scope["epoch"], parent_purpose=scope["parent_purpose"])
        if not scope.get("context_settled"):
            publish_context(usage=usage, status="cancelled" if error == "CancelledError" else "failed" if error else "complete", error=error)
            scope["context_settled"] = True
        _observation.reset(observation_token)
        _active_model_attempt.reset(scope_token)
        snapshot = scope.get("snapshot")
        if snapshot:
            snapshot.update(estimate=scope["estimate"], parent_purpose=scope["parent_purpose"],
                            context_snapshot=scope.get("context_snapshot"))
            try:
                from src.core import turn_context
                from src.core.progress_bus import get_turn_scope
                from src.core.account_leases import current_account_lease, run_account_thread
                sid, rid = get_turn_scope()
                sid = sid or usage_session_id or get_session_id()
                if sid:
                    lease = current_account_lease()
                    write = (run_account_thread(lease, turn_context.record, snapshot,
                                session_id=sid, request_id=rid, usage=usage, error=error)
                             if lease is not None else asyncio.to_thread(turn_context.record, snapshot,
                                session_id=sid, request_id=rid, usage=usage, error=error))
                    await asyncio.wait_for(write, timeout=1.0)
            except Exception:
                pass


async def counted_ainvoke(model, messages, *, purpose, usage_session_id=None, **kwargs):
    token = None
    if purpose == "turn_checkpoint" and _checkpoint_scope.get() is None:
        token = _checkpoint_scope.set({"key": "checkpoint:" + uuid.uuid4().hex,
                                       "deadline": time.monotonic() + 30.0})
    try:
        return await run_model_attempt(model, messages,
            lambda: model.ainvoke(messages, **kwargs), purpose=purpose,
            output_tokens=kwargs.get("max_tokens"), usage_session_id=usage_session_id)
    finally:
        if token is not None:
            _checkpoint_scope.reset(token)


def _reserve_checkpoint_attempt(job_id, purpose, estimated_tokens, scope):
    """A single bounded maintenance group per job, metered independently."""
    from src.config import settings
    if not job_id or time.monotonic() >= scope["deadline"]:
        raise TaskConflict("checkpoint_budget_unavailable")
    tokens = max(1, int(estimated_tokens))
    with transaction() as conn:
        _job(conn, job_id)
        rows = conn.execute("SELECT * FROM task_model_attempts WHERE job_id=? AND budget_scope LIKE 'checkpoint:%'",
                            (job_id,)).fetchall()
        if any(row["budget_scope"] != scope["key"] for row in rows) or len(rows) >= 2:
            raise TaskConflict("checkpoint_attempt_limit")
        spent = 0
        for row in rows:
            usage = json.loads(row["usage"] or "{}")
            measured = usage.get("total_tokens")
            if measured is None and all(usage.get(k) is not None for k in ("input_tokens", "output_tokens")):
                measured = int(usage["input_tokens"]) + int(usage["output_tokens"])
            spent += max(0, int(measured)) if measured is not None else row["reserved_tokens"]
        if spent + tokens > settings.CONTEXT_CHECKPOINT_MAX_TOKENS:
            raise TaskConflict("checkpoint_token_limit")
        attempt_id = uuid.uuid4().hex
        conn.execute("INSERT INTO task_model_attempts(attempt_id,job_id,purpose,status,reserved_tokens,created_at,budget_scope) VALUES (?,?,?,'running',?,?,?)",
                     (attempt_id, job_id, purpose, tokens, time.time(), scope["key"]))
        return attempt_id


def init_budget(job_id, deadline, max_calls=64, max_tokens=None):
    if max_tokens is None:
        from src.config import settings
        max_tokens = settings.AGENT_MAX_MODEL_TOKENS
    with transaction() as conn:
        _job(conn, job_id)
        conn.execute("INSERT OR IGNORE INTO task_budgets(job_id,deadline,max_calls,max_tokens) VALUES (?,?,?,?)", (job_id, deadline, max_calls, max_tokens))


def reserve_attempt(job_id, purpose, estimated_tokens):
    """Reserve every attempt, including failures with unknown provider usage."""
    maintenance = _checkpoint_scope.get()
    if maintenance is not None:
        return _reserve_checkpoint_attempt(job_id, purpose, estimated_tokens, maintenance)
    if not job_id:
        return ""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return ""
        tokens = max(1, int(estimated_tokens))
        # Actual exhaustion takes precedence over protected headroom.
        if row["deadline"] <= time.time():
            raise TaskConflict("model_budget_exhausted:deadline")
        if row["calls"] >= row["max_calls"]:
            raise TaskConflict(f"model_budget_exhausted:calls ({row['calls']}/{row['max_calls']})")
        if row["tokens"] + tokens > row["max_tokens"]:
            raise TaskConflict(f"model_budget_exhausted:tokens ({row['tokens']}+{tokens}>{row['max_tokens']})")
        if not str(purpose).startswith("final_synthesis"):
            from src.core.request_context import _observation
            parent = _observation.get() or {}
            is_final_fallback = str(parent.get("purpose", "")).startswith("final_synthesis")
            if not is_final_fallback and (row["calls"] + 1 + row["final_calls"] > row["max_calls"]
                    or row["tokens"] + tokens + row["final_tokens"] > row["max_tokens"]):
                if row["final_calls"] or row["final_tokens"]:
                    raise TaskConflict("model_budget_reserved:finalization")
        attempt_id = uuid.uuid4().hex
        conn.execute("UPDATE task_budgets SET calls=calls+1,tokens=tokens+? WHERE job_id=?", (tokens, job_id))
        conn.execute("INSERT INTO task_model_attempts(attempt_id,job_id,purpose,status,reserved_tokens,created_at) VALUES (?,?,?,'running',?,?)", (attempt_id, job_id, purpose, tokens, time.time()))
        _append(conn, job_id, "model_attempt", {"attempt_id": attempt_id, "purpose": purpose,
                "status": "running", "reserved_tokens": tokens})
        return attempt_id


def finish_attempt(attempt_id, usage=None, error=""):
    if not attempt_id:
        return
    usage = usage or {}
    reported = usage.get("total_tokens") is not None or all(
        usage.get(key) is not None for key in ("input_tokens", "output_tokens"))
    measured = max(0, int(usage.get("total_tokens") if usage.get("total_tokens") is not None else
                   int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)))
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_model_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None or row["status"] != "running":
            return
        # Unknown usage retains the reservation instead of silently charging zero.
        if reported and row["budget_scope"] == "execution":
            conn.execute("UPDATE task_budgets SET tokens=tokens+? WHERE job_id=?", (measured-row["reserved_tokens"], row["job_id"]))
        conn.execute("UPDATE task_model_attempts SET status=?,usage=?,error=? WHERE attempt_id=?", ("failed" if error else "complete", canonical(usage), error, attempt_id))
        _append(conn, row["job_id"], "model_attempt", {"attempt_id": attempt_id,
            "purpose": row["purpose"], "status": "failed" if error else "complete",
            "usage": usage, "usage_status": "reported" if reported else "unknown_reserved",
            "charged_tokens": measured if reported else row["reserved_tokens"], "error": error})


def _usage_totals(conn, job_id):
    totals = {"reported_input_tokens": 0, "reported_output_tokens": 0,
              "reported_total_tokens": 0, "unknown_reserved_tokens": 0,
              "reported_attempts": 0, "unknown_attempts": 0,
              "in_flight_reserved_tokens": 0, "in_flight_attempts": 0}
    # Physical attempts retain their original job identity across continuations;
    # the logical task's reported usage includes every closed ancestor segment.
    for row in conn.execute("WITH RECURSIVE lineage(job_id) AS (SELECT ? UNION SELECT j.parent_job_id FROM task_jobs j JOIN lineage l ON j.job_id=l.job_id WHERE j.parent_job_id<>'') SELECT usage,reserved_tokens,status FROM task_model_attempts WHERE job_id IN (SELECT job_id FROM lineage)", (job_id,)):
        usage = json.loads(row["usage"] or "{}")
        if row["status"] == "running":
            totals["in_flight_attempts"] += 1
            totals["in_flight_reserved_tokens"] += int(row["reserved_tokens"])
        elif usage.get("total_tokens") is not None or all(
                usage.get(key) is not None for key in ("input_tokens", "output_tokens")):
            totals["reported_attempts"] += 1
            totals["reported_input_tokens"] += int(usage.get("input_tokens") or 0)
            totals["reported_output_tokens"] += int(usage.get("output_tokens") or 0)
            totals["reported_total_tokens"] += int(usage.get("total_tokens") or
                int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0))
        else:
            totals["unknown_attempts"] += 1
            totals["unknown_reserved_tokens"] += int(row["reserved_tokens"])
    return totals


def protect_finalization(job_id, tokens, calls=1):
    """Keep enough remaining calls and tokens for a bounded final receipt."""
    if not job_id:
        return
    with transaction() as conn:
        row = conn.execute("SELECT max_calls FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        if row:
            conn.execute("UPDATE task_budgets SET final_tokens=?,final_calls=? WHERE job_id=?",
                         (max(0, int(tokens)), min(max(0, int(calls)), row[0]), job_id))


def release_finalization(job_id):
    if job_id:
        with transaction() as conn:
            conn.execute("UPDATE task_budgets SET final_tokens=0,final_calls=0 WHERE job_id=?", (job_id,))


def remaining_seconds(job_id):
    if not job_id:
        return float("inf")
    with transaction() as conn:
        row = conn.execute("SELECT deadline FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        return max(0.0, row[0] - time.time()) if row else float("inf")
