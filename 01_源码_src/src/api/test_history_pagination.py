"""S3 回归：历史分页游标 + 文件到对话轮的跳转键。

运行：`python -m pytest src/api/test_history_pagination.py -q`
"""
import os
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from src.api.main import _slice_history            # noqa: E402
from src.session import files as session_files     # noqa: E402


def _items(n, start=1):
    return [{"role": "assistant", "message_id": start + i, "content": f"m{start + i}"}
            for i in range(n)]


def test_newest_page_is_returned_not_oldest():
    window, has_more, next_before = _slice_history(_items(10), 3)
    assert [x["message_id"] for x in window] == [8, 9, 10]
    assert has_more is True
    assert next_before == 8


def test_no_more_when_everything_fits():
    window, has_more, next_before = _slice_history(_items(4), 10)
    assert len(window) == 4
    assert has_more is False
    assert next_before == 0


def test_before_cursor_walks_backwards_without_gaps():
    everything = _items(10)
    first, has_more, cursor = _slice_history(everything, 4)
    assert [x["message_id"] for x in first] == [7, 8, 9, 10]
    assert has_more and cursor == 7
    second, has_more2, cursor2 = _slice_history(everything, 4, cursor)
    assert [x["message_id"] for x in second] == [3, 4, 5, 6]
    assert has_more2 and cursor2 == 3
    third, has_more3, _ = _slice_history(everything, 4, cursor2)
    assert [x["message_id"] for x in third] == [1, 2]
    assert has_more3 is False


def test_cursor_beyond_all_returns_empty_page():
    window, has_more, next_before = _slice_history(_items(5), 3, before=1)
    assert window == [] and has_more is False and next_before == 0


def test_limit_is_never_zero_and_bad_values_do_not_raise():
    assert len(_slice_history(_items(3), 0)[0]) == 1
    assert len(_slice_history(_items(3), -5)[0]) == 1
    assert _slice_history(_items(3), 2, before="not-a-number")[0][-1]["message_id"] == 3


def test_entries_without_message_id_are_still_pageable():
    items = [{"role": "assistant", "content": "legacy"}, *_items(3)]
    window, has_more, _ = _slice_history(items, 2)
    assert [x["message_id"] for x in window] == [2, 3]
    assert has_more is True


def test_row_to_public_exposes_normalized_turn_request_id():
    row = {"file_id": "a" * 32, "session_id": "s1", "filename": "x.pdf",
           "mime_type": "application/pdf", "origin": "uploaded", "size_bytes": 3,
           "sha256": "x", "storage_path": "/tmp/x", "artifact_id": "",
           "created_at": 1.0, "last_accessed_at": 1.0,
           "client_request_id": "crid:abc123"}
    public = session_files._row_to_public(row)
    assert public["turn_request_id"] == "abc123"
    assert public["source_session_id"] == "s1"
    assert "client_request_id" not in public
    assert "storage_path" not in public


def test_row_to_public_keeps_raw_id_when_not_prefixed():
    row = {"file_id": "b" * 32, "session_id": "s2", "filename": "y.csv",
           "mime_type": "text/csv", "origin": "generated", "size_bytes": 1,
           "sha256": "y", "storage_path": "/tmp/y", "artifact_id": "art",
           "created_at": 2.0, "last_accessed_at": 2.0,
           "client_request_id": "plain-request"}
    public = session_files._row_to_public(row)
    assert public["turn_request_id"] == "plain-request"


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except AssertionError as exc:
                fails += 1
                print("FAIL", name, exc)
    print("FAILS:", fails)
    raise SystemExit(1 if fails else 0)
