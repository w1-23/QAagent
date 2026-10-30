# -*- coding: utf-8 -*-
"""发布准入矩阵「Agent 执行独立性」：关掉公开进度通道后，执行结果不变。

S2 引入的 `assistant_progress` 只应影响"看得见什么"，不应影响"做了什么"。
本探针用同一个问题、同一套代码、两份实例（唯一差别是 `CITRUS_PUBLIC_PROGRESS`
=1 / =0）各跑一次，比较：工具调用集合、工具终态、轮次状态、回答长度、
以及进度事件数量。

用法
----
    CITRUS_BASE=http://127.0.0.1:8011 CITRUS_LABEL=on  python probe_progress_independence.py
    CITRUS_BASE=http://127.0.0.1:8012 CITRUS_LABEL=off python probe_progress_independence.py
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

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import auth  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000").rstrip("/")
LABEL = os.environ.get("CITRUS_LABEL", "run")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = os.environ.get("CITRUS_QUERY", "柑橘黄龙病的传播途径是什么？请给出来源。")
WAIT = float(os.environ.get("CITRUS_WAIT", "240"))


def mint() -> str:
    token = secrets.token_urlsafe(36)
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, auth._stamp(),
                   auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))))
    return token


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tok = mint()
    H = {"Cookie": f"citrus_auth={tok}", "Origin": BASE}
    counts: dict[str, int] = {}
    job_id = ""
    with httpx.Client(timeout=300, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        with c.stream("POST", f"{BASE}/api/v2/chat",
                      json={"query": QUERY, "session_id": sid},
                      headers={**H, "Accept": "text/event-stream"}) as r:
            event = ""
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    counts[event] = counts.get(event, 0) + 1
                elif line.startswith("data:") and event == "job" and not job_id:
                    try:
                        job_id = (json.loads(line[5:].strip()) or {}).get("job_id", "")
                    except Exception:
                        pass
        # 等历史落库
        deadline = time.time() + WAIT
        msgs = []
        while time.time() < deadline:
            msgs = c.get(f"{BASE}/api/v2/session/{sid}/messages").json().get("messages") or []
            if any(m.get("role") == "assistant" and (m.get("content") or "").strip() for m in msgs):
                break
            time.sleep(2)
        job = c.get(f"{BASE}/jobs/{job_id}").json() if job_id else {}
        answer = "".join(m.get("content") or "" for m in msgs if m.get("role") == "assistant")
        tools = [m for m in msgs if m.get("role") in ("tool", "tool_calls")]
        report = {
            "label": LABEL, "base": BASE, "session": sid, "job_id": job_id,
            "query": QUERY,
            "event_counts": counts,
            "progress_events": counts.get("assistant_progress", 0),
            "tool_events": counts.get("tool_call_start", 0),
            "job_status": job.get("status"), "turn_status": job.get("turn_status"),
            "messages": len(msgs),
            "tool_rows": len(tools),
            "answer_chars": len(answer),
            "answer_head": answer[:120].replace("\n", " "),
        }
    path = OUT / f"probe_independence_{LABEL}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: report[k] for k in
                      ("label", "progress_events", "tool_events", "job_status",
                       "turn_status", "messages", "tool_rows", "answer_chars")},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
