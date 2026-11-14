from __future__ import annotations

from src.engine import hardware


def test_marked_provider_is_skipped_for_this_process(monkeypatch):
    monkeypatch.setitem(hardware._probe_cache, "DmlExecutionProvider", (False, "probe hang"))
    order = hardware._usable_provider_order(
        ["DmlExecutionProvider", "CPUExecutionProvider"])
    assert order == ["CPUExecutionProvider"]


def test_cpu_is_never_demoted(monkeypatch):
    hardware.mark_provider_unusable("CPUExecutionProvider", "should be ignored")
    ok, reason = hardware.probe_provider("CPUExecutionProvider")
    assert ok and reason == "cpu"


def test_mark_provider_unusable_is_recorded(monkeypatch):
    hardware.mark_provider_unusable("CUDAExecutionProvider", "no progress")
    assert hardware._probe_cache["CUDAExecutionProvider"] == (False, "no progress")


def test_provider_order_keeps_configured_sequence(monkeypatch):
    monkeypatch.setitem(hardware._probe_cache, "TensorrtExecutionProvider", (False, "x"))
    order = hardware._usable_provider_order(
        ["TensorrtExecutionProvider", "CPUExecutionProvider"])
    assert order == ["CPUExecutionProvider"]
