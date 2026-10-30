import sqlite3
import time

import pytest

from src.session import files


@pytest.fixture
def file_store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", state / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", state / "session_files")
    files.init_store()
    return tmp_path


def test_strict_session_purge_removes_upload_bytes_and_index(file_store):
    sid = "session_file_0001"
    info = files.register_file(
        session_id=sid, owner_user_id=19, filename="notes.txt",
        content=b"private attachment", mime="text/plain")
    path = files.get_file(sid, info["file_id"])["path"]
    assert path.is_file()

    files.purge_session(sid, strict=True)

    assert not path.exists()
    assert files.list_files(sid, owner_user_id=19) == []
    with sqlite3.connect(files.DB_PATH) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM session_activity WHERE session_id=?", (sid,)
        ).fetchone()[0] == 0


def test_purge_removes_unindexed_crash_leftover(file_store):
    sid = "session_orphan_file_01"
    orphan = files.FILES_ROOT / sid / "unindexed_upload.bin"
    orphan.parent.mkdir(parents=True)
    orphan.write_bytes(b"written before index transaction")

    files.purge_session(sid, strict=True)

    assert not orphan.exists()
    assert not orphan.parent.exists()


def test_preview_touch_updates_session_activity(file_store):
    sid = "session_preview_touch_01"
    info = files.register_file(
        session_id=sid, owner_user_id=25, filename="notes.txt",
        content=b"preview", mime="text/plain")
    with sqlite3.connect(files.DB_PATH) as conn:
        conn.execute("UPDATE session_activity SET last_active_at=? WHERE session_id=?",
                     (0, sid))

    result = files.get_file(sid, info["file_id"], touch=True, owner_user_id=25)

    assert result is not None
    with sqlite3.connect(files.DB_PATH) as conn:
        touched = conn.execute(
            "SELECT last_active_at FROM session_activity WHERE session_id=?",
            (sid,)).fetchone()[0]
    assert touched > 0


def test_newer_file_activity_prevents_cleanup_when_conversation_timestamp_is_old(file_store):
    sid = "session_recent_file_access_01"
    info = files.register_file(
        session_id=sid, owner_user_id=28, filename="recent.txt",
        content=b"keep while recently viewed", mime="text/plain")
    path = files.get_file(sid, info["file_id"])["path"]
    with sqlite3.connect(files.DB_PATH) as conn:
        conn.execute("UPDATE session_activity SET last_active_at=? WHERE session_id=?",
                     (time.time(), sid))

    result = files.cleanup_idle_sessions(idle_days=1, activity=[(sid, 0)])

    assert result["purged_sessions"] == 0
    assert path.is_file()


def test_file_index_enforces_owner_id_as_defense_in_depth(file_store):
    sid = "session_owner_fence_01"
    info = files.register_file(
        session_id=sid, owner_user_id=41, filename="private.txt",
        content=b"owner-bound", mime="text/plain")
    path = files.get_file(sid, info["file_id"])["path"]

    assert files.list_files(sid, owner_user_id=42) == []
    assert files.get_file(sid, info["file_id"], owner_user_id=42) is None
    assert files.delete_file(sid, info["file_id"], owner_user_id=42) is False
    assert path.is_file()
    public = files.get_file(sid, info["file_id"], owner_user_id=41)
    assert public is not None
    assert "owner_user_id" not in public


def test_session_directory_symlink_cannot_redefine_upload_root(file_store, monkeypatch):
    sid = "session_symlink_guard_01"
    candidate = files.FILES_ROOT / sid
    original = type(candidate).is_symlink

    def mark_target_as_symlink(path):
        return path == candidate or original(path)

    monkeypatch.setattr(type(candidate), "is_symlink", mark_target_as_symlink)
    with pytest.raises(ValueError, match="is a symlink"):
        files._session_dir(sid)


def test_failed_index_write_does_not_leave_untracked_upload(file_store, monkeypatch):
    def fail_connect():
        raise sqlite3.OperationalError("simulated index failure")

    monkeypatch.setattr(files, "_connect", fail_connect)
    sid = "session_failed_index_01"

    with pytest.raises(sqlite3.OperationalError, match="simulated index failure"):
        files.register_file(
            session_id=sid, owner_user_id=26, filename="upload.txt",
            content=b"must be cleaned", mime="text/plain")

    assert not (files.FILES_ROOT / sid).exists()


def test_idle_cleanup_includes_orphaned_file_activity(file_store):
    """An idle file is reclaimed even when its session is not in the activity map.

    The contract under test is per-file retention: cleanup reads
    ``session_files.last_accessed_at`` and reclaims the bytes, the library
    record, and its turn reference. The older session-idleness contract (an old
    ``session_activity`` row alone reclaims files) is what this test used to
    encode, and it contradicted the sibling test above, which requires that a
    recently viewed file survives an old conversation timestamp.
    """
    sid = "session_orphan_01"
    request_id = "crid_orphan_turn_01"
    info = files.register_file(
        session_id=sid, owner_user_id=22, filename="old.txt",
        content=b"old private attachment", mime="text/plain",
        client_request_id=request_id, producer_tool="audit_uploaded_data")
    path = files.get_file(sid, info["file_id"])["path"]
    assert files.list_turn_files(sid, 22, [request_id])[request_id]
    with sqlite3.connect(files.DB_PATH) as conn:
        conn.execute("UPDATE session_activity SET last_active_at=? WHERE session_id=?",
                     (0, sid))
        # Retention applies the file's own clock, so that is what must be old.
        conn.execute("UPDATE session_files SET last_accessed_at=0 WHERE file_id=?",
                     (info["file_id"],))

    result = files.cleanup_idle_sessions(idle_days=1, activity=[])

    assert result["purged_sessions"] == 1
    assert result["removed_files"] == 1
    assert result["expired_records"] == 1
    assert not path.exists()
    assert files.list_files(sid) == []
    # Reclaiming a file must not leave a turn reference pointing at nothing.
    assert files.list_turn_files(sid, 22, [request_id]) == {}


def test_expired_generated_artifact_entry_can_be_pruned(file_store):
    sid = "session_artifact_01"
    artifact_id = "a" * 32
    path = file_store / "state" / "artifacts" / sid / "report.html"
    path.parent.mkdir(parents=True)
    path.write_text("<html>report</html>", encoding="utf-8")
    files.register_file(
        session_id=sid, owner_user_id=31, filename="report.html",
        storage_path=str(path), mime="text/html", origin=files.ORIGIN_GENERATED,
        artifact_id=artifact_id)

    assert files.remove_generated_entry(sid, artifact_id) is True
    assert path.is_file()  # Index cleanup does not own or delete artifact bytes.
    assert files.list_files(sid, owner_user_id=31) == []


def test_generated_file_cannot_reference_another_sessions_directory(file_store):
    source_sid = "session_source_01"
    other_sid = "session_other_01"
    other_path = file_store / "state" / "artifacts" / other_sid / "file.html"
    other_path.parent.mkdir(parents=True)
    other_path.write_text("<html>other session</html>", encoding="utf-8")

    with pytest.raises(ValueError, match="managed session area"):
        files.register_file(
            session_id=source_sid, owner_user_id=1, filename="file.html",
            storage_path=str(other_path), mime="text/html",
            origin=files.ORIGIN_GENERATED, artifact_id="b" * 32)
