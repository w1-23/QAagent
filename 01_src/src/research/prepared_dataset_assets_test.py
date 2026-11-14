"""Synthetic, temporary-store contracts for private prepared dataset assets."""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import time

import numpy as np
import pandas as pd
import pytest

from src.figures.dataset_codec import encode_frame, decode_frame
from src.research import analysis_results as assets
from src.session import files

OWNER = 982301
SID = "asset_test_session"
SCRIPT_HASH = hashlib.sha256(b"synthetic script").hexdigest()


@pytest.fixture(autouse=True)
def temporary_stores(monkeypatch, tmp_path):
    from src import auth, config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "files")
    monkeypatch.setattr(files, "migrate_legacy_files_for_account", lambda owner: 0)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})
    # GC's existing figure migration uses its own store: keep it in this same
    # temporary root, with no production session/files databases involved.
    from src.figures import store
    store.set_state_dir(tmp_path / "figures")
    files.init_store()
    yield tmp_path
    store.set_state_dir(None)


def prepared(frame=None, *, owner=OWNER, parents=(), **kwargs):
    if frame is None and "typed_payload" not in kwargs:
        frame = pd.DataFrame({"value": [1 / 3, 2 / 7]})
    return assets.persist_prepared_dataset(owner, frame, session_id=SID,
        parents=parents, execution_id="exec-synthetic", script_sha256=SCRIPT_HASH,
        logical_task_id="task-synthetic", **kwargs)


def internal_file(owner=OWNER, content=b"synthetic parent"):
    return files.register_file(session_id=SID, owner_user_id=owner, filename="input.json",
                               content=content, visibility="internal", client_request_id="turn-synthetic")


def frame_with_types():
    return pd.DataFrame({
        "float": pd.Series([1 / 3, -0.0, float("inf"), float("nan")], dtype="float64"),
        "small_float": pd.Series([1 / 7, 2 / 9, np.nan, -0.0], dtype="float32"),
        "big_uint": pd.Series([2**64 - 1, 2**53 + 1, pd.NA, 0], dtype="UInt64"),
        "nullable_int": pd.Series([1, pd.NA, -2, 3], dtype="Int64"),
        "decimal": pd.Series([Decimal("1.23456789012345678901234567890"), None,
                               Decimal("-0.00000"), Decimal("NaN")], dtype="object"),
        "missing": pd.Series([None, pd.NA, pd.NaT, np.nan], dtype="object"),
        "mixed": pd.Series([2**100, 1 / 3, True, "x"], dtype="object"),
        "date": pd.Series([date(2026, 1, 2), None, date(2020, 2, 29), date(2024, 1, 1)], dtype="object"),
        "python_datetime": pd.Series([datetime(2026, 1, 2, tzinfo=timezone(timedelta(hours=5, minutes=30))),
                                       None, datetime(2026, 1, 3), pd.NaT], dtype="object"),
        "timestamp": pd.Series(pd.to_datetime(["2026-01-01T01:02:03.123456789Z", None,
                                                "2026-06-01T02:03:04.999999999Z", None], utc=True)
                                 .tz_convert("America/New_York")),
        "category": pd.Series(pd.Categorical(["a", None, "b", "a"], categories=["b", "a", "unused"], ordered=True)),
        "text": pd.Series(["a", pd.NA, "柑橘", ""], dtype="string"),
        "flag": pd.Series([True, False, pd.NA, True], dtype="boolean"),
    })


def test_codec_preserves_precision_dtypes_missing_categories_and_timezones():
    original = frame_with_types()
    payload = encode_frame(original)
    decoded = decode_frame(json.loads(json.dumps(payload, allow_nan=False)))
    pd.testing.assert_frame_equal(original, decoded)
    assert decoded.loc[0, "mixed"] == 2**100
    assert decoded.loc[0, "big_uint"] == 2**64 - 1
    assert decoded.loc[0, "decimal"].as_tuple() == original.loc[0, "decimal"].as_tuple()
    assert decoded.loc[0, "missing"] is None
    assert decoded.loc[1, "missing"] is pd.NA
    assert decoded.loc[2, "missing"] is pd.NaT
    assert np.signbit(decoded.loc[1, "float"])
    assert encode_frame(decoded) == payload


