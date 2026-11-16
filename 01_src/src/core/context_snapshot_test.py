"""Offline history-status contract; no config, provider or account imports."""
import pytest

from src.core.context_snapshot import build_context_snapshot


@pytest.mark.parametrize("status,expected", [
    ("ok", "loaded"), ("loaded", "loaded"), ("success", "success"),
    ("degraded", "degraded"), ("failed", "failed"), ("empty", "empty"),
    ("unknown", "unknown"), ("unexpected", "unknown"), (None, "unknown"),
])
def test_history_status_preserves_loader_success_and_failure(status, expected):
    snapshot = build_context_snapshot({}, logical_call_id="synthetic-call",
                                      history_load_status=status)
    assert snapshot["history_load_status"] == expected
