# -*- coding: utf-8 -*-
"""P0/P1/P2/P3 前端修复的静态契约测试（不跑浏览器、不调模型）。

这些测试只断言"代码里必须存在的接线"，防止以后重构时把
「新提问即时出刻度」「吉祥物硬切帧」「拖动位置持久化」又弄丢。
真实浏览器行为由 experiments/trace/probes/probe_rail_live_update.py 与
CDP 截图验收覆盖。
"""
from __future__ import annotations

import pathlib
import re

WEB = pathlib.Path(__file__).resolve().parents[2] / "web" / "assets"


def _read(name: str) -> str:
    return (WEB / name).read_text(encoding="utf-8")


def test_rail_exposes_pending_api():
    src = _read("turn_rail.js")
    for fn in ("function addPending(", "function dropPending(", "function sync("):
        assert fn in src, fn
    assert "addPending: addPending" in src and "dropPending: dropPending" in src
    assert "sync: sync" in src


def test_rail_pending_tick_is_not_a_fake_turn():
    src = _read("turn_rail.js")
    assert "is-pending" in src
    # 临时刻度不可点击定位，只提示“还在保存”
    assert "还在保存中" in src
    # 同一条提问只能有一个刻度
    assert "同一条提问只生成一个刻度" in src


def test_app_wires_pending_on_submit_and_sync_on_done():
    src = _read("app.js")
    assert "window.turnRail.addPending(clientRequestId, query)" in src
    assert "window.turnRail.sync(clientRequestId, query)" in src
    assert "window.turnRail.dropPending(clientRequestId)" in src
    # 切换会话与清空会话都要重建索引
    assert src.count("window.turnRail.reload()") >= 2


def test_rail_uses_gray_theme_variables_not_hardcoded_accent():
    css = _read("app.css")
    for var in ("--rail-tick:", "--rail-tick-hover:", "--rail-tick-active:",
                "--rail-tick-pending:", "--rail-track-hover:"):
        assert var in css, var
    # 鱼眼规则不得再写死橙色
    block = css[css.find(".turn-tick.is-focus"):css.find(".turn-rail-track.is-locked")]
    assert "--accent" not in block, block[:200]
    # 深色主题要有自己的一套灰白透明（找含 --rail-tick 的那一段深色变量块）
    idx = css.find("--rail-tick:")
    assert idx > 0
    dark_start = css.rfind('[data-theme="dark"]', 0, idx)
    assert dark_start > 0, "深色主题没有导航条变量"
    assert "--rail-tick-active:" in css[dark_start:idx + 300]


def test_mascot_frames_switch_without_sliding():
    css = _read("mascot.css")
    frame = css[css.find(".citrus-mascot-frame"):]
    assert "transition: none;" in frame[:600]
    assert "background-position" in frame[:600]


def test_mascot_drag_position_persists():
    js = _read("mascot.js")
    for needle in ("citrus_mascot_pos", "function applyPos(", "function savePos(",
                   "function restorePos(", "function resetPosition(", "function snap("):
        assert needle in js, needle
    # 松手后必须保留位置，不能清空 transform
    finish = js[js.find("function finish() {"):]
    assert "node.style.transform = ''" not in finish[:900]
    assert "savePos();" in finish[:900]
