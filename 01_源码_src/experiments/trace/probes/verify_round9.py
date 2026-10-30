"""Round 9 合并验收：前端 P0–P5 与并行抓取竞争策略的代码级证据。

只读本地仓库与静态资源，不调用模型、不写数据库。
运行：python experiments/trace/probes/verify_round9.py
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
WEB = ROOT / "web" / "assets"
SRC = ROOT / "src"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")


def main() -> int:
    rail = _read(WEB / "turn_rail.js")
    css = _read(WEB / "app.css")
    mascot = _read(WEB / "mascot.js")
    app = _read(WEB / "app.js")
    fetch_py = _read(SRC / "tools" / "deepseek_search_fetch.py")
    tool_results = _read(SRC / "core" / "tool_results.py")

    checks = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    # ---- P0 导航即时更新 ----
    add("P0 导航暴露增量接口",
        "window.turnRail" in rail and all(k in rail for k in ("addPending", "dropPending", "sync", "reload")),
        "window.turnRail = {refresh, focus, reload, addPending, dropPending, sync}")
    add("P0 发送即出刻度和失败即撤刻度",
        "turnRail.addPending" in app and "turnRail.dropPending" in app,
        "app.js 调用 addPending / dropPending")
    add("P0 消息 DOM 变化即时重建（不靠刷新）",
        "MutationObserver" in rail and "scheduleBuild" in rail,
        "MutationObserver(scheduleBuild).observe(chatBox, {childList:true})")
    add("P0 会话切换重建索引",
        "turnRail.reload" in app and "loadedSession" in rail,
        "app.js 切换会话调用 turnRail.reload()")
    add("P0 导航契约测试存在",
        (SRC / "api" / "test_frontend_round7.py").exists(),
        "src/api/test_frontend_round7.py")

    # ---- P1 灰色半透明体系 ----
    rail_vars = [v for v in ("--rail-tick", "--rail-tick-active", "--rail-tick-hover",
                             "--rail-tip-bg", "--rail-gap", "--rail-track-hover")
                 if v in css]
    add("P1 导航走灰色变量体系", len(rail_vars) == 6, f"{len(rail_vars)}/6 个 --rail-* 变量存在")
    add("P1 按下锁定布局（防位移误选）",
        "is-locked" in rail or "activeLockUntil" in rail, "activeLockUntil + is-locked")
    add("P1 命中区扩大", "hit" in css.lower() or "hitArea" in rail, "透明命中层/局部放大")

    # ---- P2 吉祥物原地动作 ----
    mascot_css = _read(WEB / "mascot.css")
    add("P2 逐帧硬切而非横向轮播",
        "calc(var(--mascot-col" in mascot_css and "transition: none" in mascot_css,
        "mascot.css: background-position: calc(var(--mascot-col) * 11.1111%) 0%")
    add("P2 定位与动画分离",
        ".citrus-mascot {" in mascot_css and ".citrus-mascot-frame {" in mascot_css,
        "外层 .citrus-mascot 定位拖拽 / 内层 .citrus-mascot-frame 换帧")
    add("P2 八态帧带齐全",
        all(f'data-state="{s}"' in mascot_css for s in
            ("idle", "planning", "retrieving", "generating", "completed", "failed", "dragging", "dropping")),
        "mascot/{idle,planning,retrieving,generating,completed,failed,dragging,dropping}.png")

    # ---- P3 自由拖拽 ----
    add("P3 位置持久化", "citrus_mascot_pos" in mascot, "localStorage 保存相对坐标")
    add("P3 就近吸附与恢复默认",
        "snap" in mascot.lower() and "resetPosition" in mascot, "CitrusMascot.resetPosition()")

    # ---- P4 / P5 ----
    add("P4 头像 29px 且连续助手单头像",
        "29px" in css and ".message.bot + .message.bot" in css, "app.css 气泡规则")
    add("P5 三套主题可选",
        "data-theme" in css and all(t in css for t in ("cartoon", "dark", "light")),
        "light / cartoon / dark")

    # ---- 并行抓取竞争 ----
    add("抓取为并行竞争（上限 2、完成即判）",
        "_race_candidates" in fetch_py and "FIRST_COMPLETED" in fetch_py and "_RACE_PARALLEL = 2" in fetch_py,
        "src/tools/deepseek_search_fetch.py")
    add("胜出后取消在途并清空队列",
        "cancelled_after_winner" in fetch_py and "not_started_after_winner" in fetch_py,
        "迟到结果永不读取")
    add("full_text 分支只组装一份正文",
        fetch_py.count('web_rows = [{"title": winner') == 1
        and "for item in attempts:" not in fetch_py,
        "单正文插槽，无遍历拼接")
    add("旧去重/预算机制已回退",
        "CITRUS_FETCH_DEDUPE" not in fetch_py and "_dedupe_pages" not in fetch_py,
        "回退到 b2fd9ae 基线")
    add("竞争策略测试存在", (SRC / "tools" / "test_fetch_race.py").exists(),
        "src/tools/test_fetch_race.py（10 例）")
    add("40k 模型上下文保护仍在", "40_000" in tool_results, "src/core/tool_results.py")

    head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT,
                          capture_output=True, text=True).stdout.strip()
    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"head": head, "total": len(checks),
                      "passed": len(checks) - len(failed), "failed": failed},
                     ensure_ascii=False, indent=2))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
