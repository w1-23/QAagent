# -*- coding: utf-8 -*-
"""A1 复现探针：真实问答期间，事件循环被谁占住？

这是**外部观察者**：只走公开 HTTP API（外加一枚只读探针 cookie），不 import
agent 内部实现。它做三件事：

1. 清零看门狗计数；
2. 跑一次真实问答（默认走本地检索），把 SSE 帧收齐；
3. 读回 ``/api/v2/diag/loop-watchdog``——里面每次卡顿都带着**循环线程的栈**，
   最深那一帧就是占住事件循环的函数。

判读方式
--------
- 卡顿帧指向 ``time.sleep`` / ``requests`` / ``.search(`` / ``.encode(`` 这类
  同步调用 ⇒ 该调用跑在事件循环上，必须移到执行器线程。
- 卡顿帧指向 ``sqlite3`` 写库或大 JSON 序列化 ⇒ 属于"短但频繁"的循环占用，
  按需移出。
- 只有 asyncio/uvicorn 帧 ⇒ 说明这段时间循环在正常等待，不是阻塞。

用法
----
    python experiments/trace/probes/probe_loop_stall_e2e.py
    # CITRUS_BASE=http://127.0.0.1:8010  指定实例
    # CITRUS_LOOP_QUERY="..."            自定义问题
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[3]          # 01_源码_src
sys.path.insert(0, str(ROOT))
from src import auth  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8010").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = os.environ.get(
    "CITRUS_LOOP_QUERY",
    "检索黄龙病的传播途径与防控要点，分条列出并给出可核对的来源",
)
WAIT = float(os.environ.get("CITRUS_LOOP_WAIT", "300"))


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


def read_watchdog(client: httpx.Client, reset: bool = False) -> dict:
    url = f"{BASE}/api/v2/diag/loop-watchdog" + ("?reset=1" if reset else "")
    r = client.get(url)
    if r.status_code != 200:
        return {"_status": r.status_code, "_body": r.text[:300]}
    return r.json()


def run_turn(client: httpx.Client, headers: dict, query: str) -> dict:
    sid = client.post(f"{BASE}/api/v1/session/new").json()["session_id"]
    t0 = time.time()
    frames: list[dict] = []
    event = ""
    answer_chars = 0
    tools: list[str] = []
    with client.stream("POST", f"{BASE}/api/v2/chat",
                       json={"query": query, "session_id": sid},
                       headers={**headers, "Accept": "text/event-stream"},
                       timeout=WAIT) as r:
        for line in r.iter_lines():
            if line.startswith("event:"):
                event = line[6:].strip()
                continue
            if not line.startswith("data:"):
                continue
            raw = line[5:].strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"_raw": raw[:200]}
            frames.append({"t": round(time.time() - t0, 3), "event": event})
            if event == "tool_call_start":
                tools.append(str(data.get("tool") or data.get("display_name") or ""))
            if event == "done":
                answer_chars = len(str(data.get("answer") or ""))
            if event == "error":
                answer_chars = 0
    ts = [f["t"] for f in frames]
    gaps = [(round(b - a, 2), a) for a, b in zip(ts, ts[1:])]
    gaps.sort(reverse=True)
    return {
        "session_id": sid,
        "elapsed_s": round(time.time() - t0, 1),
        "frames": len(frames),
        "tools": tools,
        "answer_chars": answer_chars,
        "event_types": sorted({f["event"] for f in frames}),
        # 用户视角的"卡住"= 相邻 SSE 帧之间的空档
        "max_gap_s": gaps[0][0] if gaps else 0.0,
        "gaps_over_1s": [g for g in gaps if g[0] >= 1.0][:8],
        "timeline": frames[:40],
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    token = mint()
    H = {"Cookie": f"citrus_auth={token}", "Origin": BASE}

    with httpx.Client(timeout=60, headers=H) as c:
        before = read_watchdog(c, reset=True)
        print(f"[probe] base={BASE} watchdog_installed={before.get('installed')} "
              f"stall_ms={before.get('stall_ms')}")
        if not before.get("installed"):
            print("[probe] 看门狗未启用：请用 CITRUSQA_LOOP_WATCHDOG=1 启动服务")
            return 2

        turn = run_turn(c, H, QUERY)
        print(f"[probe] turn done in {turn['elapsed_s']}s, frames={turn['frames']}, "
              f"tools={turn['tools']}, max_gap={turn['max_gap_s']}s")
        for gap, at in turn["gaps_over_1s"]:
            print(f"  gap {gap}s starting at t={at}s")

        after = read_watchdog(c)
        stalls = after.get("stalls") or []
        print(f"[probe] stalls={after.get('stall_count')} max={after.get('max_stall_ms')}ms")
        for stall in stalls:
            print(f"  - {stall['stall_ms']} ms @ {stall['at']}")
            for line in stall["frames"][:4]:
                print(f"      {line}")

    report = {
        "base": BASE,
        "query": QUERY,
        "watchdog_before": {k: before.get(k) for k in
                            ("installed", "stall_ms", "tick_s", "stall_count")},
        "turn": turn,
        "watchdog_after": after,
        "checks": {
            "watchdog_installed": bool(before.get("installed")),
            "turn_completed": turn["answer_chars"] > 0,
            "stalls_recorded": bool(stalls),
            # 用户可见判定：工具执行期间 SSE 是否长时间无帧（>3s 即明显"卡住"）
            "max_sse_gap_s": turn["max_gap_s"],
            "ui_stalled_over_3s": turn["max_gap_s"] >= 3.0,
            # 判定：只要卡顿帧里出现明显的同步调用，就说明找到阻塞点。
            "blocking_frames_named": any(
                token in line
                for stall in stalls
                for line in stall["frames"]
                for token in ("time.sleep", "requests", "sqlite3", ".search(",
                              ".encode(", "urlopen", "subprocess")
            ),
        },
    }
    (OUT / "probe_loop_stall_e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False))
    print(f"[probe] wrote {OUT / 'probe_loop_stall_e2e.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
