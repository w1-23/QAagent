"""File-only P4 checks with real temporary library bytes, SQLite and Deep Agents."""
import contextvars
import hashlib
import json
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace

import pytest

from src.agent.workspace_storage import WorkspaceStorage, WorkspaceConflict, MAX_FILE_BYTES
from src.agent import workspace_tools as tools


@pytest.fixture
def library(tmp_path, monkeypatch):
    # Do not import production configuration (.env/accounts/providers).
    config = ModuleType("src.config")
    config.PROJECT_ROOT = tmp_path
    config.settings = SimpleNamespace(WORKSPACE_DIR="workspace")
    monkeypatch.setitem(sys.modules, "src.config", config)
    import src
    monkeypatch.setattr(src, "config", config, raising=False)
    from src.session import files
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state" / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state" / "session_files")
    files.init_store()
    return files


@pytest.fixture
def live(library, tmp_path, monkeypatch):
    from src.core import jobs, tracing
    monkeypatch.setattr(jobs, "_DB_PATH", tmp_path / "state" / "jobs.sqlite3")
    for name, value in (("_job_id", ""), ("_user_id", None), ("_session_id", "")):
        monkeypatch.setattr(tracing, name, contextvars.ContextVar(name, default=value))
    claim = jobs.claim_job("session-one", "request-one", owner_user_id=17)
    jobs.update_job(claim.job_id, status="running")
    tracing.set_job_id(claim.job_id)
    tracing.set_user_id(17)
    tracing.set_session_id("session-one")
    return jobs, tracing, claim.job_id


def create(store, content="alpha\nbeta\n", path="/workspace/notes.txt"):
    return store.save(path, content=content, expected_version=0, expected_sha256="")["ref"]


def test_real_versions_persist_new_adapter_and_pin_old_bytes(library):
    store = WorkspaceStorage(17, "session-one")
    first = create(store)
    second = WorkspaceStorage(17, "session-one").save(first["path"], old_string="alpha",
        new_string="gamma", expected_version=first["version"], expected_sha256=first["sha256"])["ref"]
    assert second["version"] == 2 and second["file_id"] != first["file_id"]
    fresh = WorkspaceStorage(17, "session-one")
    assert fresh.read(first["path"], version=1)["text"] == "alpha\nbeta\n"
    assert fresh.read(first["path"])["text"] == "gamma\nbeta\n"
    assert first["sha256"] == hashlib.sha256(b"alpha\nbeta\n").hexdigest()
    for ref in (first, second):
        assert library.file_dependencies(ref["file_id"], 17)[0]["sha256"] == ref["sha256"]
        with library._connect() as conn:
            row = conn.execute("SELECT * FROM session_files WHERE file_id=?", (ref["file_id"],)).fetchone()
            assert row["visibility"] == "internal"
            assert Path(row["storage_path"]).is_file()
            conn.execute("BEGIN IMMEDIATE")
            assert library._delete_indexed_file(conn, row) == (False, 0)
    assert library.list_files("session-one", 17) == []  # no accidental public exports


def test_cas_checks_both_fields_and_create_only(library):
    store = WorkspaceStorage(17, "session-one")
    first = create(store)
    for version, digest in ((0, ""), (1, "0" * 64), (2, first["sha256"])):
        with pytest.raises(WorkspaceConflict) as error:
            store.save(first["path"], content="bad", expected_version=version, expected_sha256=digest)
        assert error.value.latest_ref == first
    with pytest.raises(ValueError, match="expected_version"):
        store.save(first["path"], content="bad", expected_version=1, expected_sha256="")
    assert store.read(first["path"])["ref"] == first