@pytest.mark.parametrize("index", [pd.Index([2**80, 2**80 + 1], dtype="object", name="identity"),
    pd.date_range("2026-01-01", periods=2, tz="Asia/Shanghai", name="time"),
    pd.CategoricalIndex(["b", "a"], categories=["a", "b", "c"], ordered=True, name="group"),
    pd.RangeIndex(3, 7, 2, name="row")])
def test_codec_restores_nondefault_index(index):
    original = pd.DataFrame({"v": [1 / 7, 2 / 11]}, index=index)
    pd.testing.assert_frame_equal(original, decode_frame(encode_frame(original)))


def test_codec_copies_with_only_figures_directory(temporary_stores):
    import shutil
    source = Path(__file__).parents[1] / "figures"
    copy = temporary_stores / "worker" / "figures"
    shutil.copytree(source, copy, ignore=shutil.ignore_patterns("__pycache__"))
    spec = importlib.util.spec_from_file_location("standalone_dataset_codec", copy / "dataset_codec.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frame = pd.DataFrame({"v": [Decimal("0.1234567890123456789")]})
    pd.testing.assert_frame_equal(frame, module.decode_frame(module.encode_frame(frame)))


@pytest.mark.parametrize("value", [object(), {"unexpected": "mapping"}, [1, 2], complex(1, 2), b"bytes"])
def test_codec_rejects_unsupported_values_instead_of_stringifying(value):
    with pytest.raises(ValueError, match="Unsupported"):
        encode_frame(pd.DataFrame({"v": pd.Series([value], dtype="object")}))


def test_codec_rejects_malformed_json_dtypes_and_unknown_categories():
    payload = encode_frame(pd.DataFrame({"v": [1]}))
    payload["series"][0]["dtype"] = {"kind": "dtype", "name": "__import__('os').system('false')"}
    with pytest.raises(ValueError, match="dtype"):
        decode_frame(payload)
    payload = encode_frame(pd.DataFrame({"v": pd.Categorical(["a"], categories=["a"])}))
    payload["series"][0]["values"][0] = {"t": "str", "v": "unlisted"}
    with pytest.raises(ValueError, match="categories"):
        decode_frame(payload)


def test_prepared_dataset_bytes_once_metadata_ref_and_shared_loader(temporary_stores):
    original = frame_with_types()
    snapshot = prepared(original)
    metadata = snapshot.to_dict()
    assert metadata["dataset_ref"] == snapshot.ref
    assert metadata["owner_user_id"] == OWNER
    assert metadata["execution_id"] == "exec-synthetic"
    assert metadata["script_sha256"] == SCRIPT_HASH
    assert metadata["session_id"] == SID
    assert metadata["logical_task_id"] == "task-synthetic"
    assert "data" not in metadata and "series" not in metadata
    receipt = files.get_account_file(snapshot.payload_file_ref, OWNER, allow_internal=True)
    assert receipt["visibility"] == "internal"
    assert "download_url" not in receipt and "preview_url" not in receipt
    content = receipt["path"].read_bytes()
    assert hashlib.sha256(content).hexdigest() == metadata["payload_sha256"]
    with assets._connect() as conn:
        row = conn.execute("SELECT * FROM scientific_snapshots WHERE id=?", (snapshot.id,)).fetchone()
        assert "series" not in json.loads(row["payload_json"])
        assert row["payload_file_id"] == receipt["file_id"]
    loaded = assets.load_dataset_snapshot(OWNER, snapshot.ref)
    restored = loaded.to_frame()
    restored.attrs.clear()
    pd.testing.assert_frame_equal(original, restored)
    assert files.storage_usage(OWNER)["used_bytes"] == len(content)
    assert files.storage_usage(OWNER)["file_count"] == 1
    assert files.file_dependencies(receipt["file_id"], OWNER) == []  # no self pin
    with pytest.raises(LookupError):
        assets.load_dataset_snapshot(OWNER + 1, {**snapshot.ref, "owner_user_id": OWNER})
    with pytest.raises(ValueError):
        assets.load_dataset_snapshot(OWNER, {**snapshot.ref, "sha256": "0" * 64})


def test_frame_or_typed_payload_and_reject_precision_losing_cast():
    frame = pd.DataFrame({"v": [2**100]})
    payload = encode_frame(frame)
    assert prepared(typed_payload=payload).to_frame().loc[0, "v"] == 2**100
    assert prepared(payload).to_frame().loc[0, "v"] == 2**100
    payload = encode_frame(pd.DataFrame({"v": [1.25]}))
    payload["series"][0]["dtype"] = {"kind": "dtype", "name": "int64"}
    with pytest.raises(ValueError, match="loss"):
        prepared(typed_payload=payload)
    assert files.storage_usage(OWNER)["file_count"] == 2


def test_internal_files_hidden_from_all_normal_lists_reads_and_turns():
    hidden = internal_file()
    visible = files.register_file(session_id=SID, owner_user_id=OWNER, filename="result.txt",
                                  content=b"public", client_request_id="turn-synthetic")
    assert "download_url" not in hidden and "turn_request_id" not in hidden
    assert [f["file_id"] for f in files.list_files(SID, OWNER)] == [visible["file_id"]]
    assert [f["file_id"] for f in files.list_files(SID)] == [visible["file_id"]]
    assert [f["file_id"] for f in files.list_account_files(OWNER)] == [visible["file_id"]]
    assert [f["file_id"] for f in files.list_account_files(OWNER, session_id=SID)] == [visible["file_id"]]
    assert files.get_account_file(hidden["file_id"], OWNER) is None
    assert files.get_file(SID, hidden["file_id"], owner_user_id=OWNER) is None
    assert files.get_file(SID, hidden["file_id"], allow_internal=True) is None
    assert files.get_file(SID, hidden["file_id"], owner_user_id=OWNER, allow_internal=True)
    assert files.get_account_file(hidden["file_id"], OWNER + 1, allow_internal=True) is None
    files.link_turn_files(SID, OWNER, "new-turn", [hidden["file_id"]])
    assert files.list_turn_files(SID, OWNER, ["new-turn"]) == {}
    turns = files.list_turn_files(SID, OWNER, ["turn-synthetic"])
    assert [f["file_id"] for f in turns["turn-synthetic"]] == [visible["file_id"]]
    # Even a stale/manual historical link cannot make an internal asset public.
    with files._connect() as conn:
        conn.execute("INSERT INTO session_file_turn_refs VALUES (?,?,?,?,?)",
                     (SID, OWNER, "stale-turn", hidden["file_id"], time.time()))
    assert files.list_turn_files(SID, OWNER, ["stale-turn"]) == {}


def test_internal_files_count_towards_quota_and_failed_write_has_no_leftovers(monkeypatch):
    monkeypatch.setenv("CITRUS_ACCOUNT_STORAGE_FILES", "1")
    first = prepared()
    with pytest.raises(files.StorageQuotaExceeded):
        prepared()
    assert files.storage_usage(OWNER)["file_count"] == 1
    assert len(list(files.FILES_ROOT.rglob("*.json"))) == 1
    assert assets.load_dataset_snapshot(OWNER, first.ref).id == first.id


def test_old_database_visibility_migration_preserves_public_rows(temporary_stores, monkeypatch):
    path = temporary_stores / "legacy.sqlite3"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE session_files (file_id TEXT PRIMARY KEY,session_id TEXT,owner_user_id INTEGER,"
                     "filename TEXT,origin TEXT,mime_type TEXT,size_bytes INTEGER,sha256 TEXT,storage_path TEXT,"
                     "artifact_id TEXT,created_at REAL,last_accessed_at REAL)")
        conn.execute("INSERT INTO session_files VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("a" * 32, SID, OWNER, "old.txt", "uploaded", "text/plain", 0, "b" * 64, "old", "", 1, 1))
    monkeypatch.setattr(files, "DB_PATH", path)
    files.init_store()
    with files._connect() as conn:
        assert conn.execute("SELECT visibility FROM session_files").fetchone()[0] == "public"


def test_parent_owner_and_hash_validation_rolls_back_failed_publication():
    parent = internal_file()
    foreign = internal_file(OWNER + 1)
    good = {"kind": "input_snapshot", "file_id": parent["file_id"], "sha256": parent["sha256"]}
    base = prepared(parents=[good])
    for bad in [{**good, "sha256": "0" * 64}, {**good, "owner_user_id": OWNER + 1},
                {"kind": "input_snapshot", "file_id": foreign["file_id"], "sha256": foreign["sha256"]},
                {**base.ref, "sha256": "0" * 64}, {"kind": "dataset_snapshot", "id": base.id}]:
        with pytest.raises((ValueError, LookupError)):
            prepared(parents=[bad])
        assert files.storage_usage(OWNER)["file_count"] == 2
    assert prepared(parents=[{**good, "version": 1}]).to_dict()["parents"] == [good]


def test_consumer_pins_multi_parent_transitive_closure_and_metadata_gc():
    a, b = internal_file(content=b"parent A"), internal_file(content=b"parent B")
    a_ref = {"kind": "input_snapshot", "file_id": a["file_id"], "sha256": a["sha256"]}
    b_ref = {"kind": "input_snapshot", "file_id": b["file_id"], "sha256": b["sha256"]}
    left, right = prepared(parents=[a_ref]), prepared(parents=[b_ref])
    joined = prepared(parents=[left.ref, right.ref])
    output = files.register_file(session_id=SID, owner_user_id=OWNER, filename="figure.svg", content=b"<svg/>")
    pinned = assets.pin_dataset_references(OWNER, "figure", "figure-synthetic", [joined.ref],
                                          file_ids=[output["file_id"]])
    assert {r["file_id"] for r in pinned} == {a["file_id"], b["file_id"], left.payload_file_ref,
                                              right.payload_file_ref, joined.payload_file_ref, output["file_id"]}
    future = time.time() + 31 * 86400
    assert assets.cleanup_prepared_datasets(now=future)["expired_datasets"] == 0
    assert not assets.discard_prepared_dataset(OWNER, joined.ref)
    assert assets.load_dataset_snapshot(OWNER, left.ref).id == left.id
    assert assets.release_dataset_references(OWNER, "figure", "figure-synthetic") == 6
    assert assets.release_dataset_references(OWNER, "figure", "figure-synthetic") == 0
    assert assets.cleanup_prepared_datasets(now=future)["expired_datasets"] == 3
    with pytest.raises(LookupError):
        assets.load_dataset_snapshot(OWNER, joined.ref)
    assert files.storage_usage(OWNER)["file_count"] == 3  # inputs and public output remain


def test_failed_repin_keeps_previous_consumer_dependencies():
    existing = prepared()
    assets.pin_dataset_references(OWNER, "figure", "consumer", [existing.ref])
    bad = {**existing.ref, "sha256": "0" * 64}
    with pytest.raises(ValueError):
        assets.pin_dataset_references(OWNER, "figure", "consumer", [bad])
    assert len(files.file_dependencies(existing.payload_file_ref, OWNER)) == 1
    assert assets.cleanup_prepared_datasets(now=time.time() + 31 * 86400)["expired_datasets"] == 0


def test_payload_tamper_blocks_read_and_pin():
    snapshot = prepared()
    entry = files.get_account_file(snapshot.payload_file_ref, OWNER, allow_internal=True)
    entry["path"].write_bytes(b"{}")
    with pytest.raises(ValueError, match="integrity"):
        assets.load_dataset_snapshot(OWNER, snapshot.ref)
    with pytest.raises(ValueError, match="integrity"):
        assets.pin_dataset_references(OWNER, "figure", "consumer", [snapshot.ref])


def test_idle_cleanup_expires_unconsumed_asset_metadata_and_bytes():
    snapshot = prepared()
    with assets._connect() as conn:
        conn.execute("UPDATE scientific_snapshots SET expires_at=? WHERE id=?", (time.time() - 10, snapshot.id))
    result = files.cleanup_idle_sessions()
    assert result["expired_records"] == 1
    assert result["removed_files"] == 1
    with pytest.raises(LookupError):
        assets.load_dataset_snapshot(OWNER, snapshot.ref)
    assert files.storage_usage(OWNER)["file_count"] == 0


def test_asset_retention_is_not_overridden_by_generic_file_ttl():
    snapshot = prepared(ttl_days=90)
    with files._connect() as conn:
        conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?",
                     (time.time() - 60 * 86400, snapshot.payload_file_ref))
    assert files.cleanup_idle_sessions()["expired_records"] == 0
    assert assets.load_dataset_snapshot(OWNER, snapshot.ref).id == snapshot.id


def test_discard_failed_host_publication_is_owner_bound_and_idempotent():
    snapshot = prepared()
    assert not assets.discard_prepared_dataset(OWNER + 1, snapshot.ref)
    assert assets.discard_prepared_dataset(OWNER, snapshot.ref)
    assert not assets.discard_prepared_dataset(OWNER, snapshot.ref)
    assert files.storage_usage(OWNER)["file_count"] == 0


def test_legacy_dtype_read_completes_without_rewriting_old_payload():
    snapshot = assets.persist_dataset_snapshot(OWNER,
        pd.DataFrame({"id": pd.Series([2**63 - 1, None], dtype="Int64"),
                      "group": pd.Categorical(["a", "b"])}), source={})
    with assets._connect() as conn:
        before = dict(conn.execute("SELECT * FROM scientific_snapshots WHERE id=?", (snapshot.id,)).fetchone())
    restored = assets.load_dataset_snapshot(OWNER, snapshot.ref).to_frame()
    assert str(restored["id"].dtype) == "Int64"
    assert restored.loc[0, "id"] == 2**63 - 1
    assert str(restored["group"].dtype) == "category"
    with assets._connect() as conn:
        after = dict(conn.execute("SELECT * FROM scientific_snapshots WHERE id=?", (snapshot.id,)).fetchone())
    assert before == after


def test_metadata_failure_compensates_new_bytes_without_affecting_parent():
    parent = prepared()
    with pytest.raises(ValueError, match="Unsupported"):
        prepared(parents=[parent.ref], source={"unsupported": object()})
    assert files.storage_usage(OWNER)["file_count"] == 1
    assert assets.load_dataset_snapshot(OWNER, parent.ref).id == parent.id


def test_interrupted_publication_orphan_is_collected_by_account_ttl():
    orphan = files.register_file(session_id=SID, owner_user_id=OWNER, filename="orphan.json",
        content=b"{}", visibility="internal", producer_tool="prepared_dataset")
    with files._connect() as conn:
        conn.execute("UPDATE session_files SET last_accessed_at=? WHERE file_id=?",
                     (time.time() - 31 * 86400, orphan["file_id"]))
    result = files.cleanup_idle_sessions()
    assert result["expired_records"] == 1 and result["removed_files"] == 1
    assert files.storage_usage(OWNER)["file_count"] == 0


def test_pin_and_metadata_gc_share_the_file_writer_lock(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    snapshot = prepared()
    entered, allow_pin = Event(), Event()
    original = assets._dataset_dependencies

    def pause_with_writer_lock(*args):
        entered.set()
        assert allow_pin.wait(5)
        return original(*args)

    monkeypatch.setattr(assets, "_dataset_dependencies", pause_with_writer_lock)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pin = pool.submit(assets.pin_dataset_references, OWNER, "figure", "racing-consumer", [snapshot.ref])
        assert entered.wait(5)
        sweep = pool.submit(assets.cleanup_prepared_datasets, now=time.time() + 31 * 86400)
        allow_pin.set()
        assert len(pin.result(timeout=10)) == 1
        assert sweep.result(timeout=10)["expired_datasets"] == 0
    assert assets.load_dataset_snapshot(OWNER, snapshot.ref).id == snapshot.id


def test_purge_owner_retains_other_account_metadata_and_reuses_account_file_purge():
    one, two = prepared(), prepared(owner=OWNER + 1)
    assert assets.purge_owner(OWNER) == 1
    assert files.purge_session(SID, owner_user_id=OWNER, strict=True) == 1
    assert assets.load_dataset_snapshot(OWNER + 1, two.ref).id == two.id
    assert files.get_account_file(one.payload_file_ref, OWNER, allow_internal=True) is None
