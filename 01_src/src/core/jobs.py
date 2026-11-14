"""Task jobs — 长任务最小闭环状态表（v8.3.7 M2）。

write 类长任务断连后保活执行，状态落 task_jobs 表，前端/接口可查询。
不做完整任务中心（无队列/优先级/暂停恢复/多节点）。

S1 增补（取消生命周期）
----------------------
* 状态机从四态扩为六态：`running / detached / cancel_requested / completed / failed / cancelled`；
* `detached` = 观察者离开但执行继续，**永不**被当作 user cancel；
* `cancel_requested` 与终态分离：前者只表示"已收到停止请求"，不代表线程已退出；
* 终态不可回退（`update_job` 内置守卫，除非显式 `force=True`）；
* 新增列走幂等迁移（SQLite 无 `ADD COLUMN IF NOT EXISTS`），重复执行无副作用。
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from src.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

_DB_PATH = PROJECT_ROOT / "state" / "sessions.db"
_write_lock = threading.Lock()

TERMINAL_STATUS = frozenset({"completed", "failed", "cancelled"})
OPEN_STATUS = frozenset({"running", "detached", "cancel_requested"})
VALID_STATUS = OPEN_STATUS | TERMINAL_STATUS

# 幂等迁移：(列名, 声明)
_MIGRATIONS = (
    ("cancel_requested_at", "TEXT NOT NULL DEFAULT ''"),
    ("detached_at", "TEXT NOT NULL DEFAULT ''"),
    ("stop_reason", "TEXT NOT NULL DEFAULT ''"),
    ("stop_note", "TEXT NOT NULL DEFAULT ''"),
    # Minimal terminal snapshot for detached recovery: status, display state,
    # history_saved, notices and verified artifact count.  It never contains
    # the answer body (that stays in session history) and never claims work
    # that has not finished.
    ("terminal_snapshot", "TEXT NOT NULL DEFAULT ''"),
    # Legacy rows have no owner/key and remain independent requests.
    ("owner_user_id", "INTEGER"),
    ("client_request_id", "TEXT NOT NULL DEFAULT ''"),
    ("payload_hash", "TEXT NOT NULL DEFAULT ''"),
)


def _migrate(conn) -> None:
    """补齐 S1 新增列。重复执行无副作用（列已存在则跳过）。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(task_jobs)").fetchall()}
    for name, decl in _MIGRATIONS:
        if name not in existing:
            conn.execute(f"ALTER TABLE task_jobs ADD COLUMN {name} {decl}")
    from src.core.task_events import init_schema
    init_schema(conn)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_task_jobs_owner_client_request "
        "ON task_jobs(owner_user_id, client_request_id) "
        "WHERE owner_user_id IS NOT NULL AND client_request_id <> ''")


def _connect():
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        # SQLite can return BUSY immediately when two fresh connections both
        # switch journal mode; busy_timeout does not cover that upgrade race.
        wal_deadline = time.monotonic() + 30.0
        while True:
            try:
                conn.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                code = getattr(exc, "sqlite_errorcode", 0) & 0xFF
                if code not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or time.monotonic() >= wal_deadline:
                    raise
                time.sleep(0.01)
        # Schema inspection and ALTER must be serialized across connections,
        # including two processes that first open a legacy database together.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_jobs (
                job_id TEXT PRIMARY KEY,
                session_id TEXT,
                request_id TEXT,
                job_type TEXT,
                status TEXT,
                current_step TEXT,
                progress_summary TEXT,
                result_path TEXT,
                error TEXT,
                created_at TEXT,
                updated_at TEXT,
                finished_at TEXT
            )
        """)
        _migrate(conn)
        conn.commit()
        return conn
    except BaseException:
        conn.rollback()
        conn.close()
        raise


class IdempotencyConflict(ValueError):
    """The same owner's client key already identifies a different payload."""

    def __init__(self, job_id: str):
        self.job_id = job_id
        super().__init__("client_request_id is already bound to a different request")


@dataclass(frozen=True)
class JobClaim:
    """Only created=True permits dispatch; existing jobs are facts, not leases."""

    created: bool
    job: dict

    @property
    def job_id(self) -> str:
        return str(self.job["job_id"])