def test_cas_competing_publication_cleans_staging(library, monkeypatch):
    store = WorkspaceStorage(17, "session-one")
    first = create(store)
    original = library.register_file
    competing = False
    def race(**kwargs):
        nonlocal competing
        staged = original(**kwargs)
        if not competing:
            competing = True
            store.save(first["path"], content="winner", expected_version=1, expected_sha256=first["sha256"])
        return staged
    monkeypatch.setattr(library, "register_file", race)
    with pytest.raises(WorkspaceConflict):
        store.save(first["path"], content="loser", expected_version=1, expected_sha256=first["sha256"])
    assert store.read(first["path"])["text"] == "winner"
    with library._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM workspace_file_versions").fetchone()[0] == 2
    assert len(list(library.FILES_ROOT.rglob("*.txt"))) == 2


def test_owner_session_isolation_and_readonly_inputs(library):
    store = WorkspaceStorage(17, "session-one")
    ref = create(store)
    uploaded = library.register_file(session_id="session-one", owner_user_id=17,
                                    filename="input.txt", content=b"original")
    path = f"/input/{uploaded['file_id']}/input.txt"
    assert store.read(path)["text"] == "original"
    for other in (WorkspaceStorage(18, "session-one"), WorkspaceStorage(17, "session-two")):
        for target in (ref["path"], path):
            with pytest.raises(LookupError):
                other.read(target)
        assert other.view()[1] == {}
    with pytest.raises(PermissionError, match="readonly"):
        store.save(path, content="changed", expected_version=1, expected_sha256=uploaded["sha256"])
    assert store.read(path)["text"] == "original"


@pytest.mark.parametrize("path", ["/workspace/../secret.txt", "C:/secret.txt", "/etc/passwd",
    "/workspace/.env", "/workspace/a/../../secret.txt", "/workspace/a\\b.txt",
    "/workspace/a.txt:stream", "//workspace/a.txt", "/workspace/a//b.txt", "/workspace/a./b.txt"])
def test_traversal_and_host_paths_rejected(library, path):
    store = WorkspaceStorage(17, "session-one")
    with pytest.raises((ValueError, PermissionError)):
        store.save(path, content="no", expected_version=0, expected_sha256="")


def test_symlink_file_and_directory_denied(library, tmp_path):
    store = WorkspaceStorage(17, "session-one")
    ref = create(store)
    with library._connect() as conn:
        path = Path(conn.execute("SELECT storage_path FROM session_files WHERE file_id=?", (ref["file_id"],)).fetchone()[0])
    original = path.read_bytes()
    target = tmp_path / "synthetic-outside.txt"
    target.write_bytes(original)
    path.unlink()
    try:
        path.symlink_to(target)
    except OSError as exc:
        pytest.skip("OS does not grant symlink creation: " + str(exc))
    with pytest.raises(PermissionError, match="link_denied"):
        store.read(ref["path"])
    path.unlink()
    path.write_bytes(original)
    session_dir = path.parent
    moved = session_dir.with_name("moved-session")
    session_dir.rename(moved)
    session_dir.symlink_to(moved, target_is_directory=True)
    with pytest.raises(PermissionError, match="link_denied"):
        store.read(ref["path"])
    with pytest.raises(PermissionError, match="link_denied"):
        create(store, path="/workspace/new.txt")
    session_dir.unlink()
    moved.rename(session_dir)


def test_forged_storage_row_and_tampered_bytes_denied(library, tmp_path):
    store = WorkspaceStorage(17, "session-one")
    ref = create(store)
    target = tmp_path / "synthetic-outside.txt"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    with library._connect() as conn:
        original = conn.execute("SELECT storage_path FROM session_files WHERE file_id=?", (ref["file_id"],)).fetchone()[0]
        conn.execute("UPDATE session_files SET storage_path=? WHERE file_id=?", (str(target), ref["file_id"]))
    with pytest.raises(PermissionError, match="scope_denied"):
        store.read(ref["path"])
    with library._connect() as conn:
        conn.execute("UPDATE session_files SET storage_path=? WHERE file_id=?", (original, ref["file_id"]))
    Path(original).write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity"):
        store.read(ref["path"])


