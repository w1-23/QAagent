"""吉祥物自主行为调度器的契约（P1-C 补充）。

用户要求：待机时由「带随机种子的加权调度器」自主动作；用户交互 > 真实任务态 >
自主随机 > 待机；同类权重初始 45/25/20/10；最近 3 次不重复；每个动作有冷却；
不得选中任务/互动专用动作；不得用 setInterval 轮播；缺帧不能让调度器停摆。

这些是行为契约，用静态断言锁住，避免后续改动把调度器改回「一直趴着」。
"""
from __future__ import annotations

import re
from pathlib import Path

JS = (Path(__file__).resolve().parents[2] / "web" / "assets" / "mascot.js").read_text(
    encoding="utf-8"
)


def _actions_block() -> str:
    start = JS.index("var ACTIONS = [")
    end = JS.index("];", start)
    return JS[start:end]


def _action_rows() -> list[dict[str, str]]:
    chunks = re.split(r"\n\s*\{\s*\n?\s*id:", _actions_block())[1:]
    rows = []
    for ch in chunks:
        ident = re.search(r"'([\w.]+)'", ch).group(1)

        def g(key: str) -> str:
            m = re.search(key + r":\s*([^,\n]+)", ch)
            return m.group(1).strip() if m else ""

        rows.append(
            {
                "id": ident,
                "cat": g("cat").strip("'"),
                "bucket": g("bucket").strip("'"),
                "auto": g("auto"),
                "weight": g("weight"),
                "cooldown": g("cooldown"),
            }
        )
    return rows


def test_action_registry_size_and_buckets():
    rows = _action_rows()
    assert len(rows) == 30
    auto = [r for r in rows if r["auto"] == "true"]
    assert len(auto) == 18
    per_bucket: dict[str, int] = {}
    for r in auto:
        per_bucket[r["bucket"]] = per_bucket.get(r["bucket"], 0) + 1
    assert per_bucket == {"idle": 5, "leisure": 6, "expression": 5, "sleep": 2}


def test_bucket_weights_match_spec():
    m = re.search(r"BUCKET_WEIGHT = \{([^}]*)\}", JS)
    assert m, "缺少 BUCKET_WEIGHT"
    pairs = dict(re.findall(r"(\w+):\s*(\d+)", m.group(1)))
    assert pairs == {"idle": "45", "leisure": "25", "expression": "20", "sleep": "10"}


def test_autonomous_actions_have_weight_and_cooldown():
    for r in _action_rows():
        if r["auto"] != "true":
            continue
        assert int(r["weight"] or 0) > 0, f"{r['id']} 缺少权重"
        assert int(r["cooldown"] or 0) > 0, f"{r['id']} 缺少冷却"


def test_task_and_interaction_actions_are_not_auto_playable():
    rows = {r["id"]: r for r in _action_rows()}
    for aid in ("work.think", "work.read", "work.search", "work.type",
                "work.done", "work.fail", "interact.click", "interact.wave",
                "interact.love", "drag.hold", "drop.land", "idle.rest"):
        assert aid in rows, f"缺少动作 {aid}"
        assert rows[aid]["auto"] != "true", f"{aid} 不允许自主播放"


def test_seeded_rng_and_recent_memory():
    assert "function mulberry32(" in JS
    assert re.search(r"^\s*var RECENT_MEMORY = 3;", JS, re.M)
    # 种子只在启动时初始化一次（不能每次计时都重置）
    assert JS.count("rand = mulberry32(seed)") == 1
    assert "recentIds.unshift(lastActionId)" in JS


def test_cooldown_tiers_prevent_stall():
    # 逐级放宽：不重复+冷却 → 不重复 → 仅冷却 → 任意合法
    assert "function pickAutoAction()" in JS
    assert JS.count("cooldownLeft(a) <= 0") >= 3
    assert "if (pool.length) return pickByBucket(pool);" in JS


def test_activity_levels_drive_interval():
    m = re.search(r"var ACTIVITY = \{([^}]*)\}", JS)
    assert m
    assert "quiet: [15000, 25000]" in m.group(1)
    assert "natural: [6000, 12000]" in m.group(1)
    assert "lively: [4000, 8000]" in m.group(1)
    assert "setActivityLevel: setActivityLevel" in JS


def test_no_setinterval_round_robin():
    assert "setInterval(" not in JS


def test_priority_guard_blocks_auto_during_task():
    body = JS[JS.index("function canAuto()"):]
    body = body[: body.index("function cooldownLeft")]
    for guard in ("!taskState", "!dragging", "!docHidden", "!hidden", "!still()"):
        assert guard in body, f"canAuto 缺少守卫 {guard}"