def claim_job(session_id: str, request_id: str, job_type: str = "chat", *,
              owner_user_id: int | None = None,
              client_request_id: str | None = None,
              payload: dict | None = None) -> JobClaim:
    """Atomically create or return an owner's durable HTTP request claim.

    For keyed requests, payload must be the complete JSON client request before
    server mutations: include the requested session, query, attachment content
    (or content digests), file references and behavior options. Exclude generated
    request/session IDs and upload IDs. The resolved session_id and request_id
    belong to the winning claim; retries return those original stored values.

    Every existing state, including running/interrupted/failed, returns
    created=False. No checkpoint recovery, side-effect replay or lease takeover
    is implied. Database failures propagate so callers cannot dispatch without a
    durable claim. No-key callers get a fresh independent job for legacy use.
    """
    if client_request_id is not None and not isinstance(client_request_id, str):
        raise ValueError("client_request_id must be a string")
    key = client_request_id or ""
    if key and (not key.strip() or len(key) > 64):
        raise ValueError("client_request_id must contain 1 to 64 characters")
    if owner_user_id is not None and (type(owner_user_id) is not int or owner_user_id <= 0):
        raise ValueError("owner_user_id must be a positive integer")
    digest = ""
    if key:
        if owner_user_id is None:
            raise ValueError("keyed requests require owner_user_id")
        if not isinstance(payload, dict):
            raise ValueError("keyed requests require the complete request payload")
        canonical = json.dumps(
            {"job_type": job_type, "payload": payload}, ensure_ascii=False,
            sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # SQLite, not the process-local lock, owns this compare-and-insert. The
    # unique partial index also protects against other writers/processes.
    with closing(_connect()) as conn, conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        if key:
            existing = conn.execute(
                "SELECT * FROM task_jobs WHERE owner_user_id=? AND client_request_id=?",
                (owner_user_id, key)).fetchone()
            if existing is not None:
                if existing["payload_hash"] != digest:
                    raise IdempotencyConflict(str(existing["job_id"]))
                return JobClaim(created=False, job=dict(existing))
        job_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        conn.execute(
            "INSERT INTO task_jobs (job_id, session_id, request_id, job_type, "
            "status, current_step, progress_summary, result_path, error, "
            "created_at, updated_at, finished_at, owner_user_id, client_request_id, payload_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (job_id, session_id, request_id, job_type, "running", "init", "", "", "",
             now, now, "", owner_user_id, key, digest))
        row = conn.execute("SELECT * FROM task_jobs WHERE job_id=?", (job_id,)).fetchone()
        return JobClaim(created=True, job=dict(row))


def create_job(session_id: str, request_id: str, job_type: str = "chat") -> str:
    """创建 job（每个 expert 请求一个；write 类工具执行时升级 job_type='write'）。"""
    job_id = uuid.uuid4().hex[:12]
    now = datetime.now().isoformat()
    try:
        with _write_lock:
            with _connect() as conn:
                conn.execute(
                    "INSERT INTO task_jobs (job_id, session_id, request_id, job_type, "
                    "status, current_step, progress_summary, result_path, error, "
                    "created_at, updated_at, finished_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (job_id, session_id, request_id, job_type, "running", "init",
                     "", "", "", now, now, ""),
                )
                conn.commit()
    except Exception as e:
        logger.warning(f"[Jobs] create_job failed: {e}")
    return job_id


def update_job(job_id: str, *, force: bool = False, **fields) -> bool:
    """更新 job 字段（status/job_type/current_step/progress_summary/result_path/error）。

    status -> completed/failed/cancelled 时自动写 finished_at。
    **终态守卫**：已处于终态的 job 不允许被改成别的状态（否则会出现
    "已 cancelled 又被 finally 写成 completed"），需显式 `force=True` 才覆盖。
    返回是否实际写入。
    """
    if not job_id:
        return False
    allowed = {"job_type", "status", "current_step", "progress_summary",
               "result_path", "error", "stop_reason", "stop_note",
               "terminal_snapshot"}
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return False
    if updates.get("status") in TERMINAL_STATUS:
        updates["finished_at"] = datetime.now().isoformat()
    set_clauses = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values())
    try:
        with _write_lock:
            with _connect() as conn:
                row = conn.execute(
                    "SELECT status FROM task_jobs WHERE job_id=?", (job_id,)
                ).fetchone()
                if row is None:
                    return False
                current = row[0]
                new_status = updates.get("status")
                if (
                    not force
                    and new_status
                    and current in TERMINAL_STATUS
                    and new_status != current
                ):
                    logger.warning(
                        "[Jobs] refused terminal downgrade %s: %s -> %s",
                        job_id, current, new_status,
                    )
                    return False
                conn.execute(
                    f"UPDATE task_jobs SET {set_clauses}, updated_at=? WHERE job_id=?",
                    values + [datetime.now().isoformat(), job_id],
                )
                conn.commit()
                return True
    except Exception as e:
        logger.warning(f"[Jobs] update_job failed: {e}")
        return False


