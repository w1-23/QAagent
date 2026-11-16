"""Current figure retention and retirement of legacy history in temporary stores."""
import json
import sqlite3
import time
from types import SimpleNamespace

import pytest

from src.figures import store

OWNER = 991895
OTHER = OWNER + 1
SESSION = "synthetic_retention_session"


@pytest.fixture(autouse=True)
def isolated_stores(tmp_path, monkeypatch):
    from src import auth, config
    from src.core import account_leases
    from src.session import files

    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(config, "settings", SimpleNamespace(RESEARCH_SCRIPT_IDLE_DAYS=7))
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})
    monkeypatch.setattr(account_leases, "_states", {})
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "files")
    monkeypatch.setattr(files, "migrate_legacy_files_for_account", lambda owner: 0)
    monkeypatch.setattr(store, "_STATE_DIR", tmp_path / "figures")
    files.init_store()
    token = account_leases.set_current_account_lease(None)
    yield files
    account_leases.reset_current_account_lease(token)


def project(owner=OWNER, **kwargs):
    return store.create_project(owner_user_id=owner, session_id=SESSION, **kwargs)["figure_id"]


def upload(files, value, owner=OWNER):
    return files.register_file(session_id=SESSION, owner_user_id=owner,
                               filename="synthetic.txt", content=value.encode())


def save(figure_id, revision, item=None, owner=OWNER, **fields):
    if item is not None:
        fields["exports_json"] = {"svg": {"file_id": item["file_id"]}}
    return store.update_project(figure_id, owner, expected_revision=revision - 1,
                                render_version=revision, **fields)


def pin_rows(owner=OWNER):
    with store._connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT * FROM figure_project_pins WHERE owner_user_id=? ORDER BY resource_id", (owner,))]


def no_history():
    with store._connect() as conn:
        assert not conn.execute("SELECT 1 FROM sqlite_master WHERE name='figure_revisions'").fetchone()
    assert not hasattr(store, "get_revision")
    assert not hasattr(store, "list_revisions")


def legacy_history(fid, old_file):
    """Seed the previous schema and a retained old dependency without real data."""
    resource_id, _ = store._pin_files(OWNER, fid, [old_file["file_id"]])
    current = store.get_project(fid, OWNER)
    with store._connect() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS figure_revisions (figure_id TEXT, owner_user_id INTEGER, "
                     "revision INTEGER, snapshot_json TEXT, created_at REAL)")
        conn.execute("INSERT INTO figure_revisions VALUES (?,?,?,?,?)",
                     (fid, OWNER, 1, json.dumps(current), time.time()))
        conn.execute("UPDATE figure_projects SET pins_version=1 WHERE figure_id=?", (fid,))
        store._record_pin(conn, resource_id, fid, OWNER, {}, SESSION)
    return resource_id


def test_only_current_exports_remain_pinned(isolated_stores):
    files = isolated_stores
    first, second = upload(files, "first"), upload(files, "second")
    fid = project(title="Original", source={"script": "original plotting source"})
    assert save(fid, 1, first)
    assert save(fid, 2, second)
    assert store.update_project(fid, OWNER, title="Renamed")
    current = store.get_project(fid, OWNER)
    assert current["source_json"] == {"script": "original plotting source"}
    assert current["render_version"] == 2
    assert current["exports_json"]["svg"]["file_id"] == second["file_id"]
    assert not files.is_file_referenced(first["file_id"], OWNER)
    assert files.get_account_file(first["file_id"], OWNER) is not None
    assert files.is_file_referenced(second["file_id"], OWNER)
    assert len(pin_rows()) == 1
    with pytest.raises(ValueError, match="backwards"):
        store.update_project(fid, OWNER, expected_revision=2, render_version=1)
    no_history()


@pytest.mark.parametrize("field,value", [
    ("source_kind", "script"), ("source_json", {"spec": {"x": 2}}),
    ("manifest_json", {"objects": [{"id": "new"}]}),
    ("overrides_json", [{"gid": "label", "value": "new"}]),
    ("exports_json", {"svg": {"label": "new"}}),
    ("data_refs_json", [{"kind": "synthetic", "value": "new"}]),
])
def test_saved_content_requires_a_new_revision(field, value):
    fid = project()
    assert save(fid, 1)
    before = store.get_project(fid, OWNER)
    with pytest.raises(ValueError, match="new figure revision"):
        store.update_project(fid, OWNER, expected_revision=1, **{field: value})
    assert store.get_project(fid, OWNER) == before
    no_history()


