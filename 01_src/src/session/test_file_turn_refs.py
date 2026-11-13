"""Phase 1 file-attribution guards.

Phase 1 added four moving parts that are not "pure static binding" and that the
reviewer asked to verify separately: the request-scoped contextvars, the two
new ``session_files`` columns behind an ``ALTER TABLE`` migration, the turn ->
file association table, and the bounded ``action_hint`` text that reaches the
timeline. Each test below fails for one concrete way those can go wrong:
context bleeding between concurrent requests, a non-repeatable migration, a
file that cannot be found from the request that produced it, cross-account
access through the association, a reference that outlives its file, and a hint
that echoes credentials or local paths.
"""

import asyncio
import sqlite3
import time

import pytest

from src.core import tracing
from src.core.progress_bus import tool_action_hint
from src.session import files


@pytest.fixture
def file_store(tmp_path, monkeypatch):
    state = tmp_path / "state"
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", state / "session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", state / "session_files")
    files.init_store()
    return tmp_path


def _turn_refs():
    with sqlite3.connect(files.DB_PATH) as conn:
        return conn.execute(
            "SELECT session_id, owner_user_id, client_request_id, file_id "
            "FROM session_file_turn_refs").fetchall()


# --------------------------------------------------------------- 1. isolation

def test_request_context_survives_threadpool_without_crossing():
    """Two concurrent requests must not see each other's id or tool name.

    Tools run their blocking work in ``asyncio.to_thread``; contextvars are
    copied into the worker, so the attribution seen inside the thread is the one
    bound by the request that spawned it - and a later request cannot overwrite
    it.
    """
    seen: dict[str, tuple[str, str]] = {}

    async def one(tag: str) -> None:
        tracing.set_client_request_id(f"turn-{tag}")
        tracing.set_current_tool_name(f"tool_{tag}")
        await asyncio.sleep(0)                     # interleave both requests
        seen[tag] = await asyncio.to_thread(
            lambda: (tracing.get_client_request_id(), tracing.get_current_tool_name()))
        await asyncio.sleep(0)
        # Still the same after the other coroutine ran past its own binding.
        assert tracing.get_client_request_id() == f"turn-{tag}"

    async def main() -> None:
        await asyncio.gather(one("a"), one("b"))

    asyncio.run(main())
    assert seen == {"a": ("turn-a", "tool_a"), "b": ("turn-b", "tool_b")}


def test_worker_thread_inherits_but_cannot_leak_back():
    """A thread must not be able to change the reader's own attribution."""
    tracing.set_client_request_id("turn-main")
    tracing.set_current_tool_name("main_tool")

    async def main() -> None:
        def mutate() -> None:
            tracing.set_client_request_id("turn-worker")
            tracing.set_current_tool_name("worker_tool")

        await asyncio.to_thread(mutate)
        # Worker wrote into its own copy of the context.
        assert tracing.get_client_request_id() == "turn-main"
        assert tracing.get_current_tool_name() == "main_tool"

    asyncio.run(main())


# --------------------------------------------------------------- 2. migration

def test_init_store_is_repeatable_and_preserves_existing_rows(file_store):
    sid = "session_migr_0001"
    info = files.register_file(
        session_id=sid, owner_user_id=7, filename="a.txt",
        content=b"keep me", mime="text/plain",
        client_request_id="turn-1", producer_tool="deepseek_fetch")

    files.init_store()          # second call must be a no-op, not an error
    files.init_store()

    rows = files.list_files(sid, owner_user_id=7)
    assert [row["file_id"] for row in rows] == [info["file_id"]]
    with sqlite3.connect(files.DB_PATH) as conn:
        cols = [row[1] for row in conn.execute("PRAGMA table_info(session_files)")]
    assert cols.count("client_request_id") == 1
    assert cols.count("producer_tool") == 1


def test_init_store_upgrades_a_pre_phase1_database(tmp_path, monkeypatch):
    """An older database has no attribution columns; startup must add them."""
    state = tmp_path / "state"
    state.mkdir()
    db = state / "session_files.sqlite3"
    # A real object inside the managed upload root, so the row stays visible.
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", db)
    monkeypatch.setattr(files, "FILES_ROOT", state / "session_files")
    legacy_dir = files._session_dir("session_legacy_1", 3)
    legacy_dir.mkdir(parents=True, exist_ok=True)
    legacy_path = legacy_dir / "old.txt"
    legacy_path.write_bytes(b"old")
    with sqlite3.connect(db) as conn:
        conn.execute("""
            CREATE TABLE session_files (
                file_id TEXT PRIMARY KEY, session_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL DEFAULT 0, filename TEXT NOT NULL,
                origin TEXT NOT NULL, mime_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
                storage_path TEXT NOT NULL, artifact_id TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL, last_accessed_at REAL NOT NULL)""")
        conn.execute(
            "INSERT INTO session_files VALUES('f1','session_legacy_1',3,'old.txt',"
            "'uploaded','text/plain',3,'abc',?,'',1.0,1.0)",
            (str(legacy_path.resolve()),))

    files.init_store()
    files.init_store()          # repeatable: no "duplicate column" error

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT file_id, filename, client_request_id, producer_tool "
            "FROM session_files WHERE file_id='f1'").fetchone()
    assert row == ("f1", "old.txt", "", "")
    assert files.list_files("session_legacy_1", owner_user_id=3)[0]["filename"] == "old.txt"


# --------------------------------------------------- 3. findable from request

