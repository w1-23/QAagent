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

import json
import logging
import sqlite3
import threading
import uuid
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
)


def _migrate(conn) -> None:
    """补齐 S1 新增列。重复执行无副作用（列已存在则跳过）。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(task_jobs)").fetchall()}
    for name, decl in _MIGRATIONS:
        if name not in existing:
            conn.execute(f"ALTER TABLE task_jobs ADD COLUMN {name} {decl}")
    conn.commit()


def _connect():
    import sqlite3
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
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
               "result_path", "error", "stop_reason", "stop_note"}
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


def is_write_job(job_id: str) -> bool:
    """断连保活判定：仅 write 类任务继续后台执行（S1 起普通问答也不再因断线取消）。"""
    job = get_job(job_id)
    return bool(job and job.get("job_type") == "write")
