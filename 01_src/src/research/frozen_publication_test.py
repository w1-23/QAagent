"""Frozen-data acceptance against real temporary asset storage; synthetic data."""
import copy
import hashlib
from pathlib import Path

import pytest
from src.research.prepared_dataset_assets_test import temporary_stores, OWNER, SID, prepared  # noqa: F401
from src.research.script_execution import _accept_frozen_render
from src.figures.projects import _frozen_render_state, _verify_sources, ProjectError
from src.session import files


def fixture():
    dataset = prepared()
    item = {"name": "panel", "parents": ["result"], "formats": ["svg"],
            "render_script": "fig, ax = plt.subplots()\nax.plot(data['result']['value'])"}
    digest = dataset.to_dict()["payload_sha256"]
    receipt = {"version": 1, "renderer_sha256": hashlib.sha256(item["render_script"].encode()).hexdigest(),
               "data_sha256": {"result": digest}, "geometry_sha256": "a" * 64,
               "geometry_status": "supported", "geometry_unsupported": []}
    return dataset, item, receipt, digest


def test_frozen_snapshot_exact_bytes_and_pins_are_required():
    dataset, item, receipt, digest = fixture()
    frozen, refs = _accept_frozen_render(item, {"frozen_render_receipt": receipt},
        {"result": dataset.ref}, {"result": digest}, owner=OWNER, session_id=SID)
    row = frozen["input_snapshots"][0]
    assert row["sha256"] == digest
    assert files.get_account_file(row["file_id"], OWNER) is None
    source = {"script": "original analysis", "input_snapshots": [], "output_key": "panel",
              "output_contract": {"version": 2, "tables": [{"name": "result"}], "figures": [item]},
              "frozen_render": frozen}
    assert _frozen_render_state(source) == frozen
    _verify_sources({"source_json": source, "data_refs_json": refs}, OWNER)
    with pytest.raises(ProjectError, match="固定引用"):
        _verify_sources({"source_json": source, "data_refs_json": []}, OWNER)
    path = Path(files.get_account_file(row["file_id"], OWNER, allow_internal=True)["path"])
    path.write_bytes(b"corrupt")
    with pytest.raises(ProjectError):
        _verify_sources({"source_json": source, "data_refs_json": refs}, OWNER)


@pytest.mark.parametrize("field,value", [("renderer_sha256", "b" * 64),
    ("data_sha256", {"result": "b" * 64}), ("geometry_sha256", None),
    ("geometry_status", "unknown"), ("geometry_unsupported", ["unexpected"])])
def test_mismatched_or_malformed_receipt_refuses_acceptance(field, value):
    dataset, item, receipt, digest = fixture()
    receipt[field] = value
    with pytest.raises(ValueError):
        _accept_frozen_render(item, {"frozen_render_receipt": receipt},
            {"result": dataset.ref}, {"result": digest}, owner=OWNER, session_id=SID)


def test_unsupported_geometry_can_be_saved_but_cannot_style_replay():
    dataset, item, receipt, digest = fixture()
    receipt.update(geometry_status="unsupported", geometry_sha256=None,
                   geometry_unsupported=["custom_transform"])
    frozen, _ = _accept_frozen_render(item, {"frozen_render_receipt": receipt},
        {"result": dataset.ref}, {"result": digest}, owner=OWNER, session_id=SID)
    source = {"output_key": "panel", "output_contract": {"version": 2,
        "tables": [{"name": "result"}], "figures": [item]}, "frozen_render": frozen}
    with pytest.raises(ProjectError) as error:
        _frozen_render_state(source)
    assert error.value.code == "analysis_not_frozen"


@pytest.mark.parametrize("frozen", [[], {}, {"input_snapshots": None},
    {"input_snapshots": [None]}, {"input_snapshots": [{}]},
    {"input_snapshots": [{"file_id": [], "sha256": "a" * 64}]}])
def test_malformed_frozen_references_fail_with_project_error(frozen):
    with pytest.raises(ProjectError) as error:
        _verify_sources({"source_json": {"frozen_render": frozen}, "data_refs_json": []}, OWNER)
    assert error.value.code == "source_unverified"


def test_legacy_script_does_not_enter_worker(monkeypatch):
    from src.figures import projects, sandbox
    monkeypatch.setattr(sandbox, "select_backend", lambda **kw: pytest.fail("legacy analysis replayed"))
    with pytest.raises(ProjectError) as error:
        projects._render_script({"script": "raise RuntimeError('must not execute')"}, [])
    assert error.value.code == "analysis_not_frozen"
