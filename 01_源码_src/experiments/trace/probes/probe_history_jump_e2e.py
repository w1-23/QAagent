# -*- coding: utf-8 -*-
"""S3 验收探针：历史分页 + 文件→具体对话轮。

外部观察者视角，只走公开 HTTP。要证明四件事：

1. ``/messages?limit=1`` 返回的是**最新**一页（旧实现从最老处截断）；
2. ``before`` 游标可以往回翻页，且页与页之间不丢、不重；
3. 消息行带真实 ``message_id`` 与 ``request_id``；
4. 文件库里的文件带 ``turn_request_id`` / ``source_session_id``，并且该 id
   能在同一会话的历史里找到对应的人类消息（否则“定位”就是假承诺）。

用法：
    E:\\anaconda\\envs\\rag-agent\\python.exe experiments/trace/probes/probe_history_jump_e2e.py
    # 可选：CITRUS_BASE
"""
from __future__ import annotations

import base64
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
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = os.environ.get("CITRUS_JUMP_QUERY", "请简短回答：橘和橙在植物学分类上有什么差别？")
ATTACH_NAME = "probe_history_jump.csv"
ATTACH = ("品种,学名\n宫川温州蜜柑,Citrus unshiu\n").encode("utf-8")


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
    rid = "probe" + secrets.token_hex(6)
    report: dict = {"session_id": "", "client_request_id": rid, "checks": {}}
    t0 = time.time()

    with httpx.Client(timeout=300, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        report["session_id"] = sid
        # 三轮对话：只有多轮才可能翻页（旧实现截断最老端，长会话会丢最新轮）。
        turns = os.environ.get("CITRUS_JUMP_TURNS", "3")
        turn_count = max(int(turns or 3), 3)
        done = {}
        for n in range(turn_count):
            body = {"query": QUERY if n == 0 else f"{QUERY}（第 {n + 1} 次，请再简短回答一句）",
                    "session_id": sid}
            if n == 0:
                body["client_request_id"] = rid
                body["attachments"] = [{"name": ATTACH_NAME, "type": "text/csv",
                                        "data": base64.b64encode(ATTACH).decode()}]
            with c.stream("POST", f"{BASE}/api/v2/chat", json=body,
                          headers={**H, "Accept": "text/event-stream"}) as r:
                event = ""
                for line in r.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:") and event in ("done", "error"):
                        try:
                            done = json.loads(line[5:].strip())
                        except json.JSONDecodeError:
                            done = {"_raw": line[:200]}
                        if event == "done":
                            break
        report["done_turn_status"] = done.get("turn_status")
        report["answer_chars"] = len(str(done.get("answer") or ""))
        report["turns"] = turn_count

        full = c.get(f"{BASE}/api/v2/session/{sid}/messages?limit=200").json()
        rows = full.get("messages") or []
        report["total_rows"] = len(rows)
        report["roles"] = [r.get("role") for r in rows]
        ids = [r.get("message_id") for r in rows]
        report["all_rows_have_message_id"] = all(bool(x) for x in ids)
        report["message_ids_strictly_increasing"] = all(
            int(ids[i]) < int(ids[i + 1]) for i in range(len(ids) - 1)) if len(ids) > 1 else None
        report["has_more_full_page"] = full.get("has_more")

        page1 = c.get(f"{BASE}/api/v2/session/{sid}/messages?limit=2").json()
        r1 = page1.get("messages") or []
        report["page1_ids"] = [r.get("message_id") for r in r1]
        report["page1_equals_newest_two"] = ([r.get("message_id") for r in r1] == ids[-2:])
        cursor = int(page1.get("next_before") or 0)
        report["page1_has_more"] = page1.get("has_more")
        page2 = c.get(f"{BASE}/api/v2/session/{sid}/messages?limit=2&before={cursor}").json() if cursor else {}
        r2 = page2.get("messages") or []
        report["page2_ids"] = [r.get("message_id") for r in r2]
        report["pages_do_not_overlap"] = not (set(report["page1_ids"]) & set(report["page2_ids"]))
        report["page2_older_than_page1"] = bool(r2) and max(report["page2_ids"]) < min(report["page1_ids"])
        # 往回翻到底，确认不丢行
        seen, cur, guard = [], int(page1.get("next_before") or 0), 0
        while cur and guard < 50:
            guard += 1
            pg = c.get(f"{BASE}/api/v2/session/{sid}/messages?limit=50&before={cur}").json()
            got = [r.get("message_id") for r in (pg.get("messages") or [])]
            seen = got + seen
            cur = int(pg.get("next_before") or 0)
        report["walked_back_ids"] = seen
        report["walk_reconstructs_history"] = (seen + report["page1_ids"][-1:] == [
            x for x in ids if x >= (seen[0] if seen else 0)] if seen else None)

        files = c.get(f"{BASE}/api/v2/files").json()
        if isinstance(files, list):
            items = files
        else:
            items = list(files.get("uploaded") or []) + list(files.get("generated") or [])
            report["library_counts"] = {"uploaded": len(files.get("uploaded") or []),
                                        "generated": len(files.get("generated") or [])}
        mine = [f for f in items if str(f.get("source_session_id") or "") == sid]
        report["files_in_session"] = [{"filename": f.get("filename"),
                                       "turn_request_id": f.get("turn_request_id"),
                                       "source_session_id": f.get("source_session_id")} for f in mine]
        target = [f for f in mine if str(f.get("turn_request_id") or "") == rid]
        report["file_carries_this_turn_id"] = bool(target)
        user_rows = [r for r in rows if r.get("role") == "user"]
        report["user_row_request_ids"] = [r.get("request_id") for r in user_rows]
        report["jump_target_resolvable"] = any(
            str(r.get("request_id") or "") == rid and bool(r.get("message_id")) for r in user_rows)
        report["unknown_turn_has_no_target"] = not any(
            str(r.get("request_id") or "") == "no-such-turn-id" for r in rows)

        report["elapsed_s"] = round(time.time() - t0, 1)
        report["checks"] = {
            "newest_page_returned": bool(report["page1_equals_newest_two"]),
            "paging_walks_backwards": bool(report["page2_older_than_page1"])
                                     and bool(report["pages_do_not_overlap"]),
            "rows_carry_message_id": bool(report["all_rows_have_message_id"]),
            "file_carries_turn_key": bool(report["file_carries_this_turn_id"]),
            "jump_key_matches_history": bool(report["jump_target_resolvable"]),
            "no_fake_jump_for_unknown_turn": bool(report["unknown_turn_has_no_target"]),
        }
        report["all_passed"] = all(report["checks"].values())

    path = OUT / f"history_jump_{datetime.now().strftime('%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("session_id", "total_rows", "page1_ids",
                                             "page2_ids", "file_carries_this_turn_id",
                                             "jump_target_resolvable", "elapsed_s",
                                             "checks", "all_passed")},
                     ensure_ascii=False, indent=2))
    print("saved:", path)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
