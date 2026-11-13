"""TurnEnvelope is the one terminal snapshot for every done branch."""
from __future__ import annotations

import json

from src.core.turn_envelope import build_turn_envelope


def test_completed_turn_has_one_consistent_snapshot():
    envelope = build_turn_envelope(
        answer="已完成。",
        tool_status=[{"name": "deepseek_search", "status": "complete"}],
        missing_groups=[],
        history_saved=True,
    )
    assert envelope.display_status == "completed"
    assert envelope.execution_status == "completed"
    assert envelope.job_status == "completed"
    assert envelope.history_saved is True
    json.dumps(envelope.as_dict(), ensure_ascii=False)


def test_partial_turn_never_reports_completed():
    envelope = build_turn_envelope(
        answer="部分结果如下。",
        tool_status=[
            {"name": "deepseek_search", "status": "complete"},
            {"name": "deepseek_fetch", "status": "failed",
             "reason": "timeout"},
        ],
        history_saved=True,
    )

    assert envelope.display_status == "partial"
    assert envelope.execution_status == "partial"
    assert envelope.job_status == "failed"
    assert envelope.acceptance_status == "partial"
    assert envelope.failures


def test_empty_answer_is_failed_and_says_history_is_not_saved():
    envelope = build_turn_envelope(
        answer="",
        tool_status=[],
        history_saved=False,
        notices=["本轮没有形成可交付的回答；终态以 turn_status 为准。"],
    )

    assert envelope.display_status == "failed"
    assert envelope.execution_status == "failed"
    assert envelope.job_status == "failed"
    assert envelope.history_saved is False
    assert envelope.notices


def test_user_cancel_is_cancelled_not_failed():
    envelope = build_turn_envelope(
        answer="",
        tool_status=[{"name": "deepseek_search", "status": "cancelled"}],
        requested_status="cancelled",
        display_status="cancelled",
        job_status="cancelled",
        stop_reason="user",
        history_saved=True,
        notices=["本轮已停止"],
    )

    assert envelope.display_status == "cancelled"
    assert envelope.execution_status == "cancelled"
    assert envelope.job_status == "cancelled"
    assert envelope.stop_reason == "user"


def test_invalid_display_override_falls_back_to_derived_state():
    envelope = build_turn_envelope(
        answer="已完成。",
        tool_status=[{"name": "deepseek_search", "status": "complete"}],
        display_status="not-a-state",
    )
    assert envelope.display_status == "completed"


def test_artifact_ids_carry_only_validated_identity_once():
    """Artifact attribution must come from validated records, not from prose."""
    envelope = build_turn_envelope(
        answer="报告已生成。",
        tool_status=[{
            "name": "generate_word_document",
            "status": "complete",
            "artifact_validations": [
                {"artifact_id": "art-1", "valid": True},
                {"artifact_id": "art-2", "valid": False,
                 "reason_code": "missing_artifact"},
                {"artifact_id": "art-1", "valid": True},
                {"artifact_id": "", "valid": True},
            ],
        }],
        history_saved=True,
    )
    assert envelope.artifact_ids == ("art-1",)
    assert envelope.verified_artifacts == 0   # count comes from artifact_count
    payload = envelope.as_dict()
    # asdict keeps tuples; JSON serialisation turns them into arrays.
    assert list(payload["artifact_ids"]) == ["art-1"]


def test_verified_artifact_count_and_ids_are_independent_fields():
    envelope = build_turn_envelope(
        answer="两份报告。",
        tool_status=[{
            "name": "generate_word_document",
            "status": "complete",
            "artifact_count": 2,
            "artifact_validations": [
                {"artifact_id": "a", "valid": True},
                {"artifact_id": "b", "valid": True},
            ],
        }],
        history_saved=True,
    )
    assert envelope.verified_artifacts == 2
    assert envelope.artifact_ids == ("a", "b")
