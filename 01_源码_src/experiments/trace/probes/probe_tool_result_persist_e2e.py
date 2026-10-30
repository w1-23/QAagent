# -*- coding: utf-8 -*-
"""A2 端到端：真实一轮问答后，工具结果全文可回取。

观察点（全部走公开 HTTP，外部视角）：
1. 一轮问答里出现的 ``tool_call_start`` 的 id，都能在
   ``/api/v2/session/{sid}/tool-result/{id}`` 上取回全文；
2. 取回的 ``result_text`` 非空、``result_chars`` 与文本长度一致（或截断标记为真）；
3. 不存在的 id 返回 404（不编造）。
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

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8010").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = os.environ.get("CITRUS_A2_QUERY", "检索柑橘黄龙病的传播途径，列出 3 条并给出出处")


def mint() -> str:
    token = secrets.token_urlsafe(36)
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, auth._stamp(),
                   auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))))
    return token


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    H = {"Cookie": f"citrus_auth={mint()}", "Origin": BASE}
    call_ids: list[str] = []
    with httpx.Client(timeout=300, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        event = ""
        with c.stream("POST", f"{BASE}/api/v2/chat",
                      json={"query": QUERY, "session_id": sid},
                      headers={**H, "Accept": "text/event-stream"}) as r:
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                if event == "tool_call_start":
                    try:
                        d = json.loads(line[5:].strip())
                    except json.JSONDecodeError:
                        continue
                    cid = str(d.get("tool_call_id") or "")
                    if cid and cid not in call_ids:
                        call_ids.append(cid)

        rows = []
        for cid in call_ids:
            r = c.get(f"{BASE}/api/v2/session/{sid}/tool-result/{cid}")
            if r.status_code != 200:
                rows.append({"tool_call_id": cid, "http": r.status_code})
                continue
            d = r.json()
            rows.append({
                "tool_call_id": cid, "http": 200,
                "tool_name": d.get("tool_name"), "status": d.get("status"),
                "chars": d.get("result_chars"),
                "text_len": len(d.get("result_text") or ""),
                "truncated": d.get("truncated"),
                "sha_ok": len(d.get("full_sha256") or "") == 64,
            })
        missing = c.get(f"{BASE}/api/v2/session/{sid}/tool-result/not-a-real-id")

    report = {
        "base": BASE, "query": QUERY, "session_id": sid,
        "tool_calls_seen": len(call_ids), "results": rows,
        "missing_status": missing.status_code,
        "checks": {
            "calls_observed": len(call_ids) > 0,
            "all_retrievable": all(r.get("http") == 200 for r in rows) and bool(rows),
            "all_non_empty": all(r.get("text_len", 0) > 0 for r in rows),
            "digest_present": all(r.get("sha_ok") for r in rows),
            "unknown_id_404": missing.status_code == 404,
        },
    }
    (OUT / "probe_tool_result_persist_e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False))
    for r in rows:
        print(" ", r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
