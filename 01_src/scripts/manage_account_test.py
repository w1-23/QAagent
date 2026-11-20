"""Focused tests for the offline account-management CLI (A4).

Every test runs against a temporary SQLite database: ``auth.DB_PATH`` and
``auth.PROJECT_ROOT`` are monkeypatched so no runtime file is touched.  Password
input is supplied through a fake ``getpass``; the real interactive path (hidden
input plus confirmation) is what the CLI exercises.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from src import auth

PASSWORD = "initialpass123"
NEW_PASSWORD = "newpassword123"
ADMIN_PASSWORD = "bosspassword123"


def _load_cli():
    path = Path(__file__).resolve().with_name("manage_account.py")
    spec = importlib.util.spec_from_file_location("manage_account_cli", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def cli(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state" / "accounts.sqlite3")
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    module = _load_cli()
    module.init_store()
    return module


def _feed(monkeypatch, cli, values):
    """Replace the CLI's getpass module with a scripted stub."""
    sequence = list(values)
    state = {"index": 0}

    def fake_getpass(prompt=""):
        value = sequence[min(state["index"], len(sequence) - 1)]
        state["index"] += 1
        return value

    monkeypatch.setattr(cli, "getpass", SimpleNamespace(getpass=fake_getpass))


def _user(username):
    return next((u for u in auth.list_users() if u["username"] == username), None)


def test_create_uses_getpass_and_never_echoes_password(cli, monkeypatch, capsys):
    _feed(monkeypatch, cli, [ADMIN_PASSWORD, ADMIN_PASSWORD])
    assert cli.main(["create", "alice", "--admin", "--daily-limit", "12"]) == 0

    user = _user("alice")
    assert user is not None
    assert user["role"] == "admin"
    assert user["active"] is True
    assert user["approval_status"] == "approved"
    assert user["daily_limit"] == 12

    out = capsys.readouterr().out
    assert "已创建管理员账号 alice" in out
    assert ADMIN_PASSWORD not in out


