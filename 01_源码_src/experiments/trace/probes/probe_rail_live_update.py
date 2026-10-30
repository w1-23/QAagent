# -*- coding: utf-8 -*-
"""P0 验收探针：新提问不刷新也要出刻度。

在真实浏览器里跑，但**不调用模型**——用本地合成会话（make_rail_fixture_many.py）
当夹具，直接驱动 turnRail 的公开 API，断言：

  1. 提交瞬间 addPending() 立刻多出一个刻度（is-pending），提示写「正在保存」；
  2. 同一条提问重复 addPending() 只生成一个刻度；
  3. 服务端确认（落库）后 sync() 用真实 message_id 替换临时刻度，pending 归零；
  4. 发送失败 dropPending() 撤掉刻度，正式导航不留下未成功的提问。

用法：
    python experiments/trace/probes/probe_rail_live_update.py [会话ID]
不给会话 ID 时自动用 make_rail_fixture_many.py 造一个 12 轮的夹具会话。

依赖：本机 Chrome + node（cdp_shot.js 只用 Node 内置模块，无 npm 依赖）。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import sqlite3
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[3]
SRC = ROOT / "01_源码_src"
DB = ROOT / "agent" / "state" / "sessions.db"
BASE = os.environ.get("CITRUS_LOCAL_URL", "http://127.0.0.1:8010/")


def _node_exe() -> str:
    """本机 node 不在 PATH 时（anaconda 起的 python 常见），退回到 WinGet 安装目录。"""
    env = os.environ.get("NODE_EXE")
    if env and pathlib.Path(env).exists():
        return env
    found = shutil.which("node")
    if found:
        return found
    for pat in ("AppData/Local/Microsoft/WinGet/Packages/OpenJS.NodeJS.LTS*/node-*/node.exe",
                "ProgramFiles/nodejs/node.exe"):
        for hit in pathlib.Path.home().glob(pat):
            return str(hit)
        for hit in pathlib.Path("C:/").glob(pat):
            return str(hit)
    raise SystemExit("找不到 node，请设置 NODE_EXE 指向 node.exe")
OUT_DIR = SRC / "experiments" / "trace" / "runs" / time.strftime("%Y%m%d") / "S12"


def make_fixture(turns: int = 12) -> str:
    out = subprocess.run(
        [sys.executable, str(SRC / "experiments/trace/probes/make_rail_fixture_many.py"), str(turns)],
        capture_output=True, text=True, encoding="utf-8", cwd=str(SRC),
    )
    sid = (out.stdout or "").strip().split()[0]
    if not sid:
        raise SystemExit(f"夹具创建失败: {out.stdout} {out.stderr}")
    return sid


def insert_turn(sid: str, text: str) -> int:
    con = sqlite3.connect(str(DB))
    try:
        cur = con.execute(
            "INSERT INTO messages (session_id, msg_type, content, timestamp, client_request_id)"
            " VALUES (?,?,?,?,?)",
            (sid, "human", text, time.time(), "crid-probe-" + uuid.uuid4().hex[:10]),
        )
        con.commit()
        return int(cur.lastrowid)
    finally:
        con.close()


def run_browser(sid: str, script: str) -> dict:
    js_path = OUT_DIR / "rail_probe_eval.js"
    js_path.write_text(script, encoding="utf-8")
    png = OUT_DIR / "rail_probe.png"
    cookie = ROOT / "_scratch" / "cookie.json"
    proc = subprocess.run(
        [_node_exe(), str(HERE / "cdp_shot.js"), BASE, str(png), "1400", "900",
         str(cookie), "@file:" + str(js_path)],
        capture_output=True, text=True, encoding="utf-8", timeout=180,
    )
    m = re.search(r"^EVAL: (\{.*\})$", proc.stdout or "", re.M)
    if not m:
        raise SystemExit(f"浏览器没返回结果:\n{proc.stdout}\n{proc.stderr}")
    return json.loads(m.group(1))


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sid = sys.argv[1] if len(sys.argv) > 1 else make_fixture(12)
    new_text = "探针：服务端新落库的提问（柑橘木虱防治要点）"

    script = """
localStorage.setItem('citrus_session_id_1', %s); 'ok'
__RELOAD__
(async () => {
  const q = s => Array.from(document.querySelectorAll(s));
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const out = {};
  for (let i = 0; i < 60 && !q('.turn-tick').length; i++) await sleep(200);
  out.base = q('.turn-tick').length;
  window.turnRail.addPending('crid-probe-A', '探针提问A：柑橘黄龙病传播途径');
  await sleep(200);
  out.afterAdd = q('.turn-tick').length;
  out.pendingTicks = q('.turn-tick.is-pending').length;
  const p = q('.turn-tick.is-pending')[0];
  out.pendingTip = p ? p.dataset.tip : '';
  window.turnRail.addPending('crid-probe-A', '探针提问A：柑橘黄龙病传播途径');
  await sleep(150);
  out.afterDuplicate = q('.turn-tick').length;
  window.turnRail.dropPending('crid-probe-A');
  await sleep(200);
  out.afterDrop = q('.turn-tick').length;
  out.pendingAfterDrop = q('.turn-tick.is-pending').length;
  window.turnRail.addPending('crid-probe-S', %s);
  await sleep(200);
  out.beforeSync = q('.turn-tick').length;
  for (let i = 0; i < 40; i++) {
    await sleep(500);
    const d = await fetch('/api/v2/session/' + %s + '/turns?limit=400').then(r => r.json());
    if ((d.turns || []).length > out.base) { out.serverTurns = (d.turns || []).length; break; }
  }
  await window.turnRail.sync('crid-probe-S', %s);
  await sleep(400);
  out.afterSync = q('.turn-tick').length;
  out.pendingAfterSync = q('.turn-tick.is-pending').length;
  const all = q('.turn-tick');
  out.lastTip = all.length ? all[all.length - 1].dataset.tip : '';
  return out;
})()
""" % (json.dumps(sid), json.dumps(new_text), json.dumps(sid), json.dumps(new_text))

    # 浏览器打开后、sync 之前，往库里插一条真实提问（模拟“本轮已落库”）
    import threading

    def _late_insert():
        time.sleep(9)
        insert_turn(sid, new_text)

    threading.Thread(target=_late_insert, daemon=True).start()
    got = run_browser(sid, script)

    checks = {
        "提交瞬间出现临时刻度": got["afterAdd"] == got["base"] + 1,
        "临时刻度标记为 is-pending": got["pendingTicks"] == 1,
        "提示语写明正在保存": "正在保存" in got["pendingTip"],
        "同一条提问只有一个刻度": got["afterDuplicate"] == got["base"] + 1,
        "失败可撤销": got["afterDrop"] == got["base"] and got["pendingAfterDrop"] == 0,
        "服务端确认后替换为真实刻度": got["afterSync"] == got["base"] + 1,
        "确认后不再残留 pending": got["pendingAfterSync"] == 0,
        "末条提示是服务端真实问题": "柑橘木虱" in got["lastTip"],
    }
    payload = {"session_id": sid, "base_url": BASE, "observed": got,
               "checks": checks, "passed": all(checks.values())}
    (OUT_DIR / "rail_live_update_probe.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    for k, v in checks.items():
        print(("PASS " if v else "FAIL ") + k)
    print("passed =", payload["passed"], "→", OUT_DIR / "rail_live_update_probe.json")
    return 0 if payload["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
