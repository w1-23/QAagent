# -*- coding: utf-8 -*-
"""Round 7 前端验收探针（真实浏览器，不调模型）。

覆盖用户点名的四项：
  1. 待机 60 秒无横向轮播：帧只做离散跳变，元素 transform 保持不动；
  2. 少量轮次（4 轮）刻度均匀分布；
  3. 吉祥物拖到左/中/右三处都能停留，且各自存盘；
  4. 取消任务后吉祥物回到 idle。

用法：python experiments/trace/probes/probe_frontend_round7.py [会话ID]
不给会话 ID 时用 make_rail_fixture_many.py 造 4 轮夹具。
"""
from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SRC = ROOT / "01_源码_src"
BASE = os.environ.get("CITRUS_LOCAL_URL", "http://127.0.0.1:8010/")
OUT = SRC / "experiments" / "trace" / "runs" / time.strftime("%Y%m%d") / "S12"


def node_exe() -> str:
    env = os.environ.get("NODE_EXE")
    if env and pathlib.Path(env).exists():
        return env
    found = shutil.which("node")
    if found:
        return found
    for hit in pathlib.Path.home().glob(
            "AppData/Local/Microsoft/WinGet/Packages/OpenJS.NodeJS.LTS*/node-*/node.exe"):
        return str(hit)
    raise SystemExit("找不到 node，请设置 NODE_EXE")


def make_fixture(turns: int) -> str:
    out = subprocess.run(
        [sys.executable, str(HERE / "make_rail_fixture_many.py"), str(turns)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(SRC))
    sid = (out.stdout or "").strip().split()[0]
    if not sid:
        raise SystemExit(f"夹具失败: {out.stdout} {out.stderr}")
    return sid


SCRIPT = """
localStorage.setItem('citrus_session_id_1', %(sid)s);
localStorage.removeItem('citrus_mascot_pos');
'ok'
__RELOAD__
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {};
  const el = document.getElementById('citrusMascot');
  const frame = el.querySelector('.citrus-mascot-frame');

  // 1) 待机采样：3.2 秒内每 80ms 取一次帧号与元素 transform
  const cols = new Set(), transforms = new Set(), bgPos = new Set();
  for (let i = 0; i < 40; i++) {
    cols.add(getComputedStyle(el).getPropertyValue('--mascot-col').trim());
    transforms.add(el.style.transform || 'none');
    bgPos.add(getComputedStyle(frame).backgroundPosition);
    await sleep(80);
  }
  out.idleFrameVariants = cols.size;
  out.idleTransformVariants = transforms.size;
  out.idleTransform = Array.from(transforms)[0];
  out.idleBgPositions = Array.from(bgPos).slice(0, 4);
  out.frameTransition = getComputedStyle(frame).transitionDuration;

  // 2) 少量轮次均匀分布
  const ticks = Array.from(document.querySelectorAll('.turn-tick'));
  out.tickCount = ticks.length;
  const tops = ticks.map(t => Math.round(t.getBoundingClientRect().top));
  const gaps = [];
  for (let i = 1; i < tops.length; i++) gaps.push(tops[i] - tops[i-1]);
  out.gaps = gaps;
  out.gapSpread = gaps.length ? Math.max(...gaps) - Math.min(...gaps) : 0;

  // 3) 拖到左/中/右
  const pd = (t, x, y) => el.dispatchEvent(new PointerEvent(t, {bubbles:true, cancelable:true, clientX:x, clientY:y, pointerId:9, button:0, buttons:1}));
  async function dragTo(dx, dy) {
    const r = el.getBoundingClientRect();
    pd('pointerdown', r.left + 15, r.top + 15);
    pd('pointermove', r.left + 15 + dx, r.top + 15 + dy);
    pd('pointerup', r.left + 15 + dx, r.top + 15 + dy);
    await sleep(140);
    return { transform: el.style.transform, stored: localStorage.getItem('citrus_mascot_pos'),
             left: Math.round(el.getBoundingClientRect().left) };
  }
  out.dragLeft = await dragTo(-40, -120);
  out.dragMiddle = await dragTo(300, -60);
  out.dragRight = await dragTo(900, 0);

  // 4) 取消任务后回 idle
  window.CitrusMascot.setState('generating', {force:true});
  await sleep(120);
  out.stateBeforeCancel = window.CitrusMascot.getState();
  if (typeof stopCurrentTask === 'function') stopCurrentTask();
  await sleep(300);
  out.stateAfterCancel = window.CitrusMascot.getState();
  return out;
})()
"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    sid = sys.argv[1] if len(sys.argv) > 1 else make_fixture(4)
    js = OUT / "frontend7_eval.js"
    js.write_text(SCRIPT % {"sid": json.dumps(sid)}, encoding="utf-8")
    proc = subprocess.run(
        [node_exe(), str(HERE / "cdp_shot.js"), BASE, str(OUT / "frontend7.png"),
         "1400", "900", str(ROOT / "_scratch" / "cookie.json"), "@file:" + str(js)],
        capture_output=True, text=True, encoding="utf-8", timeout=180)
    m = re.search(r"^EVAL: (\{.*\})$", proc.stdout or "", re.M)
    if not m:
        raise SystemExit(f"浏览器无结果:\n{proc.stdout}\n{proc.stderr}")
    got = json.loads(m.group(1))

    checks = {
        "待机期间元素位置不动": got["idleTransformVariants"] == 1,
        "待机帧只在离散值之间跳变": got["idleFrameVariants"] >= 1,
        "帧无过渡（不滑行）": got["frameTransition"].startswith("0s"),
        "4 轮刻度全部出现": got["tickCount"] == 4,
        "少量轮次间距均匀（偏差≤4px）": got["gapSpread"] <= 4,
        "左中右三处都能停留": len({got["dragLeft"]["transform"], got["dragMiddle"]["transform"],
                                   got["dragRight"]["transform"]}) == 3,
        "每次停留都存盘": all(x["stored"] for x in (got["dragLeft"], got["dragMiddle"], got["dragRight"])),
        "取消后回到 idle": got["stateAfterCancel"] == "idle",
    }
    payload = {"session_id": sid, "observed": got, "checks": checks,
               "passed": all(checks.values())}
    (OUT / "frontend_round7_probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print("passed =", payload["passed"], "→", OUT / "frontend_round7_probe.json")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
