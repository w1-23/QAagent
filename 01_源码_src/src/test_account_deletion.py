import sqlite3
from types import SimpleNamespace

from src import auth
from src.session import files


def _configure(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", state / "accounts.sqlite3")
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", state / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", state / "session_files")
    auth.init_store()
    files.init_store()


def _add_user(role="user"):
    with auth._connect() as conn:
        cur = conn.execute(
            "INSERT INTO users(username,salt,password_hash,role,active,daily_limit,created_at) "
            "VALUES(?,?,?,?,1,30,'2026-01-01T00:00:00+00:00')",
            ("qa_delete_user", b"salt", b"hash", role))
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

    from src.guardrails import memory
    monkeypatch.setattr(memory, "memory_store", SimpleNamespace(
        clear_user_memory=lambda _user_id: None,
        clear_session=lambda _session_id: None))

    assert auth.delete_user_account(user_id) is True
    assert not upload_path.exists()
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
