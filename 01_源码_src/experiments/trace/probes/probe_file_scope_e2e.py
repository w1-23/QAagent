# -*- coding: utf-8 -*-
"""验收探针：文件库的会话作用域是否名副其实。

只走公开 HTTP，外部观察者视角。要证明四件事：

1. ``GET /api/v2/session/{sid}/files`` 只返回该会话的文件（旧实现返回整个账号库）；
2. ``GET /api/v2/files`` 默认仍是整个账号库（文件库页不能被改坏）；
3. ``GET /api/v2/files?session_id=`` 能按会话过滤，且 ``scope`` 如实标注；
4. 传入别人的/不存在的 session_id 得到 404，而不是"帮你列一下"。

用法：
    python experiments/trace/probes/probe_file_scope_e2e.py
    # 可选：CITRUS_BASE（默认 http://127.0.0.1:8000）
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
from src.session import files as session_files  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


def _names(payload: dict) -> list[str]:
    rows = list(payload.get("uploaded") or []) + list(payload.get("generated") or [])
    return sorted(str(r.get("filename") or "") for r in rows)


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    token = mint()
    H = {"Cookie": f"citrus_auth={token}", "Origin": BASE}
    checks: dict[str, dict] = {}
    report: dict = {"base": BASE, "checks": checks}

    def check(name: str, ok: bool, detail) -> None:
        checks[name] = {"ok": bool(ok), "detail": detail}
        print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")

    with httpx.Client(timeout=120, headers=H) as c:
        sid_a = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        sid_b = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        report.update(session_a=sid_a, session_b=sid_b)

        # Seed one file per session through the store the server reads.
        # (Files normally enter via chat attachments; a real model turn per file
        # would make this probe slow and non-deterministic.)
        stamp = secrets.token_hex(3)
        seeded = {}
        for sid, name in ((sid_a, f"scope_a_{stamp}.csv"),
                          (sid_b, f"scope_b_{stamp}.csv")):
            seeded[sid] = session_files.register_file(
                session_id=sid, owner_user_id=1, filename=name,
                content=b"x,y\n1,2\n", mime="text/csv")["filename"]
        report["seeded"] = seeded

        r = c.get(f"{BASE}/api/v2/session/{sid_a}/files")
        pa = r.json() if r.status_code == 200 else {}
        names_a = _names(pa)
        check("session_route_scope_label", pa.get("scope") == "session",
              f"scope={pa.get('scope')!r}")
        check("session_route_only_own_files",
              names_a == [f"scope_a_{stamp}.csv"],
              f"names={names_a}")

        r = c.get(f"{BASE}/api/v2/files")
        pall = r.json() if r.status_code == 200 else {}
        names_all = _names(pall)
        check("account_route_keeps_library",
              pall.get("scope") == "account"
              and f"scope_a_{stamp}.csv" in names_all
              and f"scope_b_{stamp}.csv" in names_all,
              f"scope={pall.get('scope')!r} count={pall.get('count')}")

        r = c.get(f"{BASE}/api/v2/files", params={"session_id": sid_b})
        pb = r.json() if r.status_code == 200 else {}
        names_b = _names(pb)
        check("account_route_honours_filter",
              pb.get("scope") == "session"
              and names_b == [f"scope_b_{stamp}.csv"],
              f"scope={pb.get('scope')!r} names={names_b}")

        foreign = "sess-" + secrets.token_hex(8)
        r1 = c.get(f"{BASE}/api/v2/session/{foreign}/files")
        r2 = c.get(f"{BASE}/api/v2/files", params={"session_id": foreign})
        check("unknown_session_is_404",
              r1.status_code == 404 and r2.status_code == 404,
              f"route={r1.status_code} filtered={r2.status_code}")

    ok = all(v["ok"] for v in checks.values())
    report["passed"] = ok
    report["failed"] = [k for k, v in checks.items() if not v["ok"]]
    path = OUT / f"probe_file_scope_{datetime.now().strftime('%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{'ALL PASS' if ok else 'FAILED'}: {path}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
