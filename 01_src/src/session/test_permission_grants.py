"""Permission grants are session-scoped unless the operator opts out."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.config import PROJECT_ROOT, settings
from src.session.manager import SessionManager


@pytest.fixture()
def manager(tmp_path, monkeypatch):
    instance = SessionManager()
    monkeypatch.setattr(instance, "db_path", str(tmp_path / "sessions.db"))
    return instance


def test_once_grant_cannot_be_consumed_by_another_session(manager):
    assert manager.grant_permission("session-a", "write_local_file", "once")
    assert manager.consume_grant("session-b", "write_local_file", "") is False
    assert manager.consume_grant("session-a", "write_local_file", "") is True
    assert manager.consume_grant("session-a", "write_local_file", "") is False


def test_session_grant_is_bound_to_its_session(manager):
    assert manager.grant_permission("session-a", "write_local_file", "session")
    assert manager.consume_grant("session-b", "write_local_file", "") is False
    assert manager.consume_grant("session-a", "write_local_file", "") is True


def test_workspace_grant_is_session_bound_by_default(manager, monkeypatch):
    output = Path(PROJECT_ROOT) / settings.WORKSPACE_DIR / "output" / "grant.txt"
    monkeypatch.setattr(settings, "PERMISSION_WORKSPACE_CROSS_SESSION", False)
    assert manager.grant_permission("session-a", "write_local_file", "workspace")
    assert manager.consume_grant("session-b", "write_local_file", str(output)) is False
    assert manager.consume_grant("session-a", "write_local_file", str(output)) is True


def test_workspace_grant_can_cross_sessions_only_when_configured(manager, monkeypatch):
    output = Path(PROJECT_ROOT) / settings.WORKSPACE_DIR / "output" / "grant.txt"
    monkeypatch.setattr(settings, "PERMISSION_WORKSPACE_CROSS_SESSION", True)
    assert manager.grant_permission("session-a", "write_local_file", "workspace")
    assert manager.consume_grant("session-b", "write_local_file", str(output)) is True
