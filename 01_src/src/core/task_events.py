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


class TaskConflict(ValueError):
    pass


def init_schema(conn):
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
                "terminal_snapshot": decode_terminal_snapshot(job)}


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
        if budget and not __import__('math').isfinite(budget["deadline"]):
            budget["deadline"] = None
        return {"event_cursor": conn.execute("SELECT COALESCE(MAX(seq),0) FROM task_events WHERE job_id=?", (job_id,)).fetchone()[0],
                "task_version": updates[-1]["version"] if updates else 0,
                "applied_version": max((u["version"] for u in updates if u["status"] == "applied"), default=0),
                "pending_updates": [u for u in updates if u["status"] == "pending"],
                "operations": operations,
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


def start_operation(job_id, owner, tool_name, args, *, recovery_mode="never", input_refs=None, lease_seconds=600):
    refs = input_refs or []
    operation_id, lease = uuid.uuid4().hex, uuid.uuid4().hex
    digest = input_hash({"tool": tool_name, "args": args, "refs": refs})
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["owner_user_id"] != owner or job["status"] not in {"running", "detached"}:
            raise TaskConflict("operation_owner_or_state_conflict")
        conn.execute("INSERT INTO task_operations (operation_id,job_id,owner_user_id,tool_name,input_hash,inputs,input_refs,recovery_mode,status,lease,lease_until,attempts,updated_at) VALUES (?,?,?,?,?,?,?,?,'running',?,?,1,?)",
                     (operation_id, job_id, owner, tool_name, digest, canonical(args), canonical(refs), recovery_mode, lease, time.time()+lease_seconds, time.time()))
        _append(conn, job_id, "operation", {"operation_id": operation_id, "tool_name": tool_name, "status": "running", "input_hash": digest})
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
            if isinstance(data.get("facts"), dict):
                details["facts"] = data["facts"]
            if isinstance(data.get("result_refs"), list):
                details["result_refs"] = data["result_refs"]
    return details


def finish_operation(operation_id, lease, message):
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
        _append(conn, row["job_id"], "operation", {"operation_id": operation_id, "tool_name": row["tool_name"], "status": status, "outcome": outcome})
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
    primary = getattr(model, "primary", model)
    base = getattr(primary, "bound", primary)
    output = int(output_tokens or getattr(base, "max_tokens", None) or 512)
    estimated = sum(max(1, len(str(getattr(m, "content", m))) // 2) for m in messages) + output
    attempt_id = reserve_attempt(get_job_id(), purpose, estimated)
    scope_token = _active_model_attempt.set(attempt_id)
    try:
        remaining = remaining_seconds(get_job_id())
        response = (await invoke() if remaining == float("inf") else
                    await asyncio.wait_for(invoke(), timeout=remaining))
    except BaseException as exc:
        finish_attempt(attempt_id, error=type(exc).__name__)
        raise
    finally:
        _active_model_attempt.reset(scope_token)
    finish_attempt(attempt_id, extract_usage(response) or {})
    emit_usage_from_response(usage_session_id or get_session_id(), purpose, response)
    return response


async def counted_ainvoke(model, messages, *, purpose, usage_session_id=None, **kwargs):
    return await run_model_attempt(model, messages,
        lambda: model.ainvoke(messages, **kwargs), purpose=purpose,
        output_tokens=kwargs.get("max_tokens"), usage_session_id=usage_session_id)


def init_budget(job_id, deadline, max_calls=64, max_tokens=250000):
    with transaction() as conn:
        _job(conn, job_id)
        conn.execute("INSERT OR IGNORE INTO task_budgets(job_id,deadline,max_calls,max_tokens) VALUES (?,?,?,?)", (job_id, deadline, max_calls, max_tokens))


def reserve_attempt(job_id, purpose, estimated_tokens):
    """Reserve every attempt, including failures with unknown provider usage."""
    if not job_id:
        return ""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return ""
        tokens = max(1, int(estimated_tokens))
        if row["deadline"] <= time.time() or row["calls"] >= row["max_calls"] or row["tokens"] + tokens > row["max_tokens"]:
            raise TaskConflict("model_budget_exhausted")
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
    measured = int(usage.get("total_tokens") or (int(usage.get("input_tokens") or 0)+int(usage.get("output_tokens") or 0)))
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_model_attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
        if row is None or row["status"] != "running":
            return
        # Unknown usage retains the reservation instead of silently charging zero.
        if measured:
            conn.execute("UPDATE task_budgets SET tokens=tokens+? WHERE job_id=?", (measured-row["reserved_tokens"], row["job_id"]))
        conn.execute("UPDATE task_model_attempts SET status=?,usage=?,error=? WHERE attempt_id=?", ("failed" if error else "complete", canonical(usage), error, attempt_id))
        _append(conn, row["job_id"], "model_attempt", {"attempt_id": attempt_id,
            "purpose": row["purpose"], "status": "failed" if error else "complete",
            "usage": usage, "usage_status": "reported" if measured else "estimated",
            "charged_tokens": measured or row["reserved_tokens"], "error": error})


def remaining_seconds(job_id):
    if not job_id:
        return float("inf")
    with transaction() as conn:
        row = conn.execute("SELECT deadline FROM task_budgets WHERE job_id=?", (job_id,)).fetchone()
        return max(0.0, row[0] - time.time()) if row else float("inf")
