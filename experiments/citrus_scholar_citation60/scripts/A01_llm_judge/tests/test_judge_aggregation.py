from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPTS = HERE.parents[2]
JUDGE_DIR = HERE.parents[1]
sys.path.insert(0, str(SCRIPTS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


aggregate = load_module("citrus_judge_aggregate", SCRIPTS / "aggregate_llm_judges.py")
adjudicate = load_module(
    "citrus_judge_adjudicate",
    JUDGE_DIR / "30_adjudicate_disagreements.py",
)


def valid_judgment() -> dict:
    return {
        "status": "completed",
        "answerability_correct": True,
        "answer_correct": True,
        "evidence_used": True,
        "fatal_error": False,
        "decision": "PASS",
        "reason_code": "complete_answer",
        "evidence_span_locators": ["doc/chunk"],
    }


def test_missing_judge_is_not_silently_converted(tmp_path):
    result = aggregate.read_judge(tmp_path / "missing.json")
    assert result["status"] == "MISSING"
    assert result["decision"] == ""


def test_invalid_judge_schema_is_blocked(tmp_path):
    path = tmp_path / "judge.json"
    path.write_text(
        json.dumps(
            {
                "status": "completed",
                "decision": "PASS",
                "answerability_correct": True,
                "answer_correct": True,
                "evidence_used": True,
                "fatal_error": False,
                "reason_code": "",
                "evidence_span_locators": [],
            }
        ),
        encoding="utf-8",
    )
    result = aggregate.read_judge(path)
    assert result["status"] == "INVALID_SCHEMA"


def test_metric_conflict_requires_adjudication_even_when_decisions_agree():
    first = valid_judgment()
    second = valid_judgment()
    second["evidence_used"] = "not_applicable"
    assert adjudicate.judgments_disagree(first, second)


def test_identical_judgments_do_not_require_adjudication():
    assert not adjudicate.judgments_disagree(
        valid_judgment(),
        valid_judgment(),
    )


def test_third_judge_schema_is_validated():
    assert adjudicate.validate(valid_judgment()) == []
    invalid = valid_judgment()
    invalid["decision"] = "MAYBE"
    assert "decision_not_PASS_FAIL" in adjudicate.validate(invalid)
