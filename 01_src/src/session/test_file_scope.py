"""The file library must mean one thing per URL.

``GET /api/v2/session/{sid}/files`` used to answer with the whole account
library while its path claimed a single session.  That is the same class of
defect as the audit's "don't fake a contract": a caller that trusts the URL gets
another conversation's filenames.  The account-wide listing stays the default at
``/api/v2/files``, and both routes now say which scope they answered with.

Each test fails for one concrete way this can regress:

* the session route quietly going back to account-wide,
* the account route losing its default (breaking the file library page),
* the ``?session_id=`` filter being ignored,
* a foreign session id slipping through the ownership check.
"""

import asyncio
from types import SimpleNamespace

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


def _seed():
    """One file in each of two sessions, both owned by user 7."""
    a = files.register_file(session_id="sess-a" * 4, owner_user_id=7,
                            filename="alpha.csv", content=b"a,b\n1,2\n")
    b = files.register_file(session_id="sess-b" * 4, owner_user_id=7,
                            filename="beta.csv", content=b"c,d\n3,4\n")
    return a, b


def _main():
    from src.api import main
    return main


class _FakeRequest:
    """Only what these routes touch: ``request.state.user``."""

    def __init__(self, user_id):
        self.state = SimpleNamespace(user={"user_id": user_id})


def _owned(monkeypatch, allowed):
    main = _main()
    monkeypatch.setattr(main.session_manager, "session_belongs_to",
                        lambda sid, uid: sid in allowed)


# ------------------------------------------------------------ store-level scope

def test_store_filters_by_session_and_keeps_account_default(file_store):
    a, b = _seed()
    assert a["session_id"] != b["session_id"]

    only_a = files.list_account_files(7, session_id=a["session_id"])
    assert [f["filename"] for f in only_a] == ["alpha.csv"]

    everything = files.list_account_files(7)
    assert sorted(f["filename"] for f in everything) == ["alpha.csv", "beta.csv"]


def test_store_filter_cannot_reach_another_accounts_file(file_store):
    _seed()
    files.register_file(session_id="sess-c" * 4, owner_user_id=8,
                        filename="other.csv", content=b"x\n1\n")
    rows = files.list_account_files(8, session_id="sess-a" * 4)
    assert rows == [], "a session id must not become a cross-account key"


# ------------------------------------------------------------ route-level scope

def test_session_route_lists_only_that_session(file_store, monkeypatch):
    main = _main()
    a, b = _seed()
    _owned(monkeypatch, {a["session_id"], b["session_id"]})

    payload = asyncio.run(
        main.list_session_files(a["session_id"], _FakeRequest(7)))

    assert payload["scope"] == "session"
    assert payload["session_id"] == a["session_id"]
    names = [f["filename"] for f in payload["uploaded"] + payload["generated"]]
    assert names == ["alpha.csv"], "the session route must not leak session B"


def test_account_route_still_defaults_to_the_whole_library(file_store, monkeypatch):
    main = _main()
    _seed()
    _owned(monkeypatch, set())

    payload = asyncio.run(main.list_account_file_library(_FakeRequest(7)))

    assert payload["scope"] == "account"
    assert payload["count"] == 2, "the library page must keep every file"


def test_account_route_honours_the_session_filter(file_store, monkeypatch):
    main = _main()
    a, _b = _seed()
    _owned(monkeypatch, {a["session_id"]})

    payload = asyncio.run(
        main.list_account_file_library(_FakeRequest(7), session_id=a["session_id"]))

    assert payload["scope"] == "session"
    assert payload["session_id"] == a["session_id"]
    assert payload["count"] == 1


def test_filtered_route_still_checks_ownership(file_store, monkeypatch):
    main = _main()
    _seed()
    _owned(monkeypatch, set())  # nothing belongs to the caller

    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main.list_account_file_library(
            _FakeRequest(7), session_id="sess-z" * 4))
    assert excinfo.value.status_code == 404

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(main.list_session_files("sess-z" * 4, _FakeRequest(7)))
    assert excinfo.value.status_code == 404
