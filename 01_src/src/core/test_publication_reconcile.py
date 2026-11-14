"""Crash recovery and trust boundaries for immutable artifact publication."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from src import auth
from src.core import account_leases, publication_reconcile as recovery
from src.core.deliverable_check import DeliverableRejected
from src.core.tracing import set_session_id, set_user_id, set_client_request_id, set_current_tool_name
from src.research import tools
from src.session import files
from src.session.manager import session_manager

OWNER = 71031
OTHER = 71032
SID = "session-publication-outbox-01"


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(recovery, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    monkeypatch.setattr(account_leases, "_states", {})
    (tmp_path / "state").mkdir()
    with sqlite3.connect(tmp_path / "state/sessions.db") as conn:
        conn.execute("CREATE TABLE sessions(session_id TEXT PRIMARY KEY,owner_user_id INTEGER,updated_at REAL)")
        conn.execute("INSERT INTO sessions VALUES(?,?,0)", (SID, OWNER))
    auth.init_store()
    with sqlite3.connect(auth.DB_PATH) as conn:
        for uid in (OWNER, OTHER):
            conn.execute("INSERT INTO users(user_id,username,salt,password_hash,active,created_at) "
                         "VALUES(?,?,?,?,1,'fixture')", (uid, f"fixture-{uid}", b"test", b"test"))
    set_session_id(SID)
    set_user_id(OWNER)
    set_client_request_id("publication-request-01")
    set_current_tool_name("engineering_publication_fixture")
    yield tmp_path
    set_session_id("")
    set_user_id(None)
    set_client_request_id("")
    set_current_tool_name("")


def _rows(root, table="publication_outbox"):
    with sqlite3.connect(root / "state/research_artifacts.sqlite3") as conn:
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]


def _pending(root, monkeypatch, *, name="fixture.txt", content=b"engineering fixture"):
    original = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kw: (_ for _ in ()).throw(OSError("simulated index failure")))
    with pytest.raises(DeliverableRejected, match="artifact_registration_failed"):
        tools._save_artifact(name, content, "text/plain")
    monkeypatch.setattr(files, "register_file", original)
    return _rows(root, "artifacts")[-1]


def test_commit_gap_recovers_and_exact_retry_is_idempotent(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    outbox = _rows(isolated)[0]
    assert outbox["state"] == "pending"
    assert outbox["owner_user_id"] == artifact["owner_user_id"] == OWNER
    assert outbox["size_bytes"] == artifact["artifact_size_bytes"] == len(b"engineering fixture")
    assert recovery.reconcile_publications() == {"scanned": 1, "committed": 1, "pending": 0}
    first_file_id = _rows(isolated)[0]["file_id"]
    url = tools._save_artifact("fixture.txt", b"engineering fixture", "text/plain")
    assert url.endswith(artifact["artifact_id"])
    assert len(files.list_files(SID, OWNER)) == 1
    assert _rows(isolated)[0]["file_id"] == first_file_id
    assert recovery.reconcile_publications()["scanned"] == 0


def test_changed_output_keeps_prior_publication_immutable(isolated):
    first = tools._save_artifact("fixture.txt", b"first immutable version", "text/plain")
    second = tools._save_artifact("fixture.txt", b"second immutable version", "text/plain")
    assert first != second
    artifacts = _rows(isolated, "artifacts")
    assert [Path(row["path"]).read_bytes() for row in artifacts] == [
        b"first immutable version", b"second immutable version"]
    assert [row["version"] for row in artifacts] == [1, 2]
    assert all(row["state"] == "committed" for row in _rows(isolated))


def test_failed_append_registration_recovers_without_double_append(isolated, monkeypatch):
    tools._save_artifact("fixture.txt", b"original bytes", "text/plain")
    original_register = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kw: (_ for _ in ()).throw(OSError("simulated crash gap")))
    with pytest.raises(DeliverableRejected):
        tools._save_artifact("fixture.txt", b"append bytes", "text/plain", append=True)
    assert len(_rows(isolated, "artifact_append_batches")) == 1
    monkeypatch.setattr(files, "register_file", original_register)
    assert recovery.reconcile_publications()["committed"] == 1
    appended = tools._save_artifact("fixture.txt", b"append bytes", "text/plain", append=True)
    assert len(_rows(isolated, "artifacts")) == 2
    artifact = next(row for row in _rows(isolated, "artifacts") if appended.endswith(row["artifact_id"]))
    assert Path(artifact["path"]).read_bytes() == b"original bytes\n\nappend bytes"
    assert len(files.list_files(SID, OWNER)) == 2


def test_registration_error_traceback_retains_only_closed_helper_connections(isolated, monkeypatch):
    monkeypatch.setattr(files, "register_file", lambda **kw: (_ for _ in ()).throw(OSError("retained fixture failure")))
    with pytest.raises(DeliverableRejected) as failure:
        tools._save_artifact("fixture.txt", b"engineering fixture", "text/plain")
    traceback = failure.value.__cause__.__traceback__
    checked = 0
    while traceback is not None:
        frame = traceback.tb_frame
        if frame.f_code.co_filename == recovery.__file__:
            conn = frame.f_locals.get("conn")
            if isinstance(conn, sqlite3.Connection):
                with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                    conn.execute("SELECT 1")
                checked += 1
        traceback = traceback.tb_next
    assert checked > 0, "must inspect the retained real registration traceback"


def test_schema_failure_closes_database_despite_retained_traceback(isolated, monkeypatch):
    retained = []
    def fail_schema(conn):
        retained.append(conn)
        raise sqlite3.OperationalError("retained schema failure")
    monkeypatch.setattr(recovery, "init_outbox", fail_schema)
    with pytest.raises(sqlite3.OperationalError):
        tools._db_path()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        retained[0].execute("SELECT 1")


def test_outbox_failure_rolls_back_artifact_row_and_bytes(isolated, monkeypatch):
    def fail(*a, **kw):
        raise sqlite3.OperationalError("simulated outbox insert failure")
    monkeypatch.setattr(recovery, "enqueue_publication", fail)
    with pytest.raises(sqlite3.OperationalError):
        tools._save_artifact("fixture.txt", b"engineering fixture", "text/plain")
    assert _rows(isolated, "artifacts") == _rows(isolated) == []
    assert not list((isolated / "state/artifacts" / SID).glob("*"))


def test_crash_after_index_commit_reuses_registered_file(isolated, monkeypatch):
    original = recovery._snapshot
    calls = 0
    def crash_before_marker(conn, aid):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated crash before committed marker")
        return original(conn, aid)
    monkeypatch.setattr(recovery, "_snapshot", crash_before_marker)
    with pytest.raises(DeliverableRejected):
        tools._save_artifact("fixture.txt", b"engineering fixture", "text/plain")
    assert _rows(isolated)[0]["state"] == "pending"
    file_id = files.list_files(SID, OWNER)[0]["file_id"]
    monkeypatch.setattr(recovery, "_snapshot", original)
    assert recovery.reconcile_publications()["committed"] == 1
    assert files.list_files(SID, OWNER)[0]["file_id"] == file_id
    assert len(files.list_files(SID, OWNER)) == 1


def test_changed_bytes_are_not_adopted_or_rehashed_as_truth(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    path = Path(artifact["path"])
    path.write_bytes(b"x" * artifact["artifact_size_bytes"])
    monkeypatch.setattr(files, "register_file", lambda **kw: pytest.fail("must verify before registration"))
    assert recovery.reconcile_publications() == {"scanned": 1, "committed": 0, "pending": 1}
    assert _rows(isolated)[0]["state"] == "pending"
    assert _rows(isolated)[0]["last_error"] == "artifact_digest_mismatch"
    assert path.read_bytes() == b"x" * artifact["artifact_size_bytes"]


@pytest.mark.parametrize("corruption", ["outbox_owner", "both_owners", "session_owner", "disabled", "deleted", "unbound"])
def test_recovery_rejects_forged_or_unavailable_owner(isolated, monkeypatch, corruption):
    _pending(isolated, monkeypatch)
    with sqlite3.connect(isolated / "state/research_artifacts.sqlite3") as conn:
        if corruption in {"outbox_owner", "both_owners"}:
            conn.execute("UPDATE publication_outbox SET owner_user_id=?", (OTHER,))
        if corruption == "both_owners":
            conn.execute("UPDATE artifacts SET owner_user_id=?", (OTHER,))
        if corruption == "unbound":
            conn.execute("UPDATE artifacts SET owner_user_id=0")
            conn.execute("UPDATE publication_outbox SET owner_user_id=0")
    if corruption == "session_owner":
        with sqlite3.connect(isolated / "state/sessions.db") as conn:
            conn.execute("UPDATE sessions SET owner_user_id=?", (OTHER,))
    if corruption in {"disabled", "deleted"}:
        with sqlite3.connect(auth.DB_PATH) as conn:
            conn.execute("DELETE FROM users WHERE user_id=?" if corruption == "deleted"
                         else "UPDATE users SET active=0 WHERE user_id=?", (OWNER,))
    monkeypatch.setattr(files, "register_file", lambda **kw: pytest.fail("owner must be checked first"))
    assert recovery.reconcile_publications()["committed"] == 0
    assert _rows(isolated)[0]["state"] == "pending"
    assert _rows(isolated)[0]["attempts"] == 1


def test_outside_path_is_never_read_or_registered(isolated, monkeypatch):
    _pending(isolated, monkeypatch)
    outside = isolated / "unmanaged.txt"
    outside.write_text("not an artifact")
    with sqlite3.connect(isolated / "state/research_artifacts.sqlite3") as conn:
        conn.execute("UPDATE artifacts SET path=?", (str(outside),))
    monkeypatch.setattr(files, "register_file", lambda **kw: pytest.fail("must reject unmanaged path"))
    assert recovery.reconcile_publications()["pending"] == 1
    assert outside.read_text() == "not an artifact"
    assert _rows(isolated)[0]["last_error"] == "unmanaged_artifact_path"


def test_hardlinked_artifact_is_not_adopted(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    try:
        os.link(artifact["path"], isolated / "second-link.txt")
    except OSError:
        pytest.skip("host filesystem does not support hard links")
    assert recovery.reconcile_publications()["pending"] == 1
    assert _rows(isolated)[0]["last_error"] == "invalid_artifact_size_or_type"


def test_linked_artifact_is_not_followed(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    original = Path(artifact["path"])
    outside = isolated / "outside.txt"
    original.replace(outside)
    try:
        original.symlink_to(outside)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    assert recovery.reconcile_publications()["pending"] == 1
    assert _rows(isolated)[0]["last_error"] == "linked_artifact_path"
    assert outside.read_bytes() == b"engineering fixture"


def test_registration_response_without_real_index_never_commits(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    monkeypatch.setattr(files, "register_file", lambda **kw: {
        "file_id": "f" * 32, "sha256": artifact["artifact_sha256"],
        "size_bytes": artifact["artifact_size_bytes"]})
    assert recovery.reconcile_publications()["pending"] == 1
    assert _rows(isolated)[0]["state"] == "pending"
    assert _rows(isolated)[0]["last_error"] == "registration_not_persisted"


def test_quota_failure_remains_pending_and_can_be_retried(isolated, monkeypatch):
    _pending(isolated, monkeypatch)
    original = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kw: (_ for _ in ()).throw(files.StorageQuotaExceeded("fixture quota")))
    assert recovery.reconcile_publications()["pending"] == 1
    assert _rows(isolated)[0]["last_error"] == "StorageQuotaExceeded"
    monkeypatch.setattr(files, "register_file", original)
    assert recovery.reconcile_publications()["committed"] == 1
    assert len(files.list_files(SID, OWNER)) == 1


def test_existing_index_corruption_is_not_silently_overwritten(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    files.init_store()
    entry = files.register_file(session_id=SID, owner_user_id=OWNER, filename=artifact["filename"],
                                storage_path=artifact["path"], origin=files.ORIGIN_GENERATED,
                                artifact_id=artifact["artifact_id"])
    with files._connect() as conn:
        conn.execute("UPDATE session_files SET sha256=? WHERE file_id=?", ("0" * 64, entry["file_id"]))
    assert recovery.reconcile_publications()["pending"] == 1
    with files._connect() as conn:
        assert conn.execute("SELECT sha256 FROM session_files WHERE file_id=?", (entry["file_id"],)).fetchone()[0] == "0" * 64
    assert _rows(isolated)[0]["state"] == "pending"


def test_mutation_during_registration_never_marks_committed(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    original = files.register_file
    def changed(**kw):
        entry = original(**kw)
        Path(artifact["path"]).write_bytes(b"x" * artifact["artifact_size_bytes"])
        return entry
    monkeypatch.setattr(files, "register_file", changed)
    assert recovery.reconcile_publications()["pending"] == 1
    assert _rows(isolated)[0]["state"] == "pending"


def test_reconciliation_batch_is_bounded(isolated, monkeypatch):
    for index in range(3):
        _pending(isolated, monkeypatch, name=f"fixture-{index}.txt", content=f"fixture {index}".encode())
    assert recovery.reconcile_publications(limit=2) == {"scanned": 2, "committed": 2, "pending": 0}
    assert recovery.reconcile_publications(limit=2)["scanned"] == 1


def test_legacy_rows_without_bound_outbox_are_not_guessed(isolated, monkeypatch):
    _pending(isolated, monkeypatch)
    with sqlite3.connect(isolated / "state/research_artifacts.sqlite3") as conn:
        conn.execute("DELETE FROM publication_outbox")
        conn.execute("UPDATE artifacts SET owner_user_id=0")
    assert recovery.reconcile_publications()["scanned"] == 0
    assert not files.list_files(SID, OWNER)


def test_authorized_live_retry_can_bind_legacy_row(isolated, monkeypatch):
    artifact = _pending(isolated, monkeypatch)
    with sqlite3.connect(isolated / "state/research_artifacts.sqlite3") as conn:
        conn.execute("DELETE FROM publication_outbox")
        conn.execute("UPDATE artifacts SET owner_user_id=0,artifact_size_bytes=0")
    assert recovery.reconcile_publications()["scanned"] == 0
    url = tools._save_artifact("fixture.txt", b"engineering fixture", "text/plain")
    assert url.endswith(artifact["artifact_id"])
    assert _rows(isolated)[0]["state"] == "committed"
    assert _rows(isolated, "artifacts")[0]["owner_user_id"] == OWNER


def test_orphan_audit_is_bounded_and_dry_run_only(isolated):
    tools._db_path()
    root = isolated / "state/artifacts" / SID
    root.mkdir(parents=True)
    orphan = root / "orphan.part"
    orphan.write_bytes(b"incomplete engineering fixture")
    result = recovery.audit_orphan_artifacts(limit=1)
    assert result["dry_run"] is True
    assert result["scanned"] == 1
    assert result["orphans"] == [{"session_id": SID, "name": "orphan.part", "reason": "unindexed_disk_entry"}]
    assert orphan.read_bytes() == b"incomplete engineering fixture"
