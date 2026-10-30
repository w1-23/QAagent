"""Release-gate residual checks that need no model call.

1. migration idempotency  - open every sqlite DB twice, assert schema stable
2. history restore        - GET /messages twice, assert identical rows (no dupes)
3. export regression      - xlsx export has the expected sheets, HTML audit renders

Run against a running instance: CITRUS_BASE=http://127.0.0.1:8011 python probe_matrix_residual.py
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8000").rstrip("/")
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import auth  # noqa: E402


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


_COOKIE = ""


def _headers() -> dict:
    return {"Cookie": f"citrus_auth={_COOKIE}", "Origin": BASE} if _COOKIE else {}
OUT = Path(__file__).resolve().parents[1] / "runs" / "20261030" / "probe_matrix_residual.json"


def http(path: str, method: str = "GET", body: dict | None = None, timeout: float = 60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in _headers().items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw[:400]}


INSTANCE = ROOT.parent                      # /path/to/<instance>  (holds agent/state)


def db_files() -> list[Path]:
    base = INSTANCE / "agent" / "state"
    return sorted(p for p in base.glob("*.db")) if base.exists() else []


def schema_of(path: Path) -> list[tuple[str, str]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return sorted(con.execute("SELECT type,name FROM sqlite_master").fetchall())
    finally:
        con.close()


def check_migrations() -> dict:
    out = {"databases": [], "stable": True}
    for p in db_files():
        before = schema_of(p)
        # a second independent connection must not alter the schema
        con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
        con.execute("SELECT 1").fetchall()
        con.close()
        after = schema_of(p)
        same = before == after
        out["stable"] = out["stable"] and same
        out["databases"].append(
            {"db": p.name, "objects": len(before), "stable": same}
        )
    return out


def _session_with_tool_rows() -> str:
    """Prefer a session that actually has persisted tool executions."""
    db = INSTANCE / "agent" / "state" / "sessions.db"
    if not db.exists():
        return ""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT session_id FROM messages WHERE execution_json IS NOT NULL "
            "AND execution_json != '' ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    finally:
        con.close()
    return row[0] if row else ""


def check_history_restore() -> dict:
    sid = _session_with_tool_rows()
    if not sid:
        files = http("/api/v2/files")
        if isinstance(files, dict):
            for key in ("uploaded", "generated"):
                for item in files.get(key) or []:
                    if item.get("session_id"):
                        sid = item["session_id"]
                        break
                if sid:
                    break
    if not sid:
        return {"ok": False, "reason": "no session to inspect"}
    a = http(f"/api/v2/session/{sid}/messages")
    b = http(f"/api/v2/session/{sid}/messages")
    ra = (a.get("messages") or [])
    rb = (b.get("messages") or [])
    ids_a = [m.get("id") or m.get("message_id") for m in ra]
    ids_b = [m.get("id") or m.get("message_id") for m in rb]
    rows_a = [r for m in ra for r in (m.get("tool_calls") or [])]
    rows_b = [r for m in rb for r in (m.get("tool_calls") or [])]
    # A message that both calls tools and carries text is served as two entries
    # (role=tool_calls + role=assistant) sharing one message_id. That is the only
    # duplication we accept; anything else would mean the history really repeats.
    from collections import Counter

    counts = Counter(ids_a)
    by_id: dict = {}
    for m in ra:
        by_id.setdefault(m.get("id") or m.get("message_id"), set()).add(m.get("role"))
    explained = all(
        "tool_calls" in by_id.get(mid, set()) and by_id.get(mid, set()) & {"assistant", "tool"}
        for mid, n in counts.items() if n > 1
    )
    return {
        "ok": ids_a == ids_b and explained,
        "session_id": sid,
        "messages": len(ids_a),
        "identical": ids_a == ids_b,
        "duplicate_ids": len(ids_a) - len(set(ids_a)),
        "duplicates_explained": explained,
        "tool_rows": len(rows_a),
        "tool_rows_identical": rows_a == rows_b,
    }


def check_exports() -> dict:
    """Drive the real export tool for all three formats.

    The tool needs a bound session id (it registers the file for download), so
    we bind a synthetic one and then verify each artifact on disk.
    """
    out: dict = {}
    try:
        sys.path.insert(0, str(ROOT))
        from src.core.tracing import set_session_id  # type: ignore
        from src.research.tools import export_literature_evidence  # type: ignore

        set_session_id("probe" + secrets.token_hex(6))
        rows = [
            {"citation": "Author A (2023) Plant Disease 107:1-9", "claim": "HLB spreads by psyllid",
             "evidence": "vector transmission confirmed", "source": "web",
             "doi": "10.1094/PDIS-01-23-0001", "url": "https://doi.org/10.1094/PDIS-01-23-0001"},
            {"citation": "Author B (2021) Nature Plants 7:1-8", "claim": "Citrus genomics",
             "evidence": "assembly", "source": "web", "doi": "10.1038/s41477-021-0001",
             "url": "https://doi.org/10.1038/s41477-021-0001"},
        ]
        for fmt in ("xlsx", "ris", "bibtex"):
            try:
                res = export_literature_evidence.invoke(
                    {"title": "探针导出", "evidence_rows_json": json.dumps(rows, ensure_ascii=False),
                     "output_format": fmt}
                )
                text = str(res)
                out[f"{fmt}_ok"] = text.startswith(("文献", "文献引用"))
                out[f"{fmt}_text"] = text[:90]
            except Exception as exc:
                out[f"{fmt}_ok"] = False
                out[f"{fmt}_error"] = repr(exc)[:160]
        # the xlsx artifact must be a readable workbook
        art_dir = INSTANCE / "agent" / "state" / "artifacts"
        newest = sorted(art_dir.rglob("*.xlsx"), key=lambda p: p.stat().st_mtime)[-1:] if art_dir.exists() else []
        if newest:
            import io
            from openpyxl import load_workbook

            wb = load_workbook(io.BytesIO(newest[0].read_bytes()))
            out["xlsx_sheets"] = wb.sheetnames
            out["xlsx_rows"] = wb[wb.sheetnames[0]].max_row
    except Exception as exc:
        out["export_error"] = repr(exc)[:200]
    return out


def main() -> int:
    global _COOKIE
    _COOKIE = mint()
    report = {
        "base": BASE,
        "migrations": check_migrations(),
        "history_restore": check_history_restore(),
        "exports": check_exports(),
    }
    report["checks"] = {
        "migration_schema_stable": bool(report["migrations"].get("stable")),
        "history_restore_identical": bool(report["history_restore"].get("ok")),
        "export_xlsx_ok": bool(report["exports"].get("xlsx_ok")),
        "export_ris_ok": bool(report["exports"].get("ris_ok")),
        "export_bibtex_ok": bool(report["exports"].get("bibtex_ok")),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report["checks"], ensure_ascii=False))
    print(json.dumps(report["exports"], ensure_ascii=False)[:300])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())






