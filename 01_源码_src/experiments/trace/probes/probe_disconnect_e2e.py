# -*- coding: utf-8 -*-
"""发布准入矩阵「SSE 断线」验收探针：断线 ≠ 主动取消。

这是**外部观察者**：只走公开 HTTP API，不 import agent 内部。

为什么必须单独测
----------------
S1 把"客户端断线"和"用户点停止"分成了两条路径：断线只把 job 标成
`detached`，执行继续，到期仍未结束才按 `detached_timeout` 收尾。
如果哪天有人把断线又接回 `request_cancel()`，用户会看到"任务莫名被取消"，
而这种回归**单元测试抓不到**——只有真的把 TCP 连接掐掉才能验证。

流程
----
1. 造一个已登录会话。
2. 发起真实问答，读到 `job` 事件 + 至少一个工具开始执行。
3. **硬关连接**（close client，不发任何 cancel 请求）。
4. 立刻查 job：必须是 running/detached，不能是 cancelled。
5. 轮询到终态：必须是 `completed`，`stop_reason` 不能是 `user`。
6. 查历史：必须留下真实回答；工具行不能是 cancelled。

用法
----
    E:\\anaconda\\envs\\rag-agent\\python.exe experiments/trace/probes/probe_disconnect_e2e.py
    # CITRUS_BASE=http://127.0.0.1:8010 指向其它实例
    # CITRUS_DISCONNECT_WAIT=240 放宽等待上限（秒）
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

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = os.environ.get(
    "CITRUS_DISCONNECT_QUERY",
    "检索黄龙病的传播途径与防控要点，分条列出并给出可核对的来源",
)
WAIT = float(os.environ.get("CITRUS_DISCONNECT_WAIT", "240"))


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    token = mint()
    H = {"Cookie": f"citrus_auth={token}", "Origin": BASE}
    frames: list[dict] = []
    job_id = ""
    seen_tool_start = False
    t0 = time.time()

    # ---- 第一步：发起并读到"工具真的在跑"，然后掐断连接 ----
    streamer = httpx.Client(timeout=300, headers=H)
    try:
        sid = streamer.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        print(f"[probe] session={sid} base={BASE}")
        with streamer.stream("POST", f"{BASE}/api/v2/chat",
                             json={"query": QUERY, "session_id": sid},
                             headers={**H, "Accept": "text/event-stream"}) as r:
            event = ""
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
                frames.append({"t": round(time.time() - t0, 3), "event": event, "data": data})
                if event == "job" and not job_id:
                    job_id = str(data.get("job_id") or "")
                    print(f"[probe] +{time.time()-t0:.1f}s job={job_id[:12]}")
                if event == "tool_call_start":
                    seen_tool_start = True
                    print(f"[probe] +{time.time()-t0:.1f}s tool start: "
                          f"{data.get('display_name')} / {data.get('action_hint')}")
                if job_id and seen_tool_start:
                    break            # 工具在飞 —— 现在掐线
    finally:
        # 硬关：不发 cancel，直接断 TCP（模拟用户关标签页/掉网）
        try:
            streamer.close()
        except Exception:
            pass
    cut_at = round(time.time() - t0, 3)
    print(f"[probe] +{cut_at}s connection closed (no cancel request sent)")

    # ---- 第二步：断线后立刻查一次 job，再轮询到终态 ----
    time.sleep(1.5)
    snapshots: list[dict] = []
    job: dict = {}
    terminal = {"completed", "cancelled", "failed", "error", "stopped"}
    with httpx.Client(timeout=60, headers=H) as c:
        deadline = time.time() + WAIT
        while True:
            jr = c.get(f"{BASE}/jobs/{job_id}")
            job = jr.json() if jr.status_code == 200 else {"_status": jr.status_code}
            snapshots.append({
                "t": round(time.time() - t0, 1),
                "status": job.get("status"),
                "stop_reason": job.get("stop_reason"),
                "current_step": job.get("current_step"),
            })
            if str(job.get("status")) in terminal:
                break
            if time.time() > deadline:
                break
            time.sleep(2.0)

        time.sleep(1.5)          # 给落库一点时间
        hist = c.get(f"{BASE}/api/v2/session/{sid}/messages").json()
        files = c.get(f"{BASE}/api/v2/session/{sid}/files").json()

    tool_rows = [m for m in hist.get("messages", []) if m.get("role") == "tool"]
    answers = [m for m in hist.get("messages", [])
               if m.get("role") == "assistant" and (m.get("content") or "").strip()]
    first_after_cut = next(
        (s for s in snapshots if s["t"] >= cut_at), snapshots[0] if snapshots else {})
    report = {
        "session_id": sid,
        "job_id": job_id,
        "query": QUERY,
        "cut_at_s": cut_at,
        "job_snapshots": snapshots,
        "job_first_after_cut": first_after_cut,
        "job_final": {k: job.get(k) for k in
                      ("status", "stop_reason", "stop_note", "detached_at",
                       "finished_at", "current_step")},
        "history_assistant_answers": len(answers),
        "history_answer_chars": len((answers[-1].get("content") or "")) if answers else 0,
        "history_tool_rows": [
            {"name": m.get("name"), "status": m.get("status"),
             "summary": m.get("summary"), "elapsed_ms": m.get("elapsed_ms")}
            for m in tool_rows
        ],
        "files_generated": len((files.get("generated") or [])),
        "frames_before_cut": len(frames),
        "event_types": sorted({f["event"] for f in frames}),
    }
    report["checks"] = checks = {
        "job_id_exposed": bool(job_id),
        "cut_while_tool_running": seen_tool_start,
        # 断线后第一眼：必须还在跑（detached/running/...），不能已被取消
        "not_cancelled_right_after_cut": str(first_after_cut.get("status")) not in
        {"cancelled", "failed", "error"},
        # 终态：允许 completed（跑完）；若宽限期太短则可能是 detached_timeout，
        # 但**绝不能**是 user 主动取消
        "final_not_user_cancel": str(job.get("stop_reason") or "") != "user",
        "final_status_not_cancelled": str(job.get("status")) not in {"cancelled"},
        "work_continued": report["history_assistant_answers"] >= 1,
        "answer_not_empty": report["history_answer_chars"] > 0,
        "no_cancelled_tool_rows": not any(
            r.get("status") == "cancelled" for r in report["history_tool_rows"]),
    }
    (OUT / "probe_disconnect_e2e.json").write_text(
        json.dumps({"report": report, "raw_frames": frames}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("job_id", "cut_at_s", "job_first_after_cut", "job_final",
                       "history_assistant_answers", "history_answer_chars",
                       "history_tool_rows", "event_types", "checks")},
                     ensure_ascii=False, indent=2))
    print(f"[probe] wrote {OUT / 'probe_disconnect_e2e.json'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
