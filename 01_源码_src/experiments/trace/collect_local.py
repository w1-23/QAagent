# -*- coding: utf-8 -*-
"""Local-instance twin of collect.py (Windows dev box, port 8010).

Why this file exists
--------------------
collect.py hardcodes ``/home/WZQ/CitrusQA/01_源码_src`` and ``127.0.0.1:8000``.
Phase 2A acceptance requires real-model runs *before* anything is deployed, so
the observer needs a local run target. Everything else (SSE parsing, assertions,
artifacts) is intentionally identical so the two runs are comparable.

Usage
-----
    cd E:\\codex_WORKSPACES\\Citrus_QA_Agent\\01_源码_src
    python experiments/trace/collect_local.py R1 "柑橘木虱的防治措施有哪些"

Output: experiments/trace/runs/<YYYYMMDD>/<scenario>/{trace.jsonl,summary.json,answer.md}
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src import auth  # noqa: E402

BASE = "http://127.0.0.1:8010"
RUNS = pathlib.Path(__file__).resolve().parent / "runs"


def mint() -> str:
    """Create a browser session row.

    The dev box keeps the pre-``token`` schema (``token_hash`` + ``user_id``),
    the server keeps ``token``; both accept the same cookie value, so the column
    set is detected instead of hardcoded.
    """
    token = secrets.token_urlsafe(36)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(days=7)
    conn = auth._connect()
    try:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(browser_sessions)")}
        if "token" in columns:
            conn.execute(
                "INSERT INTO browser_sessions (token, created_at, expires_at) VALUES (?, ?, ?)",
                (token, now.isoformat(), expires.isoformat()),
            )
        else:
            conn.execute(
                "INSERT INTO browser_sessions (token_hash, user_id, created_at, expires_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    1,
                    now.isoformat(),
                    expires.isoformat(),
                ),
            )
        conn.commit()
    finally:
        conn.close()
    return token


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    scenario, query = sys.argv[1], sys.argv[2]
    reuse = sys.argv[3] if len(sys.argv) > 3 else ""

    token = mint()
    session_id = reuse
    started = time.time()
    frames: list[dict] = []
    answer_parts: list[str] = []
    tool_rows: list[dict] = []

    body = {"query": query, "light_mode": False}
    if session_id:
        body["session_id"] = session_id

    with httpx.Client(timeout=600.0, trust_env=False) as client:
        with client.stream(
            "POST",
            f"{BASE}/api/v2/chat",
            json=body,
            headers={"Cookie": f"citrus_auth={token}", "Origin": BASE},
        ) as resp:
            resp.raise_for_status()
            event = ""
            for raw in resp.iter_lines():
                line = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                payload = line.split(":", 1)[1].strip()
                try:
                    data = json.loads(payload)
                except Exception:
                    data = {"_raw": payload}
                frames.append(
                    {"t": f"+{time.time() - started:.3f}s", "event": event, "data": data}
                )
                if event in ("token", "text"):
                    answer_parts.append(str(data.get("delta") or data.get("content") or ""))
                elif event == "tool_call_start":
                    tool_rows.append(
                        {
                            "name": data.get("display_name") or data.get("name"),
                            "id": data.get("tool_call_id"),
                            "action": data.get("action_hint"),
                            "status": "started",
                        }
                    )
                elif event == "tool_result":
                    row = next(
                        (r for r in tool_rows if r.get("id") == data.get("tool_call_id")
                         and r["status"] == "started"),
                        None,
                    )
                    target = row or {"name": data.get("display_name") or data.get("name"),
                                     "id": data.get("tool_call_id"), "action": ""}
                    target.update(
                        {
                            "status": data.get("status"),
                            "elapsed_ms": data.get("elapsed_ms"),
                            "summary": data.get("summary"),
                            "reason": data.get("reason_code"),
                        }
                    )
                    if row is None:
                        tool_rows.append(target)
                elif event == "done" and isinstance(data, dict):
                    if isinstance(data.get("answer"), str) and not answer_parts:
                        answer_parts.append(data["answer"])
                    session_id = data.get("session_id") or session_id

    out = RUNS / datetime.now().strftime("%Y%m%d") / scenario
    out.mkdir(parents=True, exist_ok=True)
    with (out / "trace.jsonl").open("w", encoding="utf-8") as fh:
        for frame in frames:
            fh.write(json.dumps(frame, ensure_ascii=False) + "\n")
    answer = "".join(answer_parts).strip()
    (out / "answer.md").write_text(answer, encoding="utf-8")

    summary = {
        "scenario": scenario,
        "query": query,
        "session_id": session_id,
        "wall_s": round(time.time() - started, 3),
        "event_types": sorted({f["event"] for f in frames}),
        "tool_calls": tool_rows,
        "tool_call_count": len(tool_rows),
        "answer_chars": len(answer),
        "answer_head": answer[:400],
    }
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
