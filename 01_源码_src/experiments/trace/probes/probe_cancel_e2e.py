# -*- coding: utf-8 -*-
"""S1 验收探针：真实 HTTP 上"点停止"到底发生了什么。

这是**外部观察者**：只走公开 HTTP API（SSE + /jobs/{id} + /messages +
/files），不 import agent 内部，所以它观察到的就是用户看到的东西。

流程
----
1. 造一个已登录会话（直接往 browser_sessions 插一枚硬币 cookie）。
2. 发起一轮真实问答，读 SSE 直到拿到 `job` 事件（job_id）并**至少一个
   工具真正开始执行**。
3. 调 `POST /api/v2/chat/jobs/{job_id}/cancel`（精确取消这一个任务）。
4. 继续读流到自然结束，然后核对四件事：
   - `done.turn_status` 是 `cancelled`（不是 failed / completed）
   - 终态 job 记录是 `cancelled` + `stop_reason=user`
   - 历史（/messages）里该轮工具行状态是 `cancelled`，不是假的完成
   - 本轮没有留下"半截产物"（/files 为空）

用法
----
    E:\\anaconda\\envs\\rag-agent\\python.exe experiments/trace/probes/probe_cancel_e2e.py
    # 可选：CITRUS_BASE=http://127.0.0.1:8010 指定其它实例
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
QUERY = os.environ.get("CITRUS_CANCEL_QUERY", "检索黄龙病的传播途径与防控要点，分条列出")


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
    cancel_resp: dict = {}
    cancelled_at = 0.0
    seen_tool_start = False
    t0 = time.time()

    with httpx.Client(timeout=180, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        print(f"[probe] session={sid} base={BASE}")
        body = {"query": QUERY, "session_id": sid}
        if os.environ.get("CITRUS_CANCEL_VIS"):
            body["visualization_enabled"] = True
        with c.stream("POST", f"{BASE}/api/v2/chat", json=body,
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
                if event == "tool_executing" and not cancelled_at and seen_tool_start:
                    pass
                # 工具真开始跑之后才点停止——否则测到的是"还没开始就停"
                if job_id and seen_tool_start and not cancelled_at:
                    time.sleep(float(os.environ.get("CITRUS_CANCEL_DELAY", "0.8")))
                    cancel_resp = c.post(
                        f"{BASE}/api/v2/chat/jobs/{job_id}/cancel").json()
                    cancelled_at = time.time() - t0
                    print(f"[probe] +{cancelled_at:.1f}s cancel -> {json.dumps(cancel_resp, ensure_ascii=False)[:300]}")
                if event == "done":
                    break

        time.sleep(2.0)   # 给收尾/落库一点时间
        job = {}
        if job_id:
            jr = c.get(f"{BASE}/jobs/{job_id}")
            job = jr.json() if jr.status_code == 200 else {"_status": jr.status_code}
        hist = c.get(f"{BASE}/api/v2/session/{sid}/messages").json()
        files = c.get(f"{BASE}/api/v2/session/{sid}/files").json()

    done = next((f["data"] for f in frames if f["event"] == "done"), {})
    tool_rows = [m for m in hist.get("messages", []) if m.get("role") == "tool"]
    tool_events = [f["data"] for f in frames if f["event"] == "tool_result"]
    report = {
        "session_id": sid,
        "job_id": job_id,
        "query": QUERY,
        "cancel_http": cancel_resp,
        "cancelled_at_s": cancelled_at,
        "done_turn_status": done.get("turn_status"),
        "done_answer_chars": len(str(done.get("answer") or "")),
        "done_stop_notice": done.get("stop_notice") or "",
        "job_record": {k: job.get(k) for k in
                       ("status", "stop_reason", "stop_note", "detached_at",
                        "finished_at", "current_step")},
        "tool_results": [
            {"name": d.get("display_name") or d.get("tool_name"),
             "status": d.get("status"), "reason_code": d.get("reason_code"),
             "summary": d.get("summary"), "elapsed_ms": d.get("elapsed_ms")}
            for d in tool_events
        ],
        "history_tool_rows": [
            {"name": m.get("name"), "status": m.get("status"),
             "summary": m.get("summary"), "elapsed_ms": m.get("elapsed_ms")}
            for m in tool_rows
        ],
        "history_has_answer": any(
            m.get("role") == "assistant" and (m.get("content") or "").strip()
            for m in hist.get("messages", [])),
        "files_after": files,
        "frames": len(frames),
        "event_types": sorted({f["event"] for f in frames}),
    }
    report["checks"] = checks = {
        "job_id_exposed": bool(job_id),
        "cancel_accepted": bool(cancel_resp.get("accepted")),
        "job_cancelled": report["job_record"].get("status") == "cancelled",
        "stop_reason_is_user": report["job_record"].get("stop_reason") == "user",
        "turn_status_cancelled": done.get("turn_status") == "cancelled",
        "no_fake_completed_row": not any(
            r.get("status") == "complete" for r in report["history_tool_rows"]
            if any(t.get("status") == "cancelled" for t in report["tool_results"])),
        # 精确断言：被中断的工具必须留下 cancelled 行；没有工具在飞时
        # （停止落在答案生成阶段）就不该凭空造一行 —— 两者必须一致。
        "has_cancelled_row": any(
            r.get("status") == "cancelled" for r in report["history_tool_rows"]) == any(
            t.get("status") == "cancelled" for t in report["tool_results"]),
        "no_half_baked_answer": report["done_answer_chars"] == 0,
        "no_files_committed": not [
            f for f in (files.get("generated") or [])
            if str(f.get("session_id") or "") == report["session_id"]
        ],
    }
    (OUT / "probe_cancel_e2e.json").write_text(
        json.dumps({"report": report, "raw_frames": frames}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("job_id", "cancelled_at_s", "done_turn_status",
                       "job_record", "tool_results", "history_tool_rows",
                       "event_types", "checks")}, ensure_ascii=False, indent=2))
    print(f"[probe] wrote {OUT / 'probe_cancel_e2e.json'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
