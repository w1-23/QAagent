"""Engineering fixtures for immutable, owner-bound scientific provenance."""
from dataclasses import FrozenInstanceError
import hashlib
import sqlite3

import pandas as pd
import pytest

from src.research import analysis_results as results


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch, tmp_path):
    from src import auth, config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": True})


def dataset(owner=991101):
    return results.persist_dataset_snapshot(owner, pd.DataFrame({"mass": [1 / 3, None, 2 / 7], "arm": [1, 2, 1]}),
        source={"file_id": "upload:fixture", "sha256": hashlib.sha256(b"fixture").hexdigest(),
                "sheet": "Sheet1", "header_rows": [2], "version": 1},
        units={"mass": "g"}, design={"experimental_unit": "plant"}, row_ids=[3, 4, 5],
        coverage={"source_truncated": False})


def test_roundtrip_keeps_full_precision_original_rows_and_immutable_values():
    original = dataset()
    assert original.ref["kind"] == "dataset_snapshot"
    loaded = results.load_dataset_snapshot(991101, original.ref)
    assert loaded.to_frame().index.tolist() == [3, 4, 5]
    assert loaded.to_frame().loc[3, "mass"] == 1 / 3
    assert pd.isna(loaded.to_frame().loc[4, "mass"])
    value = loaded.to_dict()
    value["data"][0][0] = 99
    assert loaded.to_dict()["data"][0][0] == 1 / 3
    with pytest.raises(FrozenInstanceError):
        loaded.version = 2
    assert original.to_dict()["source"]["header_rows"] == [2]


def test_analysis_output_points_to_same_immutable_dataset_and_separate_presentation():
    snap = dataset()
    result = results.persist_analysis_result(991101, snap.ref,
        outputs={"mean": 1 / 3, "p": 0.0500000000001}, parameters={"alpha": 0.05},
        design={"unit": "plant"}, exclusions=[{"row_id": 4, "reason": "missing"}],
        coverage={"rows": 2}, presentation={"significant_digits": 3})
    loaded = results.load_analysis_result(991101, result.ref).to_dict()
    assert loaded["dataset_ref"] == snap.ref
    assert loaded["outputs"]["p"] == 0.0500000000001
    assert loaded["outputs"]["mean"] == 1 / 3
    assert loaded["presentation"] == {"significant_digits": 3}
    assert loaded["implementation"]["version"] == results.IMPLEMENTATION_VERSION
    assert loaded["exclusions"][0]["row_id"] == 4


def test_owner_filter_does_not_accept_owner_from_ref():
    snap = dataset()
    with pytest.raises(LookupError, match="not found"):
        results.load_dataset_snapshot(991102, {**snap.ref, "owner_user_id": 991101})
    for bad in [0, -1, None, True, "991101"]:
        with pytest.raises(ValueError):
            results.load_dataset_snapshot(bad, snap.ref)


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("sha256", "bad"),
    ("version", 2), ("version", True), ("kind", "analysis_result")])
def test_forged_ref_cannot_select_another_content_version(field, value):
    snap = dataset()
    with pytest.raises((LookupError, ValueError)):
        results.load_dataset_snapshot(991101, {**snap.ref, field: value})


def test_disk_payload_tamper_is_detected():
    from src.config import PROJECT_ROOT
    snap = dataset()
    with sqlite3.connect(PROJECT_ROOT / "state" / "research_artifacts.sqlite3") as conn:
        conn.execute("UPDATE scientific_snapshots SET payload_json='{}' WHERE id=?", (snap.id,))
    with pytest.raises(ValueError, match="integrity"):
        results.load_dataset_snapshot(991101, snap.ref)


def test_dataset_pins_authorized_source_and_rejects_source_version_change(monkeypatch):
    from src.session import files
    calls = []
    digest = hashlib.sha256(b"fixture").hexdigest()
    monkeypatch.setattr(files, "replace_resource_references", lambda owner, kind, identity, ids:
        calls.append((owner, kind, identity, ids)) or [{"file_id": ids[0], "sha256": digest}])
    monkeypatch.setattr(files, "release_resource_references", lambda *args: calls.append(args))
    source = {"file_id": "registered-owner-file", "sha256": digest}
    snap = results.persist_dataset_snapshot(991101, pd.DataFrame({"v": [1, 2]}), source=source)
    assert calls[0] == (991101, "dataset_snapshot", snap.id, ["registered-owner-file"])
    with pytest.raises(ValueError, match="source"):
        results.persist_dataset_snapshot(991101, pd.DataFrame({"v": [1, 2]}), source={**source, "sha256": "bad"})
    assert len(calls[-1]) == 3  # Failed publication released its source pin.
    other = dataset(991102)
    assert results.purge_owner(991101) == 1
    with pytest.raises(LookupError):
        results.load_dataset_snapshot(991101, snap.ref)
    assert results.load_dataset_snapshot(991102, other.ref).id == other.id


def test_purge_failure_keeps_retry_inventory_and_reads_recheck_account_admission(monkeypatch):
    from src import auth
    from src.session import files
    from src.core.account_leases import AccountUnavailable
    snap = dataset()
    def fail(*args):
        raise OSError("engineering pin release failure")
    monkeypatch.setattr(files, "release_resource_references", fail)
    with pytest.raises(OSError):
        results.purge_owner(991101)
    assert results.load_dataset_snapshot(991101, snap.ref).id == snap.id
    monkeypatch.setattr(auth, "user_by_id", lambda owner: {"id": owner, "active": False})
    with pytest.raises(AccountUnavailable):
        results.load_dataset_snapshot(991101, snap.ref)
    monkeypatch.setattr(files, "release_resource_references", lambda *args: None)
    assert results.purge_owner(991101) == 1


def test_timestamp_missing_and_numpy_values_are_preserved_without_textual_nan():
    snap = results.persist_dataset_snapshot(991101,
        pd.DataFrame({"time": [pd.Timestamp("2026-01-01"), pd.NaT], "value": [1 / 7, None]}),
        source={"sha256": "a" * 64})
    restored = snap.to_frame()
    assert restored.loc[0, "time"] == pd.Timestamp("2026-01-01")
    assert pd.isna(restored.loc[1, "time"])
    assert snap.to_dict()["data"][1] == [None, None]
    assert restored.loc[0, "value"] == 1 / 7


def test_invalid_row_identity_and_units_are_refused():
    frame = pd.DataFrame({"v": [1, 2]})
    with pytest.raises(ValueError, match="identity"):
        results.persist_dataset_snapshot(991101, frame, source={}, row_ids=[1, 1])
    with pytest.raises(ValueError, match="Units"):
        results.persist_dataset_snapshot(991101, frame, source={}, units={"absent": "g"})