def request_cancel(job_id: str, reason: str = "user") -> bool:
    """标记"已收到停止请求"。只从 running/detached 迁入，绝不写终态。

    重复点击停止返回 False：状态已是 cancel_requested 时不该再改一次时间戳，
    调用方据此判定 accepted=False，避免"又一次接受停止"的假象。
    """
    if not job_id:
        return False
    now = datetime.now().isoformat()
    try:
        with _write_lock:
            with _connect() as conn:
                cur = conn.execute(
                    "UPDATE task_jobs SET status='cancel_requested', "
                    "cancel_requested_at=?, stop_reason=?, updated_at=? "
                    "WHERE job_id=? AND status IN ('running','detached')",
                    (now, reason, now, job_id),
                )
                conn.commit()
                return bool(cur.rowcount)
    except Exception as e:
        logger.warning(f"[Jobs] request_cancel failed: {e}")
        return False


def mark_detached(job_id: str) -> bool:
    """观察者离开（断线/关页面），执行继续。不覆盖 cancel_requested。"""
    if not job_id:
        return False
    now = datetime.now().isoformat()
    try:
        with _write_lock:
            with _connect() as conn:
                cur = conn.execute(
                    "UPDATE task_jobs SET status='detached', detached_at=?, updated_at=? "
                    "WHERE job_id=? AND status='running'",
                    (now, now, job_id),
                )
                conn.commit()
                return bool(cur.rowcount)
    except Exception as e:
        logger.warning(f"[Jobs] mark_detached failed: {e}")
        return False


def mark_stopped(job_id: str, reason: str = "user", note: str = "") -> bool:
    """进入终态 cancelled。只从 open 态迁入；reason 必须是
    `user / detached_timeout / shutdown / internal` 之一。"""
    if not job_id:
        return False
    reason = reason or "internal"
    now = datetime.now().isoformat()
    try:
        with _write_lock:
            with _connect() as conn:
                cur = conn.execute(
                    "UPDATE task_jobs SET status='cancelled', stop_reason=?, stop_note=?, "
                    "finished_at=?, updated_at=? "
                    "WHERE job_id=? AND status IN ('running','detached','cancel_requested')",
                    (reason, note, now, now, job_id),
                )
                conn.commit()
                return bool(cur.rowcount)
    except Exception as e:
        logger.warning(f"[Jobs] mark_stopped failed: {e}")
        return False


def mark_completed_if_open(job_id: str, note: str = "", **fields) -> bool:
    """任务正常结束：仅在 open 态写入终态。

    若此前收到过停止请求（`cancel_requested`），本次完成视为"停止请求晚于完成"，
    记 `stop_note`，状态仍为 `completed`（这是唯一允许的 cancel_requested → completed 迁移）。
    """
    if not job_id:
        return False
    payload = {k: v for k, v in fields.items()
               if k in {"current_step", "progress_summary", "result_path", "error"}}
    payload["status"] = "completed"
    if note:
        payload["stop_note"] = note
    now = datetime.now().isoformat()
    payload["finished_at"] = now
    set_clauses = ", ".join(f"{k}=?" for k in payload)
    try:
        with _write_lock:
            with _connect() as conn:
                cur = conn.execute(
                    f"UPDATE task_jobs SET {set_clauses}, updated_at=? "
                    "WHERE job_id=? AND status IN ('running','detached','cancel_requested')",
                    list(payload.values()) + [now, job_id],
                )
                conn.commit()
                return bool(cur.rowcount)
    except Exception as e:
        logger.warning(f"[Jobs] mark_completed_if_open failed: {e}")
        return False


