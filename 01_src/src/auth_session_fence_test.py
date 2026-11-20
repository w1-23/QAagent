"""Credential verification must not outlive a password or admission change."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from src import auth


@pytest.mark.parametrize("mutation", ["reset", "disable_then_enable"])
def test_in_flight_login_cannot_sign_after_credentials_or_admission_change(tmp_path, monkeypatch, mutation):
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state" / "accounts.sqlite3")
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    auth.init_store()
    original_password = "sample-password-before"
    next_password = "sample-password-after"
    owner = auth.create_local_account("fixture-user", original_password)
    _, original_token = auth.login("fixture-user", original_password)
    assert auth.user_for_token(original_token)["user_id"] == owner

    checked = Event()
    may_sign = Event()
    verify = auth._matches_password

    def pause_after_verification(row, password):
        result = verify(row, password)
        checked.set()
        assert may_sign.wait(10)
        return result

    with monkeypatch.context() as patch:
        patch.setattr(auth, "_matches_password", pause_after_verification)
        with ThreadPoolExecutor(max_workers=1) as worker:
            pending = worker.submit(auth.login, "fixture-user", original_password)
            try:
                assert checked.wait(10)
                if mutation == "reset":
                    assert auth.reset_password(owner, next_password) == (None, True)
                else:
                    assert auth.set_user_status(owner, False)
                    assert auth.set_user_status(owner, True)
            finally:
                may_sign.set()
            assert pending.result(timeout=10) == (None, None)

    assert auth.user_for_token(original_token) is None
    with auth._connect() as conn:
        assert conn.execute("SELECT count(*) FROM browser_sessions WHERE user_id=?", (owner,)).fetchone()[0] == 0
    valid_password = next_password if mutation == "reset" else original_password
    user, new_token = auth.login("fixture-user", valid_password)
    assert user["user_id"] == owner
    assert auth.user_for_token(new_token)["user_id"] == owner
