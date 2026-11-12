"""One exact retry reuses a version; changed bytes never overwrite downloads."""
from __future__ import annotations

import hashlib
import sqlite3

import pytest

from src.core.deliverable_check import DeliverableRejected
from src.core.tracing import (
    set_session_id, set_user_id, set_client_request_id, set_current_tool_name)
from src.research import tools
from src.session import files


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    from src.session.manager import session_manager
    monkeypatch.setattr(session_manager, "get_session_owner", lambda sid: 101)
    set_session_id("session-publish-001")
    set_user_id(101)
    set_client_request_id("request-001")
    set_current_tool_name("export_table")
    yield tmp_path
    set_session_id("")
    set_user_id(None)
    set_client_request_id("")
    set_current_tool_name("")


def _saved(url):
    return tools.resolve_artifact("session-publish-001", url.rsplit("/", 1)[1])["path"]


def test_exact_retry_and_changed_output_have_consistent_versions(isolated):
    first = tools._save_artifact("数据.csv", b"name,value\r\na,1\r\n", "text/csv")
    assert tools._save_artifact("数据.csv", b"name,value\r\na,1\r\n", "text/csv") == first
    second = tools._save_artifact("数据.csv", b"name,value\r\na,2\r\n", "text/csv",
                                   replace_in_request=True)
    assert second != first
    assert _saved(first).read_bytes().endswith(b"a,1\r\n")
    assert _saved(second).read_bytes().endswith(b"a,2\r\n")
    assert tools._save_artifact("数据.csv", b"name,value\r\na,2\r\n", "text/csv") == second
    catalog = files.list_files("session-publish-001", 101)
    assert len(catalog) == 2
    for entry in catalog:
        resolved = files.get_account_file(entry["file_id"], 101)
        assert hashlib.sha256(resolved["path"].read_bytes()).hexdigest() == entry["sha256"]
    with sqlite3.connect(isolated / "state/research_artifacts.sqlite3") as conn:
        assert sorted(r[0] for r in conn.execute("SELECT version FROM artifacts")) == [1, 2]


def test_independent_turn_with_same_content_has_current_turn_attribution(isolated):
    first = tools._save_artifact("记录.txt", b"same bytes", "text/plain")
    set_client_request_id("request-002")
    second = tools._save_artifact("记录.txt", b"same bytes", "text/plain")
    assert first != second
    verified = tools.validate_artifact_links([second], session_id="session-publish-001",
                                             owner_user_id=101, request_id="request-002")
    assert verified[0]["valid"]


def test_append_is_idempotent_and_preserves_the_original(isolated):
    first = tools._save_artifact("数据.csv", b"name,value\r\na,1\r\n", "text/csv")
    second = tools._save_artifact("数据.csv", b"name,value\r\nb,2\r\n", "text/csv", append=True)
    assert first != second
    assert _saved(first).read_bytes() == b"name,value\r\na,1\r\n"
    assert _saved(second).read_bytes() == b"name,value\r\na,1\r\nb,2\r\n"
    assert tools._save_artifact("数据.csv", b"name,value\r\nb,2\r\n", "text/csv", append=True) == second
    assert len(files.list_files("session-publish-001", 101)) == 2


def test_registration_failure_is_visible_and_exact_retry_recovers(isolated, monkeypatch):
    original = files.register_file
    monkeypatch.setattr(files, "register_file", lambda **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(DeliverableRejected, match="artifact_registration_failed"):
        tools._save_artifact("记录.txt", b"saved bytes", "text/plain")
    monkeypatch.setattr(files, "register_file", original)
    url = tools._save_artifact("记录.txt", b"saved bytes", "text/plain")
    assert _saved(url).read_bytes() == b"saved bytes"
    assert len(files.list_files("session-publish-001", 101)) == 1


def test_append_rejects_corrupt_batch_before_any_publication(isolated):
    tools._save_artifact("报告.html", b"<html><body>complete</body></html>", "text/html")
    with pytest.raises(DeliverableRejected):
        tools._save_artifact("报告.html", b"<html><body>cut", "text/html", append=True)
    assert len(files.list_files("session-publish-001", 101)) == 1


def test_append_uses_the_producing_tools_file_not_another_tools_same_name(isolated):
    set_current_tool_name("export_paper_analysis")
    tools._save_artifact("记录.txt", b"report body\n", "text/plain")
    set_current_tool_name("export_table")
    tools._save_artifact("记录.txt", b"table body\n", "text/plain")
    merged = tools._save_artifact("记录.txt", b"table extra\n", "text/plain", append=True)
    assert _saved(merged).read_bytes() == b"table body\n\ntable extra\n"
    set_current_tool_name("export_research_notes")
    with pytest.raises(DeliverableRejected, match="append_source_mismatch"):
        tools._save_artifact("记录.txt", b"notes extra\n", "text/plain", append=True)
