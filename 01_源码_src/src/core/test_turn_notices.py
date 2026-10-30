# -*- coding: utf-8 -*-
"""Guards for the single-source turn notices.

Background: the "this turn could not finish" sentences used to live as 26
string literals inside ``general_agent_graph.py``.  They drifted, they were
phrased differently from the tool trace, and they silently replaced whatever the
model itself had written.  These tests fail if the literals come back.
"""
from __future__ import annotations

import pathlib

from src.core.turn_notices import INCOMPLETE_PREFIX, NOTICES, is_incomplete, notice
from src.core.turn_status import derive_turn_status

_SRC = pathlib.Path(__file__).resolve().parents[2]


def test_every_notice_is_prefixed_and_non_empty():
    assert len(NOTICES) >= 20, sorted(NOTICES)
    for code, body in NOTICES.items():
        assert body.strip(), code
        assert INCOMPLETE_PREFIX not in body, f"{code} 不应自带前缀"
        rendered = notice(code)
        assert rendered.startswith(INCOMPLETE_PREFIX), code
        assert len(rendered) > len(INCOMPLETE_PREFIX) + 8, code


def test_unknown_code_falls_back_instead_of_raising():
    text = notice("no_such_code_xyz")
    assert text.startswith(INCOMPLETE_PREFIX)
    assert "模型请求失败" in text


def test_format_placeholders_are_optional():
    # 占位符缺失时保留原文，不能因为少传一个参数就把提示语吞掉
    assert "{status}" in notice("model_unavailable")
    assert "503" in notice("model_unavailable", status=503)


def test_sse_trace_labels_do_not_duplicate_the_prefix():
    # action_hint / summary 走的是另一条通道，不应被塞进提示语前缀
    assert is_incomplete(notice("deadline"))
    assert not is_incomplete("柑橘木虱的防治措施包括……")


def test_turn_status_recognises_every_notice():
    for code in NOTICES:
        status = derive_turn_status(notice(code), [])
        assert status in ("partial", "failed"), (code, status)


def test_no_hardcoded_notice_literals_in_orchestration():
    """回归闸门：编排层不允许再出现「本轮未完成」字面量。"""
    offenders = []
    for rel in ("src/graph/general_agent_graph.py", "src/api/main.py"):
        path = _SRC / rel
        if not path.exists():
            continue
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if INCOMPLETE_PREFIX.rstrip("：") in line:
                offenders.append(f"{rel}:{lineno}: {line.strip()[:80]}")
    assert not offenders, "硬编码提示语回归：" + " | ".join(offenders)
