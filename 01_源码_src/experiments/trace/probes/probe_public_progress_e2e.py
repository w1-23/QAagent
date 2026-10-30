# -*- coding: utf-8 -*-
"""S2 验收探针：公开工作说明（assistant_progress）在真实 HTTP 上的表现。

外部观察者视角：只走公开 SSE，不 import Agent 内部。要回答三个问题：

1. 模型这一轮到底有没有产生公开文本？有 -> 变成 assistant_progress；
2. 它是不是出现在**同一轮工具调用之前**（顺序正确）；
3. 最终回答里有没有混进这段工作说明（污染/重复）。

用法：
    E:\\anaconda\\envs\\rag-agent\\python.exe experiments/trace/probes/probe_public_progress_e2e.py
    # 可选：CITRUS_BASE / CITRUS_PROGRESS_QUERY
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
    "CITRUS_PROGRESS_QUERY",
    "先检索柑橘黄龙病的传播途径，再对比本地资料与公开来源的异同，分条说明。")


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
    t0 = time.time()

    with httpx.Client(timeout=300, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        print(f"[probe] session={sid} base={BASE}")
        body = {"query": QUERY, "session_id": sid}
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
                if event in ("assistant_progress", "tool_call_start", "tool_result",
                             "text", "done", "job", "heartbeat", "context_usage"):
                    frames.append({"i": len(frames), "t": round(time.time() - t0, 3),
                                   "event": event, "data": data})
                if event == "done":
                    break

    progress = [f for f in frames if f["event"] == "assistant_progress"]
    tool_starts = [f for f in frames if f["event"] == "tool_call_start"]
    done = next((f["data"] for f in frames if f["event"] == "done"), {})
    answer = str(done.get("answer") or "")
    streamed_text = "".join(str(f["data"].get("content") or "") for f in frames
                            if f["event"] == "text")
    progress_texts = [str(f["data"].get("text") or "") for f in progress]

    # 顺序：每条工作说明都应出现在"紧随其后的第一个工具开始"之前
    ordering_ok = True
    for p in progress:
        later_starts = [t for t in tool_starts if t["i"] > p["i"]]
        if not later_starts:
            ordering_ok = False
    # 成本口径：模型调用次数与 token（用于对比"加工作说明前后"没有多花请求）
    usage = [f["data"] for f in frames if f["event"] == "context_usage"
             and (f["data"].get("source") == "general_agent"
                  or (f["data"].get("call") or {}).get("source") == "general_agent")]
    calls = [((u.get("call") or u)) for u in usage]
    report = {
        "session_id": sid,
        "model_calls": len(calls),
        "input_tokens": sum(int(c.get("input_tokens") or 0) for c in calls),
        "output_tokens": sum(int(c.get("output_tokens") or 0) for c in calls),
        "public_progress_enabled": __import__("os").environ.get("CITRUS_PUBLIC_PROGRESS", "1") != "0",
        "query": QUERY,
        "elapsed_s": round(time.time() - t0, 1),
        "done_turn_status": done.get("turn_status"),
        "answer_chars": len(answer),
        "streamed_text_chars": len(streamed_text),
        "progress_count": len(progress),
        "progress_texts": progress_texts,
        "progress_payload_sample": progress[0]["data"] if progress else None,
        "tool_starts": [{"display_name": f["data"].get("display_name"),
                         "action_hint": f["data"].get("action_hint"),
                         "at_s": f["t"]} for f in tool_starts],
        "event_sequence": [f["event"] for f in frames],
        "checks": {},
    }
    checks = report["checks"] = {
        "answer_not_polluted": all(t not in answer for t in progress_texts if t),
        "stream_not_polluted": all(t not in streamed_text for t in progress_texts if t),
        "progress_precedes_following_tool": ordering_ok,
        "tools_actually_ran": len(tool_starts) > 0,
        "turn_completed": done.get("turn_status") in ("completed", "partial"),
        "progress_has_no_protocol": all(
            "DSML" not in t and "<|" not in t for t in progress_texts),
    }
    (OUT / "probe_public_progress_e2e.json").write_text(
        json.dumps({"report": report, "frames": frames}, ensure_ascii=False, indent=2),
        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    print(f"[probe] wrote {OUT / 'probe_public_progress_e2e.json'}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