def test_bounded_unicode_readback_and_oversize_fail_closed(library):
    store = WorkspaceStorage(17, "session-one")
    text = "数值\r\n" * 200 + "x" * 10000
    ref = create(store, text)
    offset, chunks = 0, []
    while True:
        page = store.read(ref["path"], version=1, start_char=offset, max_chars=71)
        assert len(page["text"]) <= 71 and page["ref"] == ref
        chunks.append(page["text"])
        if page["next_start_char"] is None:
            break
        offset = page["next_start_char"]
    assert "".join(chunks) == text
    with pytest.raises(ValueError, match="invalid_page"):
        store.read(ref["path"], max_chars=20001)
    with pytest.raises(ValueError, match="byte_limit"):
        create(store, "字" * MAX_FILE_BYTES, path="/workspace/large.txt")
    oversized = library.register_file(session_id="session-one", owner_user_id=17,
        filename="large.txt", content=b"x" * (MAX_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="limit"):
        store.read(f"/input/{oversized['file_id']}/large.txt")


def test_backend_glob_search_and_full_artifacts(library, live):
    store = WorkspaceStorage(17, "session-one")
    create(store, "needle" * 500 + "\nneedle\n", "/workspace/sub/one.txt")
    create(store, "other", "/workspace/two.md")
    content, artifact = tools.workspace_glob.func("*.txt")
    assert json.loads(content) == artifact
    assert artifact["entries"][0]["ref"]["version"] == 1
    content, artifact = tools.workspace_search.func("needle", max_matches=1)
    assert json.loads(content) == artifact and artifact["truncated"]
    assert artifact["matches"][0]["text_truncated"]
    assert len(artifact["matches"][0]["text"]) == 400
    content, artifact = tools.workspace_read.func("/workspace/sub/one.txt", max_chars=20)
    assert json.loads(content) == artifact and len(artifact["text"]) == 20
    content, artifact = tools.workspace_list.func("/workspace", limit=1)
    assert json.loads(content) == artifact and artifact["next_offset"] == 1


def test_stateless_job_scope_and_cross_turn_session_persistence(library, live):
    jobs, tracing, job = live
    data = json.loads(tools.workspace_write.invoke({"path": "/workspace/test.txt", "content": "retained",
        "expected_version": 0, "expected_sha256": ""}))
    assert data["_tool_outcome"]["status"] == "complete"
    next_job = jobs.claim_job("session-one", "request-two", owner_user_id=17).job_id
    jobs.update_job(next_job, status="running")
    tracing.set_job_id(next_job)
    assert json.loads(tools.workspace_read.invoke({"path": "/workspace/test.txt"}))["text"] == "retained"
    for owner, session in ((18, "session-one"), (17, "session-two")):
        tracing.set_user_id(owner)
        tracing.set_session_id(session)
        denied = json.loads(tools.workspace_list.invoke({}))
        assert denied["_tool_outcome"]["status"] == "failed"
    tracing.set_user_id(17)
    tracing.set_session_id("session-one")
    jobs.update_job(next_job, status="completed")
    assert json.loads(tools.workspace_read.invoke({"path": "/workspace/test.txt"}))["_tool_outcome"]["status"] == "failed"


def test_hardlink_and_symlink_guard_fail_closed(library, tmp_path, monkeypatch):
    import os
    store = WorkspaceStorage(17, "session-one")
    ref = create(store)
    with library._connect() as conn:
        path = Path(conn.execute("SELECT storage_path FROM session_files WHERE file_id=?", (ref["file_id"],)).fetchone()[0])
    alias = tmp_path / "hardlink.txt"
    os.link(path, alias)
    with pytest.raises(ValueError, match="limit_or_type"):
        store.read(ref["path"])
    alias.unlink()
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda p: p == path or original(p))
    with pytest.raises(PermissionError, match="link_denied"):
        store.read(ref["path"])


