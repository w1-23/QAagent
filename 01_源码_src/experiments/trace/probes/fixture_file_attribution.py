"""P0-1 回归夹具：造一个 6 轮会话，文件分布在不同轮次。

用途：验证「生成文件留在产生它的那一轮助手气泡里」，刷新/切会话/加载更早历史都不串位。

固定夹具（可重复执行，先清后建）：
- session_id = p01-attr-fixture
- 第 1 轮：用户上传 donor_A.xlsx      → 用户气泡 1 张上传卡
- 第 2 轮：助手生成 donor_A_标注.xlsx → 助手气泡 1 张生成卡
- 第 3 轮：助手生成 report.html       → 助手气泡 1 张生成卡
- 第 4 轮：助手生成 donor_B_标注.xlsx → 助手气泡 1 张生成卡
- 第 5 轮：普通文字问答               → 无文件
- 第 6 轮：用户上传 donC.xlsx         → 用户气泡 1 张上传卡

用法：
    python experiments/trace/probes/fixture_file_attribution.py make
    python experiments/trace/probes/fixture_file_attribution.py check   # 打印库内归属
    python experiments/trace/probes/fixture_file_attribution.py drop
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]          # 01_源码_src
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))       # 允许直接 `python experiments/.../fixture*.py`
STATE = ROOT.parent / "agent" / "state"
SESSIONS_DB = STATE / "sessions.db"
FILES_DB = STATE / "session_files.sqlite3"

SESSION_ID = "p01-attr-fixture"
OWNER = 1

# (轮次, 问句, 回答, 文件名, 来源, 大小)
TURNS = [
    (1, "这是我上次的实验数据，先看一下结构", "已读取工作簿结构：两个工作表，含 rel_expr 与表达量。",
     "donor_A.xlsx", "uploaded", 18_432),
    (2, "帮我审查 donor_A 这份表达量数据", "已完成审查：B3、B5、B9 的 rel_expr 疑似离群，详情见标注文件。",
     "donor_A_标注.xlsx", "generated", 21_507),
    (3, "把审查结论整理成一页网页报告", "报告已生成：包含问题清单、分组口径与建议核查动作。",
     "review_report.html", "generated", 9_216),
    (4, "用同样的口径审查 donor_B", "donor_B 审查完成：B5、B7 疑似离群，已标注在对应单元格。",
     "donor_B_标注.xlsx", "generated", 20_991),
    (5, "两个供体的差异该怎么解释", "建议先确认取样时期与组织部位一致，再比较相对表达量。",
     "", "", 0),
    (6, "再传一份 donC 的数据备用", "已收到 donC 工作簿，暂未审查。",
     "donC.xlsx", "uploaded", 17_004),
]


def rid(turn: int) -> str:
    return f"crid-p01fixture-{turn:02d}"


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def drop() -> None:
    with _connect(SESSIONS_DB) as conn:
        conn.execute("DELETE FROM messages WHERE session_id=?", (SESSION_ID,))
        conn.execute("DELETE FROM sessions WHERE session_id=?", (SESSION_ID,))
    with _connect(FILES_DB) as conn:
        ids = [r["file_id"] for r in conn.execute(
            "SELECT file_id FROM session_files WHERE session_id=?", (SESSION_ID,))]
        conn.execute("DELETE FROM session_file_turn_refs WHERE session_id=?", (SESSION_ID,))
        conn.execute("DELETE FROM session_files WHERE session_id=?", (SESSION_ID,))
        try:
            conn.execute("DELETE FROM session_file_owners WHERE session_id=?", (SESSION_ID,))
        except sqlite3.Error:
            pass
    print(f"dropped {SESSION_ID} ({len(ids)} files)")


def make() -> None:
    drop()
    now = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
    with _connect(SESSIONS_DB) as conn:
        conn.execute(
            "INSERT INTO sessions (session_id, created_at, updated_at, checkpoint_msg_id,"
            " checkpoint_summary, checkpoint_updated_at, title, deleted_at, owner_user_id)"
            " VALUES (?,?,?,0,'','',?,NULL,?)",
            (SESSION_ID, stamp, stamp, "P0-1 文件归属夹具（6 轮）", OWNER))
        for turn, q, a, _name, _origin, _size in TURNS:
            conn.execute(
                "INSERT INTO messages (session_id, msg_type, content, timestamp,"
                " client_request_id, idempotency_key) VALUES (?,?,?,?,?,?)",
                (SESSION_ID, "human", q, stamp, rid(turn), rid(turn)))
            conn.execute(
                "INSERT INTO messages (session_id, msg_type, content, timestamp,"
                " client_request_id, idempotency_key) VALUES (?,?,?,?,?,?)",
                (SESSION_ID, "ai", a, stamp, rid(turn), rid(turn) + ":ai"))

    # Real bytes inside the managed session area: the API refuses rows whose
    # storage path does not exist or escapes the managed root.
    from src.session import files as sf
    with _connect(FILES_DB) as conn:
        for turn, _q, _a, name, origin, size in TURNS:
            if not name:
                continue
            # file_id 必须是 32 位十六进制（get_file 只接受 uuid4().hex 形态），
            # 否则 /api/v2/files/{id}/download|preview 一律 404。
            file_id = hashlib.md5(f"p01-{turn}-{name}".encode("utf-8")).hexdigest()
            if origin == "uploaded":
                root = sf._session_dir(SESSION_ID, OWNER)
            else:
                root = sf._artifact_session_dir(SESSION_ID)
            root.mkdir(parents=True, exist_ok=True)
            path = (root / f"{file_id}").resolve()
            path.write_bytes((f"fixture:{turn}:{name}".encode("utf-8") * 32)[:size])
            size = path.stat().st_size
            conn.execute(
                "INSERT OR REPLACE INTO session_files (file_id, session_id, owner_user_id,"
                " filename, origin, mime_type, size_bytes, sha256, storage_path, artifact_id,"
                " created_at, last_accessed_at, client_request_id, producer_tool)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (file_id, SESSION_ID, OWNER, name, origin, "application/octet-stream",
                 size, f"sha-{file_id}"[:64], str(path), "",
                 now, now, rid(turn), "audit_uploaded_data" if origin == "generated" else ""))
            try:
                conn.execute(
                    "INSERT OR REPLACE INTO session_file_turn_refs"
                    " (session_id, owner_user_id, client_request_id, file_id, created_at)"
                    " VALUES (?,?,?,?,?)",
                    (SESSION_ID, OWNER, rid(turn), file_id, now))
            except sqlite3.Error as exc:
                print("turn_refs insert skipped:", exc)
    print(f"fixture ready: {SESSION_ID} ({len(TURNS)} turns)")


def check() -> None:
    print("== sessions.db messages ==")
    with _connect(SESSIONS_DB) as conn:
        rows = conn.execute(
            "SELECT id, msg_type, client_request_id, substr(content,1,24) AS c"
            " FROM messages WHERE session_id=? ORDER BY id", (SESSION_ID,)).fetchall()
    for r in rows:
        print(f"  #{r['id']} {r['msg_type']:5s} req={r['client_request_id']} {r['c']}")
    print("== session_files ==")
    with _connect(FILES_DB) as conn:
        rows = conn.execute(
            "SELECT r.client_request_id AS turn, f.filename, f.origin"
            " FROM session_file_turn_refs r JOIN session_files f ON f.file_id=r.file_id"
            " WHERE r.session_id=? ORDER BY r.client_request_id, f.filename",
            (SESSION_ID,)).fetchall()
    for r in rows:
        print(f"  {r['turn']} {r['origin']:9s} {r['filename']}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "make"
    {"make": make, "drop": drop, "check": check}.get(cmd, make)()
