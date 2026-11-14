import sqlite3
from types import SimpleNamespace

import pytest

from src import auth, config
from src.core import jobs
from src.figures import store as figures
from src.session import files


def _configure(tmp_path, monkeypatch):
    state = tmp_path / "state"
    from src.core import account_leases
    monkeypatch.setattr(account_leases, "_states", {})
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", state / "accounts.sqlite3")
    monkeypatch.setattr(figures, "_STATE_DIR", state)
    monkeypatch.setattr(jobs, "_DB_PATH", state / "sessions.db")
    from src.guardrails import memory
    monkeypatch.setattr(memory, "memory_store", SimpleNamespace(
        db_path=state / "sessions.db",
        clear_user_memory=lambda _user_id: None,
        clear_session=lambda _session_id: None))
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", state / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", state / "session_files")
    auth.init_store()
    files.init_store()


def _add_user(role="user", username="qa_delete_user"):
    with auth._connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at) "
            "VALUES(?,?,?,?,1,30,'2026-01-01T00:00:00+00:00')",
            (username, b"salt", b"hash", role))
        return int(cur.lastrowid)


def _create_session_db(path, user_id, sid):
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS sessions "
                     "(session_id TEXT PRIMARY KEY, owner_user_id INTEGER, updated_at TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS messages (session_id TEXT, content TEXT)")
        conn.execute("INSERT INTO sessions VALUES(?,?,?)", (sid, user_id, "2026-01-01T00:00:00"))
        conn.execute("INSERT INTO messages VALUES(?,?)", (sid, "private conversation"))


def test_account_deletion_purges_session_uploads_and_records(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_account_01"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, sid)
    upload = files.register_file(
        session_id=sid, owner_user_id=user_id, filename="private.txt",
        content=b"private upload", mime="text/plain")
    upload_path = files.get_file(sid, upload["file_id"])["path"]
    figures.create_project(owner_user_id=user_id, session_id=sid,
                           source={"private": "figure source"})
    other_id = _add_user(username="other_account")
    other = figures.create_project(owner_user_id=other_id, source={"keep": True})
    purge_session = files.purge_session

    def purge_after_figures(*args, **kwargs):
        assert figures.count_projects(user_id) == 0
        return purge_session(*args, **kwargs)

    monkeypatch.setattr(files, "purge_session", purge_after_figures)

    from src.core.word_documents import WordDocuments
    word_db = tmp_path / "state/word_drafts.sqlite3"
    drafts = WordDocuments(word_db, owner=user_id, session=sid)
    drafts.create(title="私有草稿", profile="general", batch_id="create",
                  outline=[{"section_id": "s1", "title": "私有章节"}])
    assert auth.delete_user_account(user_id) is True
    assert figures.count_projects(user_id) == 0
    assert figures.get_project(other["figure_id"], other_id) is not None
    assert not upload_path.exists()
    with sqlite3.connect(word_db) as conn:
        for table in ("word_drafts", "word_versions", "word_batches"):
            assert conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0
    with auth._connect() as conn:
        assert conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM messages WHERE session_id=?", (sid,)).fetchone() is None
    with sqlite3.connect(files.DB_PATH) as conn:
        assert conn.execute("SELECT 1 FROM session_files WHERE session_id=?", (sid,)).fetchone() is None
        assert conn.execute("SELECT 1 FROM session_activity WHERE session_id=?", (sid,)).fetchone() is None


def test_admin_account_delete_refusal_does_not_delete_its_sessions(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    admin_id = _add_user(role="admin")
    sid = "session_admin_01"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, admin_id, sid)

    assert auth.delete_user_account(admin_id) is False
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone() is not None


def test_failed_account_purge_keeps_account_disabled(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_account_fail_01"
    _create_session_db(tmp_path / "state" / "sessions.db", user_id, sid)
    monkeypatch.setattr(files, "purge_session",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("simulated")))

    assert auth.delete_user_account(user_id) is False
    with auth._connect() as conn:
        row = conn.execute("SELECT active FROM users WHERE user_id=?", (user_id,)).fetchone()
    assert row is not None and int(row["active"]) == 0


@pytest.mark.parametrize("status", ["running", "detached", "cancel_requested", "queued", None])
def test_unfinished_jobs_defer_purge_and_allow_disabled_retry(tmp_path, monkeypatch, status):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_busy_01"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, sid)
    upload = files.register_file(session_id=sid, owner_user_id=user_id,
                                 filename="private.txt", content=b"private")
    path = files.get_file(sid, upload["file_id"])["path"]
    figure = figures.create_project(owner_user_id=user_id, session_id=sid)
    job_id = jobs.create_job(sid, "request_busy")
    with sqlite3.connect(sessions_db) as conn:
        conn.execute("UPDATE task_jobs SET status=? WHERE job_id=?", (status, job_id))
    with auth._connect() as conn:
        conn.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                     ("test_digest", user_id, "2026-01-01", "2099-01-01"))

    assert auth.delete_user_account(user_id) is False
    assert auth.user_by_id(user_id)["active"] is False
    assert auth.reserve_chat_request({"user_id": user_id, "role": "user"}) == (False, 0)
    assert path.exists()
    assert figures.get_project(figure["figure_id"], user_id) is not None
    with auth._connect() as conn:
        assert conn.execute("SELECT 1 FROM browser_sessions WHERE user_id=?", (user_id,)).fetchone() is None
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT content FROM messages WHERE session_id=?", (sid,)).fetchone()[0] == "private conversation"
        conn.execute("UPDATE task_jobs SET status='completed' WHERE job_id=?", (job_id,))

    assert auth.delete_user_account(user_id) is True
    assert auth.user_by_id(user_id) is None
    assert not path.exists()
    assert figures.get_project(figure["figure_id"], user_id) is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_terminal_jobs_allow_cleanup_without_blocking_on_another_account(tmp_path, monkeypatch, status):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    other_id = _add_user(username="other_busy_account")
    sid, other_sid = "session_done_01", "session_other_busy"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, sid)
    _create_session_db(sessions_db, other_id, other_sid)
    job_id = jobs.create_job(sid, "request_done")
    assert jobs.update_job(job_id, status=status)
    other_job = jobs.create_job(other_sid, "request_other")
    assert auth.delete_user_account(user_id) is True
    assert auth.user_by_id(other_id)["active"] is True
    assert jobs.get_job(other_job)["status"] == "running"
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (other_sid,)).fetchone()
        assert conn.execute("SELECT 1 FROM task_jobs WHERE job_id=?", (job_id,)).fetchone() is None


