"""Truncated scientific parameters must never be silently completed."""
from __future__ import annotations

import pytest

from src.core.tool_errors import ToolArgumentError
from src.research.tools import _json_arg


@pytest.mark.parametrize("raw, expected", [
    ('[{"sheet": "S1", "cell": "B4"}, {"sheet": "S2", "cell": "C', list),
    ('{"ranges": {"SSC": {"min": 8, "max": 20}, "TA": {"min": 0.2', dict),
    ('[{"doi": "10.3390/metabo14040242"}, {"doi": "10.1016/j.foodchem.2020.12778', list),
    ('{"title": "文献核验', dict),
    ('这不是 JSON，也没有任何结构', list),
])
def test_json_arg_rejects_truncated_input_without_inventing_data(raw, expected):
    with pytest.raises(ToolArgumentError) as excinfo:
        _json_arg(raw, expected, expected())
    message = str(excinfo.value)
    assert "本批未写入" in message
    assert "每批不超过 20 条" in message


def test_complete_json_and_native_arguments_keep_exact_values():
    assert _json_arg('[{"doi":"10.1016/j.foodchem.2020.12778"}]', list, []) == [
        {"doi": "10.1016/j.foodchem.2020.12778"}]
    value = {"title": "文献核验"}
    assert _json_arg(value, dict, {}) is value
    assert _json_arg("", list, []) == []


@pytest.mark.parametrize("raw", [0, False, "0", "false"])
def test_wrong_json_types_cannot_silently_select_default_attachments(raw):
    with pytest.raises(ToolArgumentError, match="list/dict"):
        _json_arg(raw, (list, dict), ["all-attachments"])
