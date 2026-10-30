# -*- coding: utf-8 -*-
"""发布准入矩阵「账号安全」验收探针：跨用户越权。

矩阵里这一行原本只有单元测试（`test_*_ownership`），没有一次真实的
"另一个已登录账号"通过 HTTP 去够别人的东西。这个探针就是补这一行。

流程
----
1. 注册两个账号（A、B），各自登录拿到 cookie。
2. A 建会话、登记一份上传文件、产出一个 artifact（走真实 `_save_artifact`）。
3. A 自测：自己的会话/文件/artifact 必须 200（对照组）。
4. B 越权：会话消息、会话文件列表、文件库、文件预览、文件下载、artifact 下载、
   删除文件、取消 A 的 job —— 必须全部 403/404，且**不能**产生副作用
   （A 的文件仍在、A 的 job 状态不变）。

用法
----
    CITRUS_BASE=http://127.0.0.1:8011 /path/.venv/bin/python probe_security_crossuser.py
"""
from __future__ import annotations

import hashlib
import json
import os
import pathlib
import secrets
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx

ROOT = pathlib.Path(__file__).resolve().parents[3]          # 01_源码_src
INSTANCE = ROOT.parent
sys.path.insert(0, str(ROOT))

from src import auth  # noqa: E402
from src.core import tracing  # noqa: E402
from src.session import files as session_files  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
DENIED = {401, 403, 404}


def make_user(tag: str) -> tuple[str, str, dict]:
    name = f"probe_{tag}_{secrets.token_hex(3)}"
    pwd = "Probe!" + secrets.token_hex(6)
    user, err, _detail = auth.register(name, pwd)
    if not user:
        raise SystemExit(f"register failed: {err}")
    logged, err2 = auth.login(name, pwd)
    if not logged:
        raise SystemExit(f"login failed: {err2}")
    return name, pwd, logged


def cookie_for(username: str, password: str) -> str:
    r = httpx.post(f"{BASE}/api/v2/auth/login",
                   json={"username": username, "password": password}, timeout=30)
    r.raise_for_status()
    tok = r.cookies.get("citrus_auth")
    if not tok:
        raise SystemExit("login returned no cookie")
    return tok


def mint_for(user_id: int) -> str:
    token = secrets.token_urlsafe(36)
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), user_id,
                   auth._stamp(),
                   auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))))
    return token


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    name_a, pwd_a, user_a = make_user("a")
    name_b, pwd_b, user_b = make_user("b")
    tok_a, tok_b = cookie_for(name_a, pwd_a), cookie_for(name_b, pwd_b)
    HA = {"Cookie": f"citrus_auth={tok_a}", "Origin": BASE}
    HB = {"Cookie": f"citrus_auth={tok_b}", "Origin": BASE}
    uid_a = int(user_a["user_id"])
    uid_b = int(user_b["user_id"])

    # ---- A: 会话 + 文件 + artifact ----
    sid = httpx.post(f"{BASE}/api/v1/session/new", headers=HA, timeout=30).json()["session_id"]
    rec = session_files.register_file(
        session_id=sid, owner_user_id=uid_a, filename="甲的资料.csv",
        content=b"variety,yield\nNagami,12\n", mime="text/csv",
        origin=session_files.ORIGIN_UPLOADED, client_request_id="crid-probe-a")
    file_id = rec["file_id"]
    tracing.set_session_id(sid)
    from src.research.tools import _save_artifact  # noqa: E402

    art_url = _save_artifact("甲的报告.html", b"<html>ok</html>", "text/html")
    art_id = art_url.rstrip("/").split("/")[-1]

    # ---- A: job 记录（真实 task_jobs 行，状态 running）----
    from src.core import jobs as jobs_mod  # noqa: E402

    # create_job 默认 status="running"；取消鉴权靠 job.session_id 的归属，
    # task_jobs 没有 owner 列（归属唯一来源就是会话）。
    job_id = jobs_mod.create_job(sid, "crid-probe-a")

    def code(resp: httpx.Response) -> int:
        return resp.status_code

    results: dict[str, object] = {}

    # ---- 对照组：A 自己能访问 ----
    results["A_own_messages"] = code(httpx.get(f"{BASE}/api/v2/session/{sid}/messages", headers=HA, timeout=30))
    results["A_own_files"] = code(httpx.get(f"{BASE}/api/v2/session/{sid}/files", headers=HA, timeout=30))
    results["A_own_artifact"] = code(httpx.get(f"{BASE}/api/v2/session/{sid}/artifacts/{art_id}", headers=HA, timeout=30))

    # ---- 越权：B 访问 A 的东西 ----
    b_checks = {
        "B_session_messages": httpx.get(f"{BASE}/api/v2/session/{sid}/messages", headers=HB, timeout=30),
        "B_session_files": httpx.get(f"{BASE}/api/v2/session/{sid}/files", headers=HB, timeout=30),
        "B_file_preview": httpx.get(f"{BASE}/api/v2/files/{file_id}/preview", headers=HB, timeout=30),
        "B_file_download": httpx.get(f"{BASE}/api/v2/files/{file_id}/download", headers=HB, timeout=30),
        "B_artifact": httpx.get(f"{BASE}/api/v2/session/{sid}/artifacts/{art_id}", headers=HB, timeout=30),
        "B_delete_file": httpx.delete(f"{BASE}/api/v2/files/{file_id}", headers=HB, timeout=30),
        "B_cancel_job": httpx.post(f"{BASE}/api/v2/chat/jobs/{job_id}/cancel", headers=HB,
                                   json={"session_id": sid}, timeout=30),
    }
    results["B_status"] = {k: v.status_code for k, v in b_checks.items()}

    # B 的文件库里不能出现 A 的文件
    lib_b = httpx.get(f"{BASE}/api/v2/files", headers=HB, timeout=30)
    lib_b_json = lib_b.json() if lib_b.status_code == 200 else {}
    ids_b = [f.get("file_id") for group in ("uploaded", "generated")
             for f in (lib_b_json.get(group) or [])]
    results["B_library_leaks_A_file"] = file_id in ids_b
    lib_a = httpx.get(f"{BASE}/api/v2/files", headers=HA, timeout=30).json()
    ids_a = [f.get("file_id") for group in ("uploaded", "generated")
             for f in (lib_a.get(group) or [])]
    results["A_library_has_file"] = file_id in ids_a

    # 副作用：A 的文件还在、A 的 job 没被取消
    still = session_files.get_file(sid, file_id, owner_user_id=uid_a)
    results["A_file_survives"] = bool(still)
    time.sleep(0.6)
    jrow = jobs_mod.get_job(job_id) if hasattr(jobs_mod, "get_job") else None
    results["A_job_status_after_B_cancel"] = (jrow or {}).get("status", "?")

    denied_ok = all(c in DENIED for c in results["B_status"].values())
    results["checks"] = {
        "A_control_access_ok": all(results[k] == 200 for k in
                                   ("A_own_messages", "A_own_files", "A_own_artifact")),
        "B_all_denied": denied_ok,
        "B_library_isolated": not results["B_library_leaks_A_file"],
        "A_file_survives_B_delete": results["A_file_survives"],
        "A_job_not_cancelled_by_B": results["A_job_status_after_B_cancel"] != "cancelled",
    }
    report = {"base": BASE, "user_a": name_a, "user_b": name_b, "session": sid,
              "file_id": file_id, "artifact_id": art_id, "job_id": job_id,
              "results": results}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "probe_security_crossuser.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(results["B_status"], ensure_ascii=False))
    print(json.dumps(results["checks"], ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())