def test_figure_purge_failure_preserves_disabled_identity_and_files(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_figure_failure"
    _create_session_db(tmp_path / "state" / "sessions.db", user_id, sid)
    figure = figures.create_project(owner_user_id=user_id, session_id=sid)
    upload = files.register_file(session_id=sid, owner_user_id=user_id,
                                 filename="private.txt", content=b"private")
    path = files.get_file(sid, upload["file_id"])["path"]
    purge = figures.purge_account

    def fail(_owner):
        raise OSError("figure cleanup unavailable")

    monkeypatch.setattr(figures, "purge_account", fail)
    assert auth.delete_user_account(user_id) is False
    assert auth.user_by_id(user_id)["active"] is False
    assert path.exists()
    assert figures.get_project(figure["figure_id"], user_id) is not None
    monkeypatch.setattr(figures, "purge_account", purge)
    assert auth.delete_user_account(user_id) is True


def test_unreadable_job_schema_fails_closed_before_any_purge(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_unreadable_jobs"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, sid)
    with sqlite3.connect(sessions_db) as conn:
        conn.execute("CREATE TABLE task_jobs (session_id TEXT)")
    figure = figures.create_project(owner_user_id=user_id, session_id=sid)
    assert auth.delete_user_account(user_id) is False
    assert auth.user_by_id(user_id)["active"] is False
    assert figures.get_project(figure["figure_id"], user_id) is not None


def test_job_registered_during_cleanup_prevents_final_identity_deletion(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sid = "session_late_job"
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, sid)
    purge = figures.purge_account
    created = []

    def purge_then_register(owner):
        result = purge(owner)
        created.append(jobs.create_job(sid, "late_request"))
        return result

    monkeypatch.setattr(figures, "purge_account", purge_then_register)
    assert auth.delete_user_account(user_id) is False
    assert auth.user_by_id(user_id)["active"] is False
    assert jobs.get_job(created[0])["status"] == "running"
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT 1 FROM sessions WHERE session_id=?", (sid,)).fetchone()


def test_new_session_during_cleanup_prevents_final_identity_deletion(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    sessions_db = tmp_path / "state" / "sessions.db"
    _create_session_db(sessions_db, user_id, "session_initial")
    purge = figures.purge_account

    def purge_then_create_session(owner):
        result = purge(owner)
        _create_session_db(sessions_db, owner, "session_arrived_late")
        return result

    monkeypatch.setattr(figures, "purge_account", purge_then_create_session)
    assert auth.delete_user_account(user_id) is False
    assert auth.user_by_id(user_id)["active"] is False
    with sqlite3.connect(sessions_db) as conn:
        assert conn.execute("SELECT count(*) FROM sessions WHERE owner_user_id=?", (user_id,)).fetchone()[0] == 2


def test_account_without_chat_sessions_still_purges_figure_projects(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    user_id = _add_user()
    figure = figures.create_project(owner_user_id=user_id, source={"private": "data"})
    assert auth.delete_user_account(user_id) is True
    assert figures.get_project(figure["figure_id"], user_id) is None
    assert auth.user_by_id(user_id) is None
