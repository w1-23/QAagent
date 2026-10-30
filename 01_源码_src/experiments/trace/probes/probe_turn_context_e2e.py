# -*- coding: utf-8 -*-
"""D-A1 验收探针：本轮**实际发送给模型**的请求快照是否真的被记录。

外部观察者视角：只走公开 HTTP（SSE 对话 + /turn-context 端点），不 import
Agent 内部；断言的是"发出去的那一份"，不是按角色重建的近似。

三个问题：
1. 一轮真实问答后，`/turn-context` 是否有记录？条数/字符/工具消息数是多少？
2. 记录里的角色顺序与真实调用是否一致（system → user → assistant(tool_calls) → tool）？
3. 用量是否来自真实 usage（而非估算）？

用法：
    python experiments/trace/probes/probe_turn_context_e2e.py
    # 可选：CITRUS_BASE / CITRUS_TC_QUERY
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
QUERY = os.environ.get("CITRUS_TC_QUERY", "柑橘黄龙病的传播媒介是什么？")


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
    result: dict = {"base": BASE, "query": QUERY, "checks": {}, "snapshot": None}

    with httpx.Client(timeout=300, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        result["session_id"] = sid
        print(f"[probe] session={sid} base={BASE}")

        t0 = time.time()
        with c.stream("POST", f"{BASE}/api/v2/chat",
                      json={"query": QUERY, "session_id": sid},
                      headers={**H, "Accept": "text/event-stream"}) as r:
            for _ in r.iter_lines():
                pass
        result["turn_seconds"] = round(time.time() - t0, 2)

        data = c.get(f"{BASE}/api/v2/session/{sid}/turn-context",
                     params={"limit": 20}).json()
        result["raw"] = {k: v for k, v in data.items() if k != "rows"}
        rows = data.get("rows") or []

        checks = result["checks"]
        checks["found"] = bool(data.get("found"))
        checks["calls"] = len(rows)
        checks["scope_is_measured"] = data.get("scope") == "measured"
        if rows:
            row = rows[0]
            payload = row.get("payload") or {}
            kinds = [m.get("kind") for m in payload.get("messages") or []]
            checks["msg_count"] = row.get("msg_count")
            checks["chars"] = row.get("chars")
            checks["tool_msgs"] = row.get("tool_msgs")
            checks["input_tokens"] = row.get("input_tokens")
            checks["output_tokens"] = row.get("output_tokens")
            checks["first_kind_is_system"] = bool(kinds) and kinds[0] == "system"
            checks["has_user_msg"] = "user" in kinds
            checks["kinds"] = kinds
            # 明细里不得出现整段系统提示词（只允许 160 字符预览）
            previews = [len(str(m.get("preview") or "")) for m in payload.get("messages") or []]
            checks["preview_max_chars"] = max(previews) if previews else 0
            checks["preview_within_cap"] = all(p <= 160 for p in previews)
            result["snapshot"] = row

        checks["pass"] = bool(
            checks.get("found") and checks.get("scope_is_measured")
            and checks.get("calls", 0) >= 1
            and checks.get("msg_count", 0) >= 2
            and checks.get("chars", 0) > 0
            and checks.get("first_kind_is_system")
            and checks.get("has_user_msg")
            and checks.get("preview_within_cap")
        )

    path = OUT / f"turn_context_e2e_{datetime.now().strftime('%H%M%S')}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result["checks"], ensure_ascii=False, indent=2))
    print(f"[probe] saved {path}")
    print("[probe] PASS" if result["checks"]["pass"] else "[probe] FAIL")
    return 0 if result["checks"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