def test_owner_scoped_current_state(isolated_stores):
    files = isolated_stores
    item = upload(files, "owned")
    fid = project()
    assert save(fid, 1, item)
    assert store.get_project(fid, OTHER) is None
    assert not save(fid, 2, owner=OTHER)
    assert not store.delete_project(fid, OTHER)
    assert files.is_file_referenced(item["file_id"], OWNER)
    assert store.delete_project(fid, OWNER)
    assert store.get_project(fid, OWNER) is None
    assert pin_rows() == []


def test_metadata_reuses_current_pins_and_current_files_survive_ttl(isolated_stores):
    files = isolated_stores
    first, second = upload(files, "first"), upload(files, "second")
    fid = project()
    assert save(fid, 1, first)
    assert save(fid, 2, second)
    pins = pin_rows()
    for index in range(3):
        assert store.update_project(fid, OWNER, title=f"Title {index}")
    assert pin_rows() == pins
    assert files.delete_account_file(first["file_id"], OWNER)
    assert not files.delete_account_file(second["file_id"], OWNER)
    with files._connect() as conn:
        conn.execute("UPDATE session_files SET last_accessed_at=0")
    assert files.cleanup_idle_sessions(1)["expired_records"] == 0
    assert store.delete_project(fid, OWNER)
    assert files.delete_account_file(second["file_id"], OWNER)


