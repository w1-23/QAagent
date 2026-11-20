"""HTTP contract tests for the invite/application auth endpoints (A2).

A synthetic FastAPI app is built from the production middleware and route
handlers, backed by a real temporary SQLite auth database. No network, external
service, or live database is used.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.testclient import TestClient

from src import auth
from src.api import main
from src.core.account_admission import AccountUnavailable
from src.core.account_leases import AccountDeletionPending

ORIGIN = "http://testserver"

ADMIN_PASSWORD = "Citrus-{{SECRET_f3nq8zta}}-Admin1"
USER_PASSWORD = "Citrus-{{SECRET_x2v9wpco}}-User1"
APPLICANT_PASSWORD = "Citrus-{{SECRET_q4m8zrvk}}-Apply1"
NEW_PASSWORD = "Citrus-{{SECRET_n7c2bkla}}-Next1"


def _register_body(username: str, password: str, invite_code: str, **extra):
    body = {"username": username, "password": password, "avatar_preset": "01",
            "invite_code": invite_code}
    body.update(extra)
    return body


def _cookies(token: str) -> dict:
    return {"citrus_auth": token}


def _write() -> dict:
    return {"Origin": ORIGIN}


def _probe(request: Request):
    return {"ok": True}


def _session_probe(session_id: str, request: Request):
    return {"session_id": session_id}


def _job_probe(job_id: str, request: Request):
    return {"job_id": job_id}


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state" / "accounts.sqlite3")
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    auth.init_store()

    app = FastAPI()
    app.middleware("http")(main.account_auth_guard)
    app.add_exception_handler(RequestValidationError, main.sanitized_validation_error)
    routes = (
        ("/api/v2/auth/register", main.register_account, ["POST"]),
        ("/api/v2/auth/login", main.login_account, ["POST"]),
        ("/api/v2/auth/logout", main.logout_account, ["POST"]),
        ("/api/v2/auth/password/change", main.change_account_password, ["POST"]),
        ("/api/v2/auth/password/recovery-codes", main.issue_recovery_codes, ["POST"]),
        ("/api/v2/auth/password/recover", main.recover_account_password, ["POST"]),
        ("/api/v2/auth/me", main.current_account, ["GET"]),
        ("/api/v2/auth/account", main.delete_account, ["DELETE"]),
        ("/api/v2/admin/users", main.admin_list_users, ["GET"]),
        ("/api/v2/admin/users/{user_id}/reset-password", main.admin_reset_password, ["POST"]),
        ("/api/v2/admin/users/{user_id}/active", main.admin_set_user_active, ["POST"]),
        ("/api/v2/admin/applications", main.admin_list_applications, ["GET"]),
        ("/api/v2/admin/applications/{application_id}/approve",
         main.admin_approve_application, ["POST"]),
        ("/api/v2/admin/applications/{application_id}/reject",
         main.admin_reject_application, ["POST"]),
        ("/api/v2/admin/invites", main.admin_list_invites, ["GET"]),
        ("/api/v2/admin/invites", main.admin_create_invite, ["POST"]),
        ("/api/v2/admin/invites/{invite_id}/revoke", main.admin_revoke_invite, ["POST"]),
        ("/api/v2/probe", _probe, ["POST"]),
        ("/sessions/{session_id}", _session_probe, ["GET"]),
        ("/jobs/{job_id}", _job_probe, ["GET"]),
    )
    for path, endpoint, methods in routes:
        app.add_api_route(path, endpoint, methods=methods)
    return SimpleNamespace(client=TestClient(app), tmp=tmp_path)


def _make_admin(username="admin-user"):
    user_id = auth.create_local_account(username, ADMIN_PASSWORD, role="admin")
    user, token = auth.login(username, ADMIN_PASSWORD)
    assert user["user_id"] == user_id and token
    return user_id, token


def _make_user(username="member-user", password=USER_PASSWORD):
    user_id = auth.create_local_account(username, password)
    user, token = auth.login(username, password)
    assert user["user_id"] == user_id and token
    return user_id, token


def _make_invite(admin_id, admin_token):
    invite = auth.issue_invite(actor_user_id=admin_id, actor_token=admin_token)
    assert invite["code"] and invite["status"] == "issued"
    return invite


# ---------------------------------------------------------------- AUTH ids

def test_auth08_anonymous_paths_reachable_without_session(api):
    client = api.client
    assert client.post("/api/v2/auth/logout").status_code == 200
    response = client.post("/api/v2/auth/register",
                           json=_register_body("anon-user", APPLICANT_PASSWORD, "a" * 32))
    assert response.status_code == 400
    response = client.post("/api/v2/auth/login",
                           json={"username": "nobody", "password": USER_PASSWORD})
    assert response.status_code == 401
    assert "用户名或密码错误" in response.json()["detail"]
    response = client.post("/api/v2/auth/password/recover",
                           json={"username": "nobody", "recovery_code": "r_" + "a" * 32,
                                 "new_password": NEW_PASSWORD})
    assert response.status_code == 401


def test_auth09_cookie_writes_require_same_origin(api):
    _, token = _make_user()
    client = api.client
    assert client.post("/api/v2/probe", cookies=_cookies(token)).status_code == 403
    assert client.post("/api/v2/probe", cookies=_cookies(token),
                       headers={"Origin": "http://evil.example"}).status_code == 403
    assert client.post("/api/v2/probe", cookies=_cookies(token),
                       headers=_write()).status_code == 200
    # Cookie-bearing anonymous writes also require a trusted same origin.
    response = client.post("/api/v2/auth/login", cookies=_cookies(token),
                           json={"username": "member-user", "password": USER_PASSWORD})
    assert response.status_code == 403
    # Logout stays callable so a stale cookie can always be cleared.
    assert client.post("/api/v2/auth/logout", cookies=_cookies(token)).status_code == 200


def test_auth10_admin_prefix_requires_admin(api):
    _, token = _make_user()
    response = api.client.get("/api/v2/admin/users", cookies=_cookies(token))
    assert response.status_code == 403


def test_auth11_session_ownership(api, monkeypatch):
    _, token = _make_user()

    class StubSessions:
        @staticmethod
        def session_belongs_to(session_id, owner_user_id):
            return False

    monkeypatch.setattr(main, "session_manager", StubSessions())
    response = api.client.get("/sessions/foreign-session", cookies=_cookies(token))
    assert response.status_code == 404


def test_auth12_job_ownership(api, monkeypatch):
    _, token = _make_user()
    from src.core import jobs as jobs_mod
    monkeypatch.setattr(jobs_mod, "get_job", lambda job_id: None)
    response = api.client.get("/jobs/foreign-job", cookies=_cookies(token))
    assert response.status_code == 404


def test_auth13_account_lease_failure_maps_to_409(api, monkeypatch):
    _, token = _make_user()
    import src.core.account_leases as leases

    @contextmanager
    def unavailable(owner):
        raise AccountUnavailable("closed")
        yield None

    monkeypatch.setattr(leases, "account_lease", unavailable)
    response = api.client.post("/api/v2/probe", cookies=_cookies(token), headers=_write())
    assert response.status_code == 409
    assert response.json()["code"] == "account_closed"


def test_auth14_register_rate_limit(api):
    client = api.client
    body = _register_body("rate-user", APPLICANT_PASSWORD, "a" * 32)
    for _ in range(8):
        assert client.post("/api/v2/auth/register", json=body).status_code == 400
    assert client.post("/api/v2/auth/register", json=body).status_code == 429


def test_auth15_login_rate_limit(api):
    client = api.client
    _make_user("limit-user", password=NEW_PASSWORD)
    for i in range(13):
        replies = [client.post("/api/v2/auth/login", json={
            "username": name, "password": USER_PASSWORD,
        }) for name in ("limit-user", "unused-user")]
        expected = 401 if i < 12 else 429
        assert [r.status_code for r in replies] == [expected, expected]
        assert replies[0].json() == replies[1].json()
        assert all("set-cookie" not in r.headers for r in replies)


def test_auth22_host_mismatch_ignores_forwarded_host(api):
    _, token = _make_user()
    response = api.client.post("/api/v2/probe", cookies=_cookies(token), headers={
        "Host": "wrong.example", "Origin": ORIGIN, "X-Forwarded-Host": "testserver",
    })
    assert response.status_code == 403


def test_auth22_invalid_username_stays_data_and_does_not_spend_invite(api):
    admin_id, token = _make_admin()
    code = _make_invite(admin_id, token)["code"]
    invalid = "invalid'fixture"
    response = api.client.post("/api/v2/auth/register", json=_register_body(invalid, USER_PASSWORD, code))
    assert response.status_code in {400, 422}
    assert code not in response.text
    assert api.client.post("/api/v2/auth/login", json={
        "username": invalid, "password": USER_PASSWORD,
    }).status_code == 401
    assert not auth.list_applications(status="pending")
    assert api.client.post("/api/v2/auth/register", json=_register_body("fresh-fixture", USER_PASSWORD, code)).status_code == 202


def test_auth16_admin_disable_revokes_sessions(api):
    _, admin_token = _make_admin()
    user_id, user_token = _make_user()
    response = api.client.post(f"/api/v2/admin/users/{user_id}/active",
                               json={"active": False}, cookies=_cookies(admin_token),
                               headers=_write())
    assert response.status_code == 200 and response.json()["active"] is False
    assert auth.user_for_token(user_token) is None
    with auth._connect() as conn:
        count = conn.execute("SELECT count(*) FROM browser_sessions WHERE user_id=?",
                             (user_id,)).fetchone()[0]
    assert count == 0


def test_auth17_deleting_account_cannot_be_enabled(api):
    _, admin_token = _make_admin()
    user_id, _ = _make_user()
    with auth._connect() as conn:
        conn.execute("UPDATE users SET active=0 WHERE user_id=?", (user_id,))
        conn.execute("UPDATE account_admission SET deleting=1 WHERE owner=?", (user_id,))
    response = api.client.post(f"/api/v2/admin/users/{user_id}/active",
                               json={"active": True}, cookies=_cookies(admin_token),
                               headers=_write())
    assert response.status_code == 409
    assert response.json()["code"] == "account_deletion_pending"


def test_auth18_admin_reset_revokes_sessions(api):
    _, admin_token = _make_admin()
    user_id, user_token = _make_user()
    response = api.client.post(
        f"/api/v2/admin/users/{user_id}/reset-password",
        json={"current_password": ADMIN_PASSWORD}, cookies=_cookies(admin_token),
        headers=_write())
    assert response.status_code == 200
    temporary = response.json()["temporary_password"]
    assert temporary and response.headers["cache-control"] == "no-store"
    assert auth.user_for_token(user_token) is None
    user, token = auth.login("member-user", temporary)
    assert user["user_id"] == user_id and token


def test_auth20_cli_reset_preserves_status(api):
    user_id, user_token = _make_user()
    temporary, ok = auth.reset_password(user_id)
    assert ok and temporary
    assert auth.user_by_id(user_id)["active"] is True
    assert auth.user_for_token(user_token) is None


def test_auth21_cli_create_has_no_session(api):
    user_id = auth.create_local_account("cli-user", USER_PASSWORD)
    with auth._connect() as conn:
        sessions = conn.execute("SELECT count(*) FROM browser_sessions WHERE user_id=?",
                                (user_id,)).fetchone()[0]
        admission = conn.execute("SELECT count(*) FROM account_admission WHERE owner=?",
                                 (user_id,)).fetchone()[0]
    assert sessions == 0 and admission == 1


def test_auth22_cli_disable_revokes_sessions(api):
    user_id, user_token = _make_user()
    assert auth.set_user_status(user_id, False) is True
    assert auth.user_for_token(user_token) is None


def test_auth23_legacy_migration_backfills_approved(tmp_path, monkeypatch):
    db_path = tmp_path / "state" / "accounts.sqlite3"
    db_path.parent.mkdir(parents=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        "CREATE TABLE users (user_id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "username TEXT NOT NULL COLLATE NOCASE UNIQUE, salt BLOB NOT NULL, "
        "password_hash BLOB NOT NULL, role TEXT NOT NULL DEFAULT 'user', "
        "active INTEGER NOT NULL DEFAULT 1, daily_limit INTEGER NOT NULL DEFAULT 30, "
        "created_at TEXT NOT NULL);"
        "INSERT INTO users(username,salt,password_hash,created_at) "
        "VALUES('legacy',x'00',x'00','2020-01-01T00:00:00+00:00');"
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(auth, "DB_PATH", db_path)
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    auth.init_store()
    with auth._connect() as conn:
        status = conn.execute(
            "SELECT approval_status FROM users WHERE username='legacy'").fetchone()[0]
        admission = conn.execute("SELECT count(*) FROM account_admission").fetchone()[0]
        marker = conn.execute(
            "SELECT count(*) FROM auth_schema_migrations WHERE version=1").fetchone()[0]
    assert status == "approved" and admission == 1 and marker == 1


def test_auth24_deletion_pending_and_retry_token_scope(api, monkeypatch):
    user_id, token = _make_user()
    auth.retain_deletion_retry(token, user_id)
    with auth._connect() as conn:
        conn.execute("UPDATE users SET active=0 WHERE user_id=?", (user_id,))
    assert auth.user_for_token(token) is None

    def raise_pending(*args, **kwargs):
        raise AccountDeletionPending("busy")

    monkeypatch.setattr(auth, "delete_user_account", raise_pending)
    response = api.client.request("DELETE", "/api/v2/auth/account",
                                  json={"password": USER_PASSWORD},
                                  cookies=_cookies(token), headers=_write())
    assert response.status_code == 202
    assert response.json()["status"] == "deletion_pending"
    assert response.json()["code"] == "account_work_in_flight"
    assert response.headers["retry-after"] == "2"
    # The retry token is scoped to DELETE /account and is not general auth.
    assert api.client.get("/api/v2/auth/me", cookies=_cookies(token)).status_code == 401


# ------------------------------------------------------------- HTTP contracts

def test_register_consumes_invite_and_returns_202_without_cookie(api):
    admin_id, admin_token = _make_admin()
    invite = _make_invite(admin_id, admin_token)
    response = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("applicant", APPLICANT_PASSWORD, invite["code"]))
    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == "pending" and payload["application_id"]
    assert "set-cookie" not in response.headers
    assert api.client.post("/api/v2/auth/login",
                           json={"username": "applicant", "password": APPLICANT_PASSWORD}
                           ).status_code == 401
    # One-time invite: a second claim fails generically.
    second = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("applicant2", APPLICANT_PASSWORD, invite["code"]))
    assert second.status_code == 400


def test_register_username_unavailable_returns_409(api):
    admin_id, admin_token = _make_admin()
    invite = _make_invite(admin_id, admin_token)
    response = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("admin-user", APPLICANT_PASSWORD, invite["code"]))
    assert response.status_code == 409
    assert response.json()["code"] == "username_unavailable"


def test_register_sensitive_validation_does_not_echo_secrets(api):
    response = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("applicant", APPLICANT_PASSWORD, "a" * 32, privileged=True))
    assert response.status_code == 422
    text = response.text
    assert APPLICANT_PASSWORD not in text
    assert "{{SECRET_q4m8zrvk}}" not in text
    assert "a" * 32 not in text


def test_admin_review_flow(api):
    admin_id, admin_token = _make_admin()
    invite = _make_invite(admin_id, admin_token)
    registered = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("applicant", APPLICANT_PASSWORD, invite["code"]))
    application_id = registered.json()["application_id"]

    listing = api.client.get("/api/v2/admin/applications", cookies=_cookies(admin_token))
    assert listing.status_code == 200
    page = listing.json()
    assert set(page) == {"applications", "limit", "offset", "next_offset"}
    assert any(row["application_id"] == application_id for row in page["applications"])

    approved = api.client.post(
        f"/api/v2/admin/applications/{application_id}/approve", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert approved.status_code == 200
    assert approved.json()["application"]["status"] == "approved"
    # Same decision is idempotent; the opposite decision conflicts.
    again = api.client.post(
        f"/api/v2/admin/applications/{application_id}/approve", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert again.status_code == 200
    conflict = api.client.post(
        f"/api/v2/admin/applications/{application_id}/reject", json={"reason": "later"},
        cookies=_cookies(admin_token), headers=_write())
    assert conflict.status_code == 409
    missing = api.client.post(
        "/api/v2/admin/applications/unknown/approve", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert missing.status_code == 404

    assert api.client.post(
        "/api/v2/auth/login",
        json={"username": "applicant", "password": APPLICANT_PASSWORD}).status_code == 200


def test_admin_invite_flow(api):
    _, admin_token = _make_admin()
    created = api.client.post("/api/v2/admin/invites", json={},
                              cookies=_cookies(admin_token), headers=_write())
    assert created.status_code == 201
    invite = created.json()["invite"]
    assert invite["code"] and invite["status"] == "issued"
    assert created.headers["cache-control"] == "no-store"

    listing = api.client.get("/api/v2/admin/invites", cookies=_cookies(admin_token))
    assert listing.status_code == 200
    page = listing.json()
    assert set(page) == {"invites", "limit", "offset", "next_offset"}
    assert all("code" not in row for row in page["invites"])

    revoked = api.client.post(
        f"/api/v2/admin/invites/{invite['invite_id']}/revoke", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert revoked.status_code == 200 and revoked.json()["invite"]["status"] == "revoked"
    repeat = api.client.post(
        f"/api/v2/admin/invites/{invite['invite_id']}/revoke", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert repeat.status_code == 200
    missing = api.client.post(
        "/api/v2/admin/invites/unknown/revoke", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert missing.status_code == 404


def test_revoke_used_or_expired_invite_conflicts(api):
    admin_id, admin_token = _make_admin()
    used = _make_invite(admin_id, admin_token)
    registered = api.client.post(
        "/api/v2/auth/register",
        json=_register_body("applicant", APPLICANT_PASSWORD, used["code"]))
    assert registered.status_code == 202
    response = api.client.post(
        f"/api/v2/admin/invites/{used['invite_id']}/revoke", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert response.status_code == 409 and response.json()["code"] == "invite_conflict"

    expired = _make_invite(admin_id, admin_token)
    with auth._connect() as conn:
        conn.execute("UPDATE account_invites SET expires_at=? WHERE invite_id=?",
                     ("2000-01-01T00:00:00+00:00", expired["invite_id"]))
    response = api.client.post(
        f"/api/v2/admin/invites/{expired['invite_id']}/revoke", json={},
        cookies=_cookies(admin_token), headers=_write())
    assert response.status_code == 409


def test_password_change_requires_reauth_and_revokes_sessions(api):
    _, token = _make_user()
    wrong = api.client.post(
        "/api/v2/auth/password/change",
        json={"current_password": "definitely-wrong", "new_password": NEW_PASSWORD},
        cookies=_cookies(token), headers=_write())
    assert wrong.status_code == 403
    changed = api.client.post(
        "/api/v2/auth/password/change",
        json={"current_password": USER_PASSWORD, "new_password": NEW_PASSWORD},
        cookies=_cookies(token), headers=_write())
    assert changed.status_code == 200
    assert changed.json() == {"status": "login_required"}
    set_cookie = changed.headers.get("set-cookie", "").lower()
    assert "citrus_auth=" in set_cookie and "max-age=0" in set_cookie
    assert auth.user_for_token(token) is None
    assert api.client.post(
        "/api/v2/auth/login",
        json={"username": "member-user", "password": NEW_PASSWORD}).status_code == 200


def test_recovery_codes_and_recover_flow(api):
    _, token = _make_user()
    issued = api.client.post(
        "/api/v2/auth/password/recovery-codes",
        json={"current_password": USER_PASSWORD}, cookies=_cookies(token), headers=_write())
    assert issued.status_code == 200
    codes = issued.json()["codes"]
    assert len(codes) == 8 and issued.headers["cache-control"] == "no-store"

    admin_id, admin_token = _make_admin()
    _make_user('other-member')
    invite = _make_invite(admin_id, admin_token)
    for username, code in (('other-member', codes[0]), ('member-user', invite['code'])):
        refused = api.client.post('/api/v2/auth/password/recover',
                                  json={'username': username, 'recovery_code': code,
                                        'new_password': NEW_PASSWORD})
        assert refused.status_code == 401
        assert 'set-cookie' not in refused.headers

    recovered = api.client.post(
        "/api/v2/auth/password/recover",
        json={"username": "member-user", "recovery_code": codes[0],
              "new_password": NEW_PASSWORD})
    assert recovered.status_code == 200
    assert recovered.json() == {"status": "login_required"}
    assert "set-cookie" not in recovered.headers
    assert auth.user_for_token(token) is None
    assert api.client.post(
        "/api/v2/auth/login",
        json={"username": "member-user", "password": NEW_PASSWORD}).status_code == 200

    api.client.cookies.clear()
    reused = api.client.post(
        "/api/v2/auth/password/recover",
        json={"username": "member-user", "recovery_code": codes[0],
              "new_password": NEW_PASSWORD})
    ghost = api.client.post(
        "/api/v2/auth/password/recover",
        json={"username": "ghost-user", "recovery_code": "r_" + "a" * 32,
              "new_password": NEW_PASSWORD})
    assert reused.status_code == 401 and ghost.status_code == 401
    assert reused.json() == ghost.json()


def test_admin_reset_password_flow(api):
    admin_id, admin_token = _make_admin()
    user_id, user_token = _make_user()
    wrong = api.client.post(
        f"/api/v2/admin/users/{user_id}/reset-password",
        json={"current_password": "definitely-wrong"}, cookies=_cookies(admin_token),
        headers=_write())
    assert wrong.status_code == 403
    forbidden = api.client.post(
        f"/api/v2/admin/users/{admin_id}/reset-password",
        json={"current_password": ADMIN_PASSWORD}, cookies=_cookies(admin_token),
        headers=_write())
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "admin_target_forbidden"
    reset = api.client.post(
        f"/api/v2/admin/users/{user_id}/reset-password",
        json={"current_password": ADMIN_PASSWORD}, cookies=_cookies(admin_token),
        headers=_write())
    assert reset.status_code == 200
    temporary = reset.json()["temporary_password"]
    assert temporary and reset.headers["cache-control"] == "no-store"
    assert auth.user_for_token(user_token) is None
    user, token = auth.login("member-user", temporary)
    assert user["user_id"] == user_id and token
    missing = api.client.post(
        "/api/v2/admin/users/99999/reset-password",
        json={"current_password": ADMIN_PASSWORD}, cookies=_cookies(admin_token),
        headers=_write())
    assert missing.status_code == 404


def test_reauth_rate_limit_returns_429(api):
    _, token = _make_user()
    body = {"current_password": "definitely-wrong", "new_password": NEW_PASSWORD}
    for _ in range(10):
        response = api.client.post("/api/v2/auth/password/change", json=body,
                                   cookies=_cookies(token), headers=_write())
        assert response.status_code == 403
    assert api.client.post("/api/v2/auth/password/change", json=body,
                           cookies=_cookies(token), headers=_write()).status_code == 429


def test_login_sets_expected_cookie_flags(api):
    auth.create_local_account("flag-user", USER_PASSWORD)
    response = api.client.post("/api/v2/auth/login",
                               json={"username": "flag-user", "password": USER_PASSWORD})
    assert response.status_code == 200
    set_cookie = response.headers.get("set-cookie", "").lower()
    assert "citrus_auth=" in set_cookie
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie
    assert "path=/" in set_cookie
    assert "max-age=604800" in set_cookie


@pytest.mark.parametrize("origin", ["http://[", "ftp://testserver", "http://testserver/path"])
def test_auth22_rejects_malformed_origin(api, origin):
    client = api.client
    response = client.post("/api/v2/auth/login", headers={"Origin": origin},
                           json={"username": "example_user", "password": USER_PASSWORD})
    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_pending_and_rejected_accounts_cannot_access_business_routes(api):
    admin_id, admin_token = _make_admin()
    _, member_token = _make_user()
    invite = _make_invite(admin_id, admin_token)
    response = api.client.post('/api/v2/auth/register', json=_register_body(
        'applicant', APPLICANT_PASSWORD, invite['code']))
    assert response.status_code == 202
    application_id = response.json()['application_id']
    review = f'/api/v2/admin/applications/{application_id}'
    user_id = auth.list_applications(status='pending')[0]['user_id']
    assert api.client.post(review + '/approve', json={}, cookies=_cookies(member_token),
                           headers=_write()).status_code == 403
    client = TestClient(main.app)

    for expected_status in ('pending', 'rejected'):
        if expected_status == 'rejected':
            rejected = api.client.post(review + '/reject', json={'reason': 'fixture'},
                                       cookies=_cookies(admin_token), headers=_write())
            assert rejected.status_code == 200
            assert rejected.json()['application']['status'] == 'rejected'
        enable = api.client.post(f'/api/v2/admin/users/{user_id}/active', json={'active': True},
                                 cookies=_cookies(admin_token), headers=_write())
        assert enable.status_code == 409 and enable.json()['code'] == 'account_not_approved'
        login = client.post('/api/v2/auth/login', json={'username': 'applicant',
                                                       'password': APPLICANT_PASSWORD})
        assert login.status_code == 401 and 'set-cookie' not in login.headers
        # Even a syntactically valid legacy token row must not grant admission.
        with auth._connect() as conn:
            token = auth._insert_session(conn, user_id)
        client.cookies.set('citrus_auth', token)
        for path in ('/api/v2/session/sample/messages', '/api/v2/files/sample/download',
                     '/api/v2/session/sample/workspace-files', '/jobs/sample',
                     '/api/v2/admin/applications'):
            assert client.get(path).status_code == 401, path
        client.cookies.clear()

    assert api.client.post('/api/v2/auth/register', json=_register_body(
        'second-applicant', APPLICANT_PASSWORD, invite['code'])).status_code == 400


def test_registration_requires_invite_and_rejects_role_override(api):
    admin_id, token = _make_admin()
    invite = _make_invite(admin_id, token)
    body = _register_body('applicant', APPLICANT_PASSWORD, invite['code'])
    without_invite = {k: v for k, v in body.items() if k != 'invite_code'}
    assert api.client.post('/api/v2/auth/register', json=without_invite).status_code == 422
    response = api.client.post('/api/v2/auth/register', json={**body, 'role': 'admin'})
    assert response.status_code == 422 and APPLICANT_PASSWORD not in response.text
    assert api.client.post('/api/v2/auth/register', json=body).status_code == 202