@pytest.mark.parametrize("argv", [
    ["create", "bob", "--password", "leakme12345"],
    ["reset", "bob", "--password", "leakme12345"],
])
def test_password_is_not_a_cli_option(cli, argv, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main(argv)
    assert info.value.code == 2
    assert _user("bob") is None


@pytest.mark.parametrize("command", ["create", "reset"])
def test_help_has_no_password_option(cli, command, capsys):
    with pytest.raises(SystemExit) as info:
        cli.main([command, "--help"])
    assert info.value.code == 0
    assert "--password" not in capsys.readouterr().out


def test_reset_preserves_disabled_state(cli, monkeypatch, capsys):
    user_id = auth.create_local_account("alice", PASSWORD)
    assert auth.set_user_status(user_id, False) is True

    _feed(monkeypatch, cli, [NEW_PASSWORD, NEW_PASSWORD])
    assert cli.main(["reset", "alice"]) == 0

    user = _user("alice")
    assert user["active"] is False
    assert user["approval_status"] == "approved"
    assert auth.verify_password(user_id, NEW_PASSWORD, allow_disabled=True) is True

    out = capsys.readouterr().out
    assert "保持停用" in out
    assert NEW_PASSWORD not in out


def test_reset_revokes_existing_sessions(cli, monkeypatch):
    auth.create_local_account("alice", PASSWORD)
    logged_in, token = auth.login("alice", PASSWORD)
    assert logged_in and token

    _feed(monkeypatch, cli, [NEW_PASSWORD, NEW_PASSWORD])
    assert cli.main(["reset", "alice"]) == 0

    assert auth.user_for_token(token) is None
    assert _user("alice")["active"] is True


def test_reset_requires_matching_confirmation(cli, monkeypatch, capsys):
    user_id = auth.create_local_account("alice", PASSWORD)
    _feed(monkeypatch, cli, [NEW_PASSWORD, "mismatchpass1", NEW_PASSWORD, NEW_PASSWORD])
    assert cli.main(["reset", "alice"]) == 0

    assert "两次输入不一致" in capsys.readouterr().out
    assert auth.verify_password(user_id, NEW_PASSWORD) is True


def test_reset_unknown_user_never_prompts(cli, monkeypatch, capsys):
    def _boom(prompt=""):
        raise AssertionError("getpass must not run for an unknown account")

    monkeypatch.setattr(cli, "getpass", SimpleNamespace(getpass=_boom))
    assert cli.main(["reset", "ghost"]) == 2
    assert "账号不存在" in capsys.readouterr().out


def test_enable_rejects_pending_application(cli, capsys):
    admin_id = auth.create_local_account("boss", ADMIN_PASSWORD, role="admin")
    invite = auth.issue_invite(actor_user_id=admin_id)
    auth.register("newbie", PASSWORD, invite_code=invite["code"])

    assert cli.main(["enable", "newbie"]) == 2
    user = _user("newbie")
    assert user["active"] is False
    assert user["approval_status"] == "pending"
    assert "审核" in capsys.readouterr().out


def test_disable_revokes_sessions(cli):
    auth.create_local_account("alice", PASSWORD)
    _user_obj, token = auth.login("alice", PASSWORD)
    assert token

    assert cli.main(["disable", "alice"]) == 0
    assert auth.user_for_token(token) is None
    assert _user("alice")["active"] is False


def test_issue_invite_prints_code_once_for_admin(cli, capsys):
    admin_id = auth.create_local_account("boss", ADMIN_PASSWORD, role="admin")
    assert cli.main(["issue-invite", "--admin", "boss"]) == 0

    out = capsys.readouterr().out
    code = out.split("：", 1)[1].splitlines()[0].strip()
    assert len(code) == 32
    assert out.count(code) == 1

    invites = auth.list_invites()
    assert len(invites) == 1
    assert invites[0]["created_by"] == admin_id
    assert invites[0]["status"] == "issued"


def test_issue_invite_requires_active_approved_admin(cli, capsys):
    auth.create_local_account("alice", PASSWORD)
    assert cli.main(["issue-invite", "--admin", "alice"]) == 2
    assert "管理员" in capsys.readouterr().out
    assert auth.list_invites() == []

    admin_id = auth.create_local_account("boss", ADMIN_PASSWORD, role="admin")
    auth.set_user_status(admin_id, False)
    assert cli.main(["issue-invite", "--admin", "boss"]) == 2
    assert auth.list_invites() == []

    assert cli.main(["issue-invite", "--admin", "ghost"]) == 2
    assert "账号不存在" in capsys.readouterr().out


@pytest.mark.parametrize("days", ["0", "31"])
def test_issue_invite_rejects_out_of_range_days(cli, days, capsys):
    auth.create_local_account("boss", ADMIN_PASSWORD, role="admin")
    assert cli.main(["issue-invite", "--admin", "boss", "--days", days]) == 2
    assert "有效期" in capsys.readouterr().out
    assert auth.list_invites() == []


def test_create_duplicate_reports_safe_error(cli, monkeypatch, capsys):
    auth.create_local_account("alice", PASSWORD)
    _feed(monkeypatch, cli, [NEW_PASSWORD, NEW_PASSWORD])
    assert cli.main(["create", "alice"]) == 2

    out = capsys.readouterr().out
    assert "已存在" in out
    assert NEW_PASSWORD not in out


def test_list_shows_accounts_without_secrets(cli, capsys):
    auth.create_local_account("alice", PASSWORD)
    assert cli.main(["list"]) == 0

    out = capsys.readouterr().out
    assert "alice" in out
    assert "已通过" in out
    assert PASSWORD not in out


def test_enable_unknown_user(cli, capsys):
    assert cli.main(["enable", "ghost"]) == 2
    assert "账号不存在" in capsys.readouterr().out


def test_reset_recovers_disabled_admin_locally(cli, monkeypatch):
    admin_id = auth.create_local_account("boss", ADMIN_PASSWORD, role="admin")
    assert auth.set_user_status(admin_id, False) is True

    _feed(monkeypatch, cli, [NEW_PASSWORD, NEW_PASSWORD])
    assert cli.main(["reset", "boss"]) == 0

    admin = _user("boss")
    assert admin["role"] == "admin"
    assert admin["active"] is False
    assert admin["approval_status"] == "approved"
    assert auth.verify_password(admin_id, NEW_PASSWORD, allow_disabled=True) is True


def test_create_refuses_echoing_terminal(cli, monkeypatch, capsys):
    import warnings

    def unavailable(prompt):
        warnings.warn("terminal echo cannot be disabled", cli.GetPassWarning)
        raise AssertionError("must abort before reading a password")

    monkeypatch.setattr(cli, "getpass", SimpleNamespace(getpass=unavailable))
    assert cli.main(["create", "alice"]) == 2
    assert "隐藏密码输入" in capsys.readouterr().out
    assert auth.list_users() == []