@pytest.mark.parametrize("failure", ["stale", "race", "insert"])
def test_failed_update_releases_only_provisional_pins(isolated_stores, monkeypatch, failure):
    files = isolated_stores
    old, provisional = upload(files, "old"), upload(files, "provisional")
    fid = project()
    assert save(fid, 1, old)
    before, pins = store.get_project(fid, OWNER), pin_rows()
    if failure == "insert":
        with store._connect() as conn:
            conn.execute("CREATE TRIGGER fail_pin BEFORE INSERT ON figure_project_pins "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic pin failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic pin failure"):
            save(fid, 2, provisional)
    elif failure == "race":
        original_pin = store._pin_files
        def interleaved_pin(*args, **kwargs):
            result = original_pin(*args, **kwargs)
            assert save(fid, 2, title="Concurrent winner")
            return result
        monkeypatch.setattr(store, "_pin_files", interleaved_pin)
        assert not save(fid, 2, provisional)
        before = store.get_project(fid, OWNER)
        assert before["title"] == "Concurrent winner"
    else:
        assert not save(fid, 3, provisional)
    assert store.get_project(fid, OWNER) == before
    assert pin_rows() == pins
    assert files.is_file_referenced(old["file_id"], OWNER)
    assert not files.is_file_referenced(provisional["file_id"], OWNER)


def test_purge_only_releases_target_owner(isolated_stores):
    files = isolated_stores
    fid, foreign = project(), project(OTHER)
    first, second = upload(files, "first"), upload(files, "second")
    other_file = upload(files, "foreign", OTHER)
    assert save(fid, 1, first)
    assert save(fid, 2, second)
    assert save(foreign, 1, other_file, owner=OTHER)
    assert store.purge_account(OWNER) == 1
    assert store.purge_account(OWNER) == 0
    assert pin_rows() == []
    for item in (first, second):
        assert not files.is_file_referenced(item["file_id"], OWNER)
    assert files.is_file_referenced(other_file["file_id"], OTHER)
    assert store.get_project(foreign, OTHER)


def test_failed_delete_cleanup_keeps_retryable_pin_records(isolated_stores, monkeypatch):
    files = isolated_stores
    item = upload(files, "current")
    fid = project()
    assert save(fid, 1, item)
    release = store._release_pin
    def fail(*args, **kwargs):
        raise OSError("synthetic cleanup failure")
    monkeypatch.setattr(store, "_release_pin", fail)
    with pytest.raises(OSError, match="synthetic cleanup failure"):
        store.delete_project(fid, OWNER)
    assert store.get_project(fid, OWNER) is None
    assert len(pin_rows()) == 1
    monkeypatch.setattr(store, "_release_pin", release)
    assert store.migrate_resource_references()["released_abandoned"] == 1
    assert pin_rows() == []
    assert not files.is_file_referenced(item["file_id"], OWNER)


def test_current_script_frozen_tables_and_dataset_parent_closure_survive_saves(isolated_stores):
    import pandas as pd
    from src.research import script_assets as assets
    from src.research.analysis_results import persist_prepared_dataset

    files = isolated_stores
    source = upload(files, "x\n1\n")
    script = assets.save_script(OWNER, SESSION, name="synthetic.py", source="x=1")["script_ref"]
    parent = persist_prepared_dataset(OWNER, pd.DataFrame({"x": [1]}), session_id=SESSION,
        parents=[{"kind": "input_snapshot", "file_id": source["file_id"], "sha256": source["sha256"]}],
        execution_id="synthetic-parent", script_sha256=script["sha256"])
    dataset = persist_prepared_dataset(OWNER, pd.DataFrame({"x": [1]}), session_id=SESSION,
        parents=[parent.ref], execution_id="synthetic-child", script_sha256=script["sha256"])
    frozen = upload(files, "frozen numerical table")
    frozen_ref = {"file_id": frozen["file_id"], "sha256": frozen["sha256"]}
    source_state = {"script_ref": script, "script": "x=1", "frozen_render": {"input_snapshots": [frozen_ref]}}
    fid = project(source_kind="script", source=source_state,
                  data_refs=[dataset.ref, {"file_id": frozen["file_id"]}])
    assert save(fid, 1, upload(files, "render1"))
    assert save(fid, 2, upload(files, "render2"))
    assert store.get_project(fid, OWNER)["source_json"] == source_state
    assert assets.cleanup_scripts(now=time.time() + 8 * 86400)["expired_scripts"] == 0
    dependencies = (source["file_id"], parent.payload_file_ref, dataset.payload_file_ref, frozen["file_id"])
    for file_id in dependencies:
        assert files.is_file_referenced(file_id, OWNER)
    assert len(pin_rows()) == 1
    assert store.purge_account(OWNER) == 1
    assert assets.cleanup_scripts(now=time.time() + 8 * 86400)["expired_scripts"] == 1
    for file_id in dependencies:
        assert not files.is_file_referenced(file_id, OWNER)


@pytest.mark.parametrize("failure", ["stale", "insert"])
def test_failed_update_releases_new_script_and_preserves_current_script(failure):
    from src.research import script_assets as assets

    first = assets.save_script(OWNER, SESSION, name="first.py", source="x=1")["script_ref"]
    second = assets.save_script(OWNER, SESSION, name="second.py", source="x=2")["script_ref"]
    fid = project(source_kind="script", source={"script_ref": first})
    assert save(fid, 1)
    pins, before = pin_rows(), store.get_project(fid, OWNER)
    if failure == "insert":
        with store._connect() as conn:
            conn.execute("CREATE TRIGGER fail_pin BEFORE INSERT ON figure_project_pins "
                         "BEGIN SELECT RAISE(ABORT, 'synthetic script failure'); END")
        with pytest.raises(sqlite3.IntegrityError, match="synthetic script failure"):
            save(fid, 2, source_json={"script_ref": second})
    else:
        assert not save(fid, 3, source_json={"script_ref": second})
    assert pin_rows() == pins
    assert store.get_project(fid, OWNER) == before
    with assets._connect() as conn:
        scripts = [dict(row) for row in conn.execute("SELECT * FROM pins")]
    assert len(scripts) == 1
    assert scripts[0]["script_id"] == first["id"]


def test_delete_racing_provisional_pin_does_not_leave_orphan(isolated_stores, monkeypatch):
    files = isolated_stores
    old, provisional = upload(files, "old"), upload(files, "provisional")
    fid = project()
    assert save(fid, 1, old)
    original_pin = store._pin_files
    def deleting_pin(*args, **kwargs):
        result = original_pin(*args, **kwargs)
        assert store.delete_project(fid, OWNER)
        return result
    monkeypatch.setattr(store, "_pin_files", deleting_pin)
    assert not save(fid, 2, provisional)
    assert store.get_project(fid, OWNER) is None
    assert pin_rows() == []
    for item in (old, provisional):
        assert not files.is_file_referenced(item["file_id"], OWNER)


def test_cleanup_failure_acknowledges_save_and_retry_keeps_concurrent_new_pins(isolated_stores, monkeypatch):
    files = isolated_stores
    first, second, third = [upload(files, name) for name in ("first", "second", "third")]
    fid = project()
    assert save(fid, 1, first)
    release = store._release_pin
    def fail(*args, **kwargs):
        raise OSError("synthetic cleanup failure")
    monkeypatch.setattr(store, "_release_pin", fail)
    assert save(fid, 2, second)
    assert store.get_project(fid, OWNER)["render_version"] == 2
    assert sum(pin["obsolete"] for pin in pin_rows()) == 1
    raced = False
    def racing_release(*args, **kwargs):
        nonlocal raced
        if not raced:
            raced = True
            # A save lands after migration captured the obsolete identity.
            assert save(fid, 3, third)
        return release(*args, **kwargs)
    monkeypatch.setattr(store, "_release_pin", racing_release)
    store.migrate_resource_references()
    assert store.get_project(fid, OWNER)["render_version"] == 3
    assert len(pin_rows()) == 1 and not pin_rows()[0]["obsolete"]
    assert files.is_file_referenced(third["file_id"], OWNER)
    for item in (first, second):
        assert not files.is_file_referenced(item["file_id"], OWNER)


def test_migration_pins_current_before_releasing_history_and_is_idempotent(isolated_stores, monkeypatch):
    files = isolated_stores
    old, current = upload(files, "old"), upload(files, "current")
    fid = project()
    assert save(fid, 1, current)
    old_pin = legacy_history(fid, old)
    files.replace_resource_references(OWNER, "figure", "other-consumer", [old["file_id"]])
    release = store._release_pin
    def guarded_release(owner, resource_id, **kwargs):
        active = [p for p in pin_rows() if not p["obsolete"]]
        assert len(active) == 1 and active[0]["resource_id"] != resource_id
        assert files.is_file_referenced(current["file_id"], OWNER)
        return release(owner, resource_id, **kwargs)
    monkeypatch.setattr(store, "_release_pin", guarded_release)
    before = store.get_project(fid, OWNER)
    assert store.migrate_resource_references()["migrated"] == 1
    assert store.get_project(fid, OWNER) == {**before, "pins_version": 2}
    assert len(pin_rows()) == 1
    assert pin_rows()[0]["resource_id"] != old_pin
    assert [p["resource_id"] for p in files.file_dependencies(old["file_id"], OWNER)] == ["other-consumer"]
    assert files.get_account_file(old["file_id"], OWNER) is not None
    assert store.migrate_resource_references() == {"migrated": 0, "released_abandoned": 0}
    no_history()


def test_failed_migration_leaves_legacy_snapshots_and_pins(isolated_stores, monkeypatch):
    files = isolated_stores
    fid = project()
    assert save(fid, 1, upload(files, "current"))
    legacy_history(fid, upload(files, "old"))
    before, pins = store.get_project(fid, OWNER), pin_rows()
    def fail(*args, **kwargs):
        raise OSError("synthetic pin failure")
    monkeypatch.setattr(store, "_pin_files", fail)
    with pytest.raises(OSError, match="synthetic pin failure"):
        store.migrate_resource_references()
    assert store.get_project(fid, OWNER) == before
    assert pin_rows() == pins
    with store._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM figure_revisions").fetchone()[0] == 1


def test_migration_racing_save_releases_only_its_provisional_pins(isolated_stores, monkeypatch):
    files = isolated_stores
    old, current, newer = [upload(files, name) for name in ("old", "current", "newer")]
    fid = project()
    assert save(fid, 1, current)
    legacy_history(fid, old)
    pin = store._pin_files
    raced = False
    def racing_pin(*args, **kwargs):
        nonlocal raced
        result = pin(*args, **kwargs)
        if not raced:
            raced = True
            assert save(fid, 2, newer)
        return result
    monkeypatch.setattr(store, "_pin_files", racing_pin)
    assert store.migrate_resource_references()["migrated"] == 0
    assert store.get_project(fid, OWNER)["render_version"] == 2
    assert len(pin_rows()) == 1
    assert files.is_file_referenced(newer["file_id"], OWNER)
    for item in (old, current):
        assert not files.is_file_referenced(item["file_id"], OWNER)
    no_history()