def test_edit_uniqueness_and_session_limits(library, monkeypatch):
    from src.agent import workspace_storage as module
    store = WorkspaceStorage(17, "session-one")
    ref = create(store, "same same")
    with pytest.raises(ValueError):
        store.save(ref["path"], old_string="same", new_string="different",
                   expected_version=1, expected_sha256=ref["sha256"])
    result = store.save(ref["path"], old_string="same", new_string="different", replace_all=True,
                       expected_version=1, expected_sha256=ref["sha256"])
    assert result["occurrences"] == 2
    monkeypatch.setattr(module, "MAX_FILES", 1)
    with pytest.raises(ValueError, match="count_limit"):
        create(store, "extra", "/workspace/extra.txt")
    monkeypatch.setattr(module, "MAX_VERSIONS", 2)
    latest = result["ref"]
    with pytest.raises(ValueError, match="version_count"):
        store.save(ref["path"], content="extra", expected_version=2, expected_sha256=latest["sha256"])
    with library._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 2


def test_edit_expansion_is_bounded_before_backend_allocation(library, monkeypatch):
    from deepagents.backends.store import StoreBackend
    store = WorkspaceStorage(17, "session-one")
    ref = create(store, "x" * MAX_FILE_BYTES)
    def must_not_edit(*args, **kwargs):
        raise AssertionError("oversize edit reached backend allocation")
    monkeypatch.setattr(StoreBackend, "edit", must_not_edit)
    with pytest.raises(ValueError, match="byte_limit"):
        store.save(ref["path"], old_string="x", new_string="y" * MAX_FILE_BYTES,
                   replace_all=True, expected_version=1, expected_sha256=ref["sha256"])
    assert store.read(ref["path"])["ref"] == ref


def test_view_byte_budget_and_failed_publish_rollback(library, monkeypatch):
    from src.agent import workspace_storage as module
    store = WorkspaceStorage(17, "session-one")
    create(store, "12345")
    monkeypatch.setattr(module, "MAX_VIEW_BYTES", 7)
    with pytest.raises(ValueError, match="view_byte_limit"):
        create(store, "678", "/workspace/extra.txt")
    with library._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM session_files").fetchone()[0] == 1
    uploaded = library.register_file(session_id="session-one", owner_user_id=17,
        filename="input.txt", content=b"12345678")
    with pytest.raises(ValueError, match="view_byte_limit"):
        store.view()
    assert store.read("/workspace/notes.txt")["text"] == "12345"


def test_schema_denies_identity_injection_and_returns_conflict_artifact(library, live):
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        tools.workspace_list.invoke({"owner": 18})
    _, saved = tools.workspace_write.func("/workspace/test.txt", "one", 0, "")
    content, conflict = tools.workspace_write.func("/workspace/test.txt", "two", 0, "")
    assert json.loads(content) == conflict
    assert conflict["latest_ref"] == saved["ref"] and conflict["write_applied"] is False
    assert conflict["_tool_outcome"]["status"] == "not_run"


def test_registration_modes_and_no_execution_surface(library, monkeypatch):
    from src.agent.capabilities import CapabilityCatalog
    from src.tools import registry
    monkeypatch.setattr(registry, "_TOOL_SPECS", {})
    catalog = CapabilityCatalog()
    tools.register_workspace_tools(catalog)
    assert tools.WORKSPACE_TOOL_NAMES == {"workspace_list", "workspace_glob", "workspace_search",
                                          "workspace_read", "workspace_write", "workspace_edit"}
    for name in tools.WORKSPACE_TOOL_NAMES:
        item = catalog.resolve(name, set(tools.WORKSPACE_TOOL_NAMES))
        spec = registry.get_tool_spec(name)
        mutates = name in {"workspace_write", "workspace_edit"}
        assert catalog.execution_mode(name) == ("sequential" if mutates else "parallel")
        assert spec.is_readonly is not mutates
        assert spec.recovery_mode == ("never" if mutates else "local_read")
        assert spec.write_scope == ("session_artifact" if mutates else None)
        assert item.response_format == "content_and_artifact"
