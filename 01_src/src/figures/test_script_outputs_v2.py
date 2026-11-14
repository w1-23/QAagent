"""Offline v2 protocol tests; direct core calls do NOT validate isolation.

No LocalBackend, Docker, WSL, service, user data or model calls are used.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.figures import core, sandbox, script_outputs as protocol, worker


def contract(tables=(), figures=()):
    return protocol.normalize_output_contract({"version": 2, "tables": list(tables),
                                               "figures": list(figures)})


def job_for(value, **kwargs):
    target = kwargs.get("output_key", "")
    return sandbox.SandboxJob(job_id="offline-job", output_contract=value,
                              outputs=protocol.output_slots(value, output_key=target), **kwargs)


def receipt_for(job, payloads=None):
    payloads = payloads or {}
    states = {}
    for key in job.outputs:
        if key == "result":
            continue
        if key in payloads:
            data = payloads[key]
            states[key] = {"status": "ok", "size": len(data),
                           "sha256": hashlib.sha256(data).hexdigest()}
        else:
            states[key] = {"status": "missing", "error": "Not emitted."}
    partial = any(v["status"] != "ok" and k != "report" and not k.endswith(":overrides")
                  for k, v in states.items())
    return {"version": 2, "ok": True, "status": "partial" if partial else "ok",
            "job_id": job.job_id, "output_key": job.output_key, "slots": states,
            "stdout": "", "figure_count": len({key.split(":")[1] for key in payloads
                                                  if key.startswith("figure:")}), "applied_count": 0}


def put_outputs(tmp_path, job, payloads=None, receipt=None):
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    for key, data in (payloads or {}).items():
        (out / job.outputs[key]).write_bytes(data)
    data = json.dumps(receipt if receipt is not None else receipt_for(job, payloads)).encode()
    (out / "result.json").write_bytes(data)
    return out


def test_slots_flat_deterministic_and_targeted():
    value = contract([{"name": "tidy", "parents": ["data"]}],
                     [{"name": "first"}, {"name": "relations", "formats": ["svg", "png", "pdf"]}])
    slots = protocol.output_slots(value)
    assert slots["table:tidy"] == "table_00.json"
    assert slots["figure:relations:svg"] == "figure_01.svg"
    replay = protocol.output_slots(value, output_key="relations")
    assert replay["figure:relations:manifest"] == "figure_01.objects.json"
    assert not any(key.startswith("table:") or key.startswith("figure:first:") for key in replay)
    assert protocol.output_slots({}) == {}
    maximum = contract([{"name": f"t{i}"} for i in range(8)],
                       [{"name": f"f{i}", "formats": ["svg", "png", "pdf", "tiff"]} for i in range(8)])
    assert len(protocol.output_slots(maximum)) == 58


@pytest.mark.parametrize("bad", [
    {"version": 1}, {"version": True}, {"version": 2, "extra": 1},
    {"version": 2, "tables": [{"name": "../data"}]},
    {"version": 2, "tables": [{"name": "数据"}]},
    {"version": 2, "tables": [{"name": "same"}], "figures": [{"name": "Same"}]},
    {"version": 2, "tables": [{"name": "a", "parents": ["a"]}]},
    {"version": 2, "figures": [{"name": "a", "formats": ["html"]}]},
    {"version": 2, "figures": [{"name": "a", "formats": ["svg", "svg"]}]},
    {"version": 2, "tables": [{"name": f"t{i}"} for i in range(9)]},
])
def test_bad_contracts(bad):
    with pytest.raises(ValueError):
        protocol.normalize_output_contract(bad)


def test_receipt_zero_figures_and_shared_parser(tmp_path):
    job = job_for(contract())
    put_outputs(tmp_path, job)
    outputs = sandbox._read_outputs(tmp_path, job)
    assert set(outputs) == {"result"}
    receipt = protocol.validate_receipt(outputs["result"], job.output_contract, job_id=job.job_id)
    assert receipt["status"] == "ok"


def test_only_success_slots_read_and_hash_verified(tmp_path):
    job = job_for(contract([{"name": "tidy"}], [{"name": "relations"}]))
    payloads = {"table:tidy": b'{"format":"offline"}', "figure:relations:png": b"png-test",
                "figure:relations:manifest": b'{"qa_receipt":{}}'}
    put_outputs(tmp_path, job, payloads)
    result = sandbox._read_outputs(tmp_path, job)
    assert set(result) == {"result", *payloads}
    assert json.loads(result["result"])["status"] == "partial"
    (tmp_path / "out" / job.outputs["table:tidy"]).write_bytes(b"tampered")
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


@pytest.mark.parametrize("mutation", ["path", "identity", "state", "sha", "aggregate", "extra", "version"])
def test_forged_receipt_is_rejected(tmp_path, mutation):
    job = job_for(contract([{"name": "tidy"}]))
    receipt = receipt_for(job, {"table:tidy": b"data"})
    if mutation == "path":
        receipt["slots"]["../../secret"] = receipt["slots"].pop("table:tidy")
    elif mutation == "identity":
        receipt["job_id"] = "another-job"
    elif mutation == "state":
        receipt["slots"]["table:tidy"]["status"] = "done"
    elif mutation == "sha":
        receipt["slots"]["table:tidy"]["sha256"] = "f" * 64
    elif mutation == "aggregate":
        receipt["status"] = "partial"
    elif mutation == "version":
        receipt["version"] = True
    else:
        receipt["path"] = "secret"
    put_outputs(tmp_path, job, {"table:tidy": b"data"}, receipt)
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_oversize_duplicate_receipt_and_extra_files(tmp_path):
    job = job_for(contract())
    out = put_outputs(tmp_path, job)
    (out / "result.json").write_bytes(b" " * (protocol.MAX_RECEIPT_BYTES + 1))
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)
    put_outputs(tmp_path, job)
    (out / "result.json").write_bytes(b'{"version":2,"version":2}')
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)
    put_outputs(tmp_path, job)
    (out / "undeclared.txt").write_text("not authorized")
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_linked_output_rejected(tmp_path):
    job = job_for(contract([{"name": "tidy"}]))
    out = put_outputs(tmp_path, job, {"table:tidy": b"data"})
    # Hard links need no Windows symlink privilege.
    os.link(out / job.outputs["table:tidy"], tmp_path / "alias")
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_legacy_count_not_relaxed(tmp_path):
    job = sandbox.SandboxJob(outputs={str(i): f"file{i}.png" for i in range(9)})
    (tmp_path / "out").mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_job_serialization_and_mismatched_allocation(tmp_path):
    job = job_for(contract(), script="pass")
    assert json.loads(job.as_json())["output_contract"]["version"] == 2
    (tmp_path / "in").mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox._write_job_files(tmp_path, replace(job, outputs={"result": "other.json"}))


def test_core_zero_report_and_exception_discards_all():
    # Direct function calls with synthetic trusted source: not isolation tests.
    value = contract([{"name": "tidy", "parents": ["data"]}])
    good = core.capture_outputs("emit.table('tidy', object(), parents=['data'])\nemit.report({'rows': 3})",
                                output_contract=value)
    assert not good.error and "tidy" in good.tables and good.report == {"rows": 3}
    failed = core.capture_outputs("emit.table('tidy', object())\nraise RuntimeError('stop')",
                                  output_contract=value)
    assert failed.error_type == "script_error" and failed.tables == {} and failed.figures == {}
    stopped = core.capture_outputs("emit.report('discard')\nraise SystemExit(0)", output_contract=contract())
    assert stopped.error and not stopped.report_emitted


def test_core_named_closed_figures_and_replay():
    from matplotlib.figure import Figure
    first, second = Figure(), Figure()
    value = contract([{"name": "tidy"}], [{"name": "first"}, {"name": "second"}])
    source = "emit.table('tidy', object())\nemit.figure('first', one)\nemit.figure('second', two)"
    result = core.capture_outputs(source, output_contract=value, namespace={"one": first, "two": second})
    assert not result.error and result.figures == {"first": first, "second": second}
    replay = core.capture_outputs(source, output_contract=value, output_key="second",
                                  namespace={"one": first, "two": second})
    assert not replay.error and replay.tables == {} and replay.figures == {"second": second}
    duplicate = core.capture_outputs("emit.figure('first', one)\nemit.figure('first', one)",
                                     output_contract=value, namespace={"one": first})
    assert duplicate.error and duplicate.figures == {}


def test_legacy_capture_still_accepts_one_figure():
    result = core.capture_figure("fig = plt.figure()")
    assert not result.error and result.figure is not None and result.figures_seen == 1


def run_synthetic_worker(tmp_path, monkeypatch, value, result, *, output_key="", replay=False):
    """Test serialization without executing any source in the host."""
    job = job_for(value, output_key=output_key, replay_overrides=replay)
    core_stub = SimpleNamespace(__name__="src.figures.core", capture_outputs=lambda *a, **k: result,
                                RenderedFigure=core.RenderedFigure, _tick_label_problems=lambda *a, **k: [])
    calls = []

    def export(rendered, *, formats, dpi):
        calls.append(formats[0])
        if formats[0] == "svg":
            raise ValueError("synthetic SVG export failure")
        return {formats[0]: formats[0].encode()}

    exporter = SimpleNamespace(export_all=export)
    manifest = SimpleNamespace(assign_locators=lambda x: x,
                               build_manifest=lambda *a, **k: {"qa_receipt": {"synthetic": True}})
    out = tmp_path / "out"
    code = worker._run_outputs(json.loads(job.as_json()), "unused", {}, out, core_stub, exporter, manifest)
    return code, job, calls


def test_figure_parents_preserve_absent_vs_explicit():
    value = contract(figures=[{"name": "related", "parents": ["data", "tidy"]},
                              {"name": "independent", "parents": []}, {"name": "implicit"}])
    assert value["figures"][0]["parents"] == ["data", "tidy"]
    assert value["figures"][1]["parents"] == []
    assert "parents" not in value["figures"][2]
    for parents in (["related"], ["data", "data"], ["../data"], "data"):
        with pytest.raises(ValueError):
            contract(figures=[{"name": "related", "parents": parents}])


def test_dataset_loader_roundtrip_and_scope(tmp_path):
    import pandas as pd
    from src.figures.dataset_codec import encode_frame
    frame = pd.DataFrame({"value": pd.Series([1, None, 3], dtype="Int64")})
    source = tmp_path / "dataset.json"
    source.write_text(json.dumps(encode_frame(frame)), encoding="utf-8")
    load = worker._dataset_loader((source, "__citrusfig__"))
    pd.testing.assert_frame_equal(load(source), frame)
    outside = tmp_path / "outside.json"
    outside.write_text(source.read_text())
    with pytest.raises(ValueError):
        load(outside)
    source.write_text('{"version":1,"version":1}')
    with pytest.raises(ValueError):
        load(source)


def test_output_replaced_between_lstat_and_open(tmp_path, monkeypatch):
    job = job_for(contract([{"name": "tidy"}]))
    out = put_outputs(tmp_path, job, {"table:tidy": b"data"})
    real_open = sandbox.os.open
    target = out / job.outputs["table:tidy"]
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"data")
    swapped = False

    def swapping_open(path, flags, *args, **kwargs):
        nonlocal swapped
        if not swapped and str(path) in (str(target), target.name):
            swapped = True
            os.replace(replacement, target)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(sandbox.os, "open", swapping_open)
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)
    assert swapped


def test_symlink_output_rejected_when_supported(tmp_path):
    job = job_for(contract([{"name": "tidy"}]))
    out = put_outputs(tmp_path, job, {"table:tidy": b"data"})
    target = out / job.outputs["table:tidy"]
    outside = tmp_path / "outside"
    outside.write_bytes(b"data")
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires privileges on this Windows host.")
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_v2_file_count_and_byte_limits_remain_bounded(tmp_path, monkeypatch):
    job = job_for(contract([{"name": f"t{i}"} for i in range(8)],
                           [{"name": f"f{i}", "formats": ["svg", "png", "pdf", "tiff"]} for i in range(8)]))
    payloads = {key: b"x" for key in job.outputs if key != "result"}
    put_outputs(tmp_path, job, payloads)
    assert len(sandbox._read_outputs(tmp_path, job)) == 58
    monkeypatch.setattr(sandbox, "MAX_TOTAL_OUTPUT_BYTES", 1)
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(tmp_path, job)


def test_worker_per_format_partial_and_qa(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    value = contract(figures=[{"name": "relations"}, {"name": "other", "formats": ["png"]}])
    result = core.ScriptOutputsResult(figures={"relations": Figure(), "other": Figure()})
    code, job, calls = run_synthetic_worker(tmp_path, monkeypatch, value, result)
    assert code == 0 and calls == ["svg", "png", "pdf", "png"]
    outputs = sandbox._read_outputs(tmp_path, job)
    receipt = json.loads(outputs["result"])
    assert receipt["status"] == "partial"
    assert receipt["slots"]["figure:relations:svg"]["status"] == "error"
    assert outputs["figure:relations:png"] == b"png"
    assert json.loads(outputs["figure:other:manifest"])["qa_receipt"] == {"synthetic": True}


def test_worker_serialization_failure_and_report_only(tmp_path, monkeypatch):
    import src.figures.dataset_codec as codec
    monkeypatch.setattr(codec, "encode_frame", lambda frame: (_ for _ in ()).throw(ValueError("unsupported dtype")))
    result = core.ScriptOutputsResult(tables={"tidy": object()}, report={"explored": True}, report_emitted=True)
    code, job, _ = run_synthetic_worker(tmp_path, monkeypatch, contract([{"name": "tidy"}]), result)
    assert code == 0
    outputs = sandbox._read_outputs(tmp_path, job)
    assert "table:tidy" not in outputs and json.loads(outputs["report"])["explored"]
    assert json.loads(outputs["result"])["slots"]["table:tidy"]["status"] == "error"


@pytest.mark.parametrize("failure", ["runtime", "timeout"])
def test_runner_never_collects_outputs_after_failure(monkeypatch, failure):
    import subprocess
    job = job_for(contract(), script="pass")
    backend = sandbox.DockerBackend(image="offline-test-image")

    def completed(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("offline", 1)
        return subprocess.CompletedProcess([], worker.EXIT_SCRIPT_ERROR,
                                           stdout=b'{"ok":true}', stderr=b"script failed")

    def forbidden_collection(*args):
        raise AssertionError("Failure must not collect a forged success receipt.")

    monkeypatch.setattr(sandbox.subprocess, "run", completed)
    monkeypatch.setattr(sandbox, "_read_outputs", forbidden_collection)
    monkeypatch.setattr(backend, "_kill", lambda *args: None)
    result = backend.run(job)
    assert not result.ok and result.outputs == {}
    assert result.failure_kind == (sandbox.FAILURE_TIMEOUT if failure == "timeout" else sandbox.FAILURE_RUNTIME)


def test_worker_refuses_planted_receipt(tmp_path, monkeypatch):
    out = tmp_path / "out"
    out.mkdir()
    (out / "result.json").write_text('{"ok":true}')
    result = core.ScriptOutputsResult()
    code, _, _ = run_synthetic_worker(tmp_path, monkeypatch, contract(), result)
    assert code == worker.EXIT_FORBIDDEN


def test_worker_real_codec_and_manifest_without_script_exec(tmp_path):
    import pandas as pd
    from matplotlib.figure import Figure
    from src.figures import exporter, manifest
    from src.figures.dataset_codec import decode_frame
    value = contract([{"name": "tidy", "parents": ["data"]}],
                     [{"name": "raw", "formats": ["png"]}])
    frame = pd.DataFrame({"value": pd.Series([1, None], dtype="Int64")})
    result = core.ScriptOutputsResult(tables={"tidy": frame}, figures={"raw": Figure()})
    job = job_for(value)
    stub = SimpleNamespace(__name__="src.figures.core", capture_outputs=lambda *a, **k: result,
                           RenderedFigure=core.RenderedFigure, _tick_label_problems=core._tick_label_problems)
    code = worker._run_outputs(json.loads(job.as_json()), "unused", {}, tmp_path / "out",
                               stub, exporter, manifest)
    assert code == 0
    outputs = sandbox._read_outputs(tmp_path, job)
    assert json.loads(outputs["result"])["status"] == "ok"
    pd.testing.assert_frame_equal(decode_frame(json.loads(outputs["table:tidy"])), frame)
    assert "checks" in json.loads(outputs["figure:raw:manifest"])["qa_receipt"]
    assert outputs["figure:raw:png"].startswith(b"\x89PNG")


def test_worker_failure_has_no_receipt(tmp_path, monkeypatch):
    result = core.ScriptOutputsResult(error="script failed", stdout="diagnostic")
    code, job, calls = run_synthetic_worker(tmp_path, monkeypatch, contract(), result)
    assert code == worker.EXIT_SCRIPT_ERROR and not calls
    assert not (tmp_path / "out" / "result.json").exists()


def test_worker_replay_only_target_and_overrides(tmp_path, monkeypatch):
    from matplotlib.figure import Figure
    import src.figures.overrides as overrides
    monkeypatch.setattr(overrides, "validate_overrides", lambda values, manifest: values)
    monkeypatch.setattr(overrides, "merge_overrides", lambda base, incoming: base + incoming)
    monkeypatch.setattr(overrides, "apply_overrides", lambda *a: ["applied"])
    value = contract([{"name": "tidy"}], [{"name": "first"}, {"name": "second", "formats": ["png"]}])
    result = core.ScriptOutputsResult(figures={"second": Figure()})
    code, job, calls = run_synthetic_worker(tmp_path, monkeypatch, value, result, output_key="second", replay=True)
    assert code == 0 and calls == ["png"]
    outputs = sandbox._read_outputs(tmp_path, job)
    assert set(outputs) == {"result", "figure:second:png", "figure:second:manifest", "figure:second:overrides"}
    assert json.loads(outputs["result"])["applied_count"] == 1