def test_a_file_is_found_under_the_request_that_produced_it(file_store):
    sid = "session_turn_0001"
    artifact_dir = files._artifact_session_dir(sid)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    (artifact_dir / "report.xlsx").write_bytes(b"xlsx bytes")
    made = files.register_file(
        session_id=sid, owner_user_id=11, filename="report.xlsx",
        storage_path=str(artifact_dir / "report.xlsx"),
        mime="application/vnd.ms-excel",
        origin=files.ORIGIN_GENERATED, artifact_id="a" * 32,
        client_request_id="turn-export", producer_tool="export_research_excel")

    found = files.list_turn_files(sid, 11, ["turn-export"])
    assert [row["file_id"] for row in found["turn-export"]] == [made["file_id"]]
    assert found["turn-export"][0]["producer_tool"] == "export_research_excel"

    # A different request id of the same session must not claim the file.
    # (The helper only returns keys that actually resolved to a file.)
    other = files.list_turn_files(sid, 11, ["turn-other"])
    assert other.get("turn-other", []) == []


def test_link_turn_files_attaches_existing_library_files(file_store):
    sid = "session_turn_0002"
    up = files.register_file(session_id=sid, owner_user_id=5, filename="in.csv",
                             content=b"a,b\n1,2\n", mime="text/csv")
    assert files.list_turn_files(sid, 5, ["turn-2"]).get("turn-2", []) == []

    files.link_turn_files(sid, 5, "turn-2", [up["file_id"]])
    linked = files.list_turn_files(sid, 5, ["turn-2"])["turn-2"]
    assert [row["file_id"] for row in linked] == [up["file_id"]]


# ------------------------------------------------------------ 4. cross-account

def test_turn_files_are_invisible_to_another_account(file_store):
    sid = "session_acct_0001"
    mine = files.register_file(
        session_id=sid, owner_user_id=21, filename="private.xlsx",
        content=b"secret", client_request_id="turn-mine",
        producer_tool="export_research_excel")

    assert files.list_turn_files(sid, 99, ["turn-mine"]).get("turn-mine", []) == []
    assert files.list_turn_files(sid, 21, ["turn-mine"])["turn-mine"]

    # Owning the other account must not let it delete my file either.
    assert files.delete_file(sid, mine["file_id"], owner_user_id=99) is False
    assert files.get_file(sid, mine["file_id"], owner_user_id=21) is not None


# --------------------------------------------- 5. no reference outlives the file

def test_deleting_a_file_leaves_no_turn_reference(file_store):
    sid = "session_gone_0001"
    info = files.register_file(
        session_id=sid, owner_user_id=31, filename="gone.txt",
        content=b"bye", client_request_id="turn-x", producer_tool="local_hybrid_search")
    assert len(_turn_refs()) == 1

    assert files.delete_file(sid, info["file_id"], owner_user_id=31) is True
    assert _turn_refs() == []


def test_removing_a_generated_panel_leaves_no_turn_reference(file_store):
    sid = "session_gone_0002"
    artifact_dir = files._artifact_session_dir(sid)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    path = artifact_dir / "report.html"
    path.write_text("<html></html>", encoding="utf-8")

    info = files.register_file(
        session_id=sid, owner_user_id=32, filename="report.html",
        storage_path=str(path), origin=files.ORIGIN_GENERATED,
        artifact_id="b" * 32, client_request_id="turn-y",
        producer_tool="audit_research_file")
    assert len(_turn_refs()) == 1

    assert files.remove_generated_entry(sid, "b" * 32) is True
    assert _turn_refs() == []
    assert files.get_file(sid, info["file_id"], owner_user_id=32) is None


def test_purge_session_clears_every_reference(file_store):
    sid = "session_gone_0003"
    files.register_file(session_id=sid, owner_user_id=41, filename="u.txt",
                        content=b"1", client_request_id="turn-p")
    files.register_file(session_id=sid, owner_user_id=41, filename="v.txt",
                        content=b"2", client_request_id="turn-p")
    assert len(_turn_refs()) == 2

    files.purge_session(sid, owner_user_id=41)
    assert _turn_refs() == []
    assert files.list_files(sid, owner_user_id=41) == []


def test_cleanup_leaves_no_dangling_reference(file_store):
    sid = "session_gone_0004"
    info = files.register_file(
        session_id=sid, owner_user_id=51, filename="old.bin",
        content=b"kept on disk", client_request_id="turn-c",
        producer_tool="deepseek_fetch")
    old = time.time() - files.DEFAULT_IDLE_DAYS * 86400 - 60
    with sqlite3.connect(files.DB_PATH) as conn:
        conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?",
                     (old, info["file_id"]))

    files.cleanup_idle_sessions()
    assert _turn_refs() == []
    assert files.get_file(sid, info["file_id"], owner_user_id=51) is None


# ------------------------------------------------------------- 6. hint hygiene

def test_action_hint_keeps_the_host_and_drops_the_token():
    hint = tool_action_hint(
        "deepseek_fetch",
        {"url": "https://example.org/paper.pdf?token=sk-live-9f8e7d&sig=abc"})
    assert "example.org" in hint
    assert "sk-live-9f8e7d" not in hint
    assert "token" not in hint


def test_action_hint_does_not_echo_credentials_or_paths():
    hint = tool_action_hint("deepseek_search", {
        "query": "citrus greening", "api_key": "sk-secret-123",
        "authorization": "Bearer abcdef", "file": r"E:\private\secret.xlsx"})
    assert "citrus greening" in hint
    assert "sk-secret-123" not in hint
    assert "Bearer abcdef" not in hint
    assert "secret.xlsx" not in hint


def test_action_hint_is_bounded_and_edge_safe():
    hint = tool_action_hint("deepseek_search", {"query": "word " * 200})
    assert 0 < len(hint) <= 80
    assert tool_action_hint("local_hybrid_search", None) == ""
    assert tool_action_hint("local_hybrid_search", {}) == ""
