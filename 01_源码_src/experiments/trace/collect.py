# -*- coding: utf-8 -*-
"""Collect one real chat turn and persist its execution trace.

Usage
-----
    cd /home/WZQ/CitrusQA/01_源码_src
    ../.venv/bin/python experiments/trace/collect.py S1 "柑橘木虱的防治措施有哪些"

Output
------
``experiments/trace/runs/<YYYYMMDD>/<scenario>/``
    trace.jsonl   one JSON per SSE frame: {"t": "+1.234s", "event": ..., "data": ...}
    session.json  GET /api/v2/session/{sid}/messages (persisted rows)
    summary.json  machine-checkable assertion result + timings
    answer.md     final answer text

The collector is a pure observer over the public HTTP API: it never imports
agent internals, so a scenario run cannot change agent behaviour.
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

sys.path.insert(0, "/home/WZQ/CitrusQA/01_源码_src")
from src import auth  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000")  # override for local instances
RUNS = pathlib.Path(__file__).resolve().parent / "runs"


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


def revoke(token: str) -> None:
    with auth._connect() as c:
        c.execute("DELETE FROM browser_sessions WHERE token_hash=?",
                  (hashlib.sha256(token.encode()).hexdigest(),))


def summarize(frames: list[dict], history: dict, sid: str) -> dict:
    starts = [f for f in frames if f["event"] == "tool_call_start"]
    results = [f for f in frames if f["event"] == "tool_result"]
    done = next((f["data"] for f in frames if f["event"] == "done"), {})
    rows = [m for m in history.get("messages", []) if m.get("role") == "tool"]
    executed = [r for r in results if (r["data"].get("elapsed_ms") or 0) > 0]
    return {
        "session_id": sid,
        "event_types": sorted({f["event"] for f in frames}),
        "tool_starts": len(starts),
        "tool_results": len(results),
        "tools": [
            {
                "name": r["data"].get("display_name") or r["data"].get("tool_name"),
                "status": r["data"].get("status"),
                "reason_code": r["data"].get("reason_code"),
                "summary": r["data"].get("summary"),
                "action_hint": r["data"].get("action_hint"),
                "elapsed_ms": r["data"].get("elapsed_ms"),
            }
            for r in results
        ],
        "executed_count": len(executed),
        "unavailable_count": sum(
            1 for r in results if r["data"].get("reason_code") == "tool_unavailable"),
        "turn_status": done.get("turn_status"),
        "answer_chars": len(str(done.get("answer") or "")),
        "history_tool_rows": [
            {"name": m.get("name"), "status": m.get("status"),
             "summary": m.get("summary"), "elapsed_ms": m.get("elapsed_ms")}
            for m in rows
        ],
    }


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    scenario, query = sys.argv[1], sys.argv[2]
    # Optional 3rd argument: reuse an existing session so a follow-up turn can
    # be traced against the evidence the previous turn already gathered.
    reuse_session = sys.argv[3].strip() if len(sys.argv) > 3 else ""
    outdir = RUNS / datetime.now().strftime("%Y%m%d") / scenario
    outdir.mkdir(parents=True, exist_ok=True)

    token = mint()
    headers = {"Cookie": f"citrus_auth={token}", "Origin": BASE,
               "Accept": "text/event-stream"}
    frames: list[dict] = []
    history: dict = {}
    try:
        with httpx.Client(timeout=600) as c:
            sid = reuse_session or c.post(
                f"{BASE}/api/v1/session/new", headers=headers).json()["session_id"]
            print(f"[{scenario}] session={sid} query={query!r}")
            t0 = time.monotonic()
            with c.stream("POST", f"{BASE}/api/v2/chat", headers=headers,
                          json={"query": query, "session_id": sid,
                                "light_mode": False}) as r:
                etype = ""
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        etype = line[6:].strip()
                    elif line.startswith("data:"):
                        try:
                            payload = json.loads(line[5:].strip())
                        except Exception:
                            payload = {"_raw": line[5:].strip()[:400]}
                        frames.append({"t": f"+{time.monotonic() - t0:.3f}s",
                                       "event": etype, "data": payload})
                        if etype in {"tool_result"}:
                            d = payload
                            print(f"  +{time.monotonic() - t0:6.1f}s  tool_result "
                                  f"{d.get('display_name')} | {d.get('status')} | "
                                  f"{d.get('reason_code') or '-'} | "
                                  f"{d.get('summary')} | {d.get('elapsed_ms')} ms")
                        elif etype in {"done", "error"}:
                            break
            history = c.get(f"{BASE}/api/v2/session/{sid}/messages",
                            headers=headers).json()
    finally:
        revoke(token)

    (outdir / "trace.jsonl").write_text(
        "\n".join(json.dumps(f, ensure_ascii=False) for f in frames) + "\n",
        encoding="utf-8")
    (outdir / "session.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    summary = summarize(frames, history, sid)
    (outdir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    answer = str(next((f["data"].get("answer") or "" for f in frames
                       if f["event"] == "done"), ""))
    (outdir / "answer.md").write_text(answer, encoding="utf-8")

    print(f"\n[{scenario}] executed={summary['executed_count']} "
          f"unavailable={summary['unavailable_count']} "
          f"turn_status={summary['turn_status']} answer_chars={summary['answer_chars']}")
    print(f"[{scenario}] written -> {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