def mark_interrupted_jobs() -> int:
    """Close request jobs left running by a process restart or crash."""
    now = datetime.now().isoformat()
    try:
        with _write_lock:
            with _connect() as conn:
                cursor = conn.execute(
                    "UPDATE task_jobs SET status='failed', current_step='interrupted', "
                    "progress_summary='服务进程在任务完成前停止；请重新提交。', "
                    "error='service_restart', updated_at=?, finished_at=? "
                    "WHERE status IN ('running','detached','cancel_requested')",
                    (now, now),
                )
                from src.core.task_events import recover_interrupted
                recover_interrupted(conn)
                conn.commit()
                count = int(cursor.rowcount or 0)
        if count:
            logger.warning("[Jobs] marked %d stale open jobs as interrupted", count)
        return count
    except Exception as exc:
        logger.warning("[Jobs] failed to reconcile interrupted jobs: %s", exc)
        return 0


def get_job(job_id: str) -> Optional[dict]:
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM task_jobs WHERE job_id=?",
                               (job_id,)).fetchone()
            if row is None:
                return None
            return dict(row)
    except Exception as e:
        logger.warning(f"[Jobs] get_job failed: {e}")
        return None


def save_terminal_snapshot(job_id: str, snapshot: dict) -> bool:
    """Persist the minimal terminal view for detached/reconnect recovery."""
    if not job_id or not isinstance(snapshot, dict):
        return False
    try:
        payload = json.dumps(snapshot, ensure_ascii=False, default=str)
    except Exception as exc:
        logger.warning("[Jobs] terminal snapshot encode failed: %s", exc)
        return False
    # Once the durable done frame exists it owns the stamped terminal sequence.
    # API cleanup may still hold the pre-stamp envelope; it must not overwrite it.
    try:
        with closing(_connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT data FROM task_events WHERE job_id=? AND event='done' ORDER BY seq LIMIT 1", (job_id,)).fetchone()
            if row is not None:
                envelope = json.loads(row[0]).get("turn_envelope")
                if isinstance(envelope, dict):
                    payload = json.dumps(envelope, ensure_ascii=False)
            cursor = conn.execute("UPDATE task_jobs SET terminal_snapshot=?,updated_at=? WHERE job_id=?",
                                  (payload, datetime.now().isoformat(), job_id))
            return bool(cursor.rowcount)
    except Exception as exc:
        logger.warning("[Jobs] terminal snapshot save failed: %s", exc)
        return False


def decode_terminal_snapshot(job: Optional[dict]) -> dict:
    """Parse the stored snapshot; missing/corrupt data is an empty view."""
    raw = str((job or {}).get("terminal_snapshot") or "")
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def list_for_session(session_id: str, limit: int = 10) -> list:
    try:
        with _connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM task_jobs WHERE session_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (session_id, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[Jobs] list_for_session failed: {e}")
        return []


def purge_protocol_for_owner(owner_user_id: int) -> None:
    """Purge task payloads before account deletion removes the parent jobs."""
    from pathlib import Path
    if not Path(_DB_PATH).is_file():
        return
    # Account cleanup must inspect an existing store, never bootstrap a partial
    # sessions database for an account that has not submitted a task.
    with closing(sqlite3.connect(str(_DB_PATH), timeout=30)) as conn, conn:
        conn.execute("BEGIN IMMEDIATE")
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "task_jobs" not in tables:
            return
        columns = {row[1] for row in conn.execute("PRAGMA table_info(task_jobs)")}
        if "owner_user_id" not in columns:
            return
        for table in ("task_events", "task_updates", "task_approvals", "task_operations",
                      "task_model_attempts", "task_budgets"):
            if table in tables:
                conn.execute(f"DELETE FROM {table} WHERE job_id IN ("
                             "SELECT job_id FROM task_jobs WHERE owner_user_id=?)", (int(owner_user_id),))


def is_write_job(job_id: str) -> bool:
    """断连保活判定：仅 write 类任务继续后台执行（S1 起普通问答也不再因断线取消）。"""
    job = get_job(job_id)
    return bool(job and job.get("job_type") == "write")
