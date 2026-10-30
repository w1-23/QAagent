"""Round 9 验收夹具：往合成会话里插入/删除真实 human 消息（不调用模型）。

用法：
  python experiments/trace/probes/rail_fixture_messages.py add <session_id> <n> <tag>
  python experiments/trace/probes/rail_fixture_messages.py drop <session_id> <tag>
"""
from __future__ import annotations

import sqlite3
import sys
import time
from pathlib import Path

DB = Path(__file__).resolve().parents[4] / "agent" / "state" / "sessions.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def add(session_id: str, n: int, tag: str) -> None:
    base = int(time.time() * 1000)
    with _conn() as conn:
        for i in range(n):
            conn.execute(
                "INSERT INTO messages (session_id, msg_type, content, timestamp, client_request_id)"
                " VALUES (?, 'human', ?, ?, ?)",
                (session_id, f"{tag}-{i + 1} 这条是合成问题，用于验证导航刻度", base + i, f"{tag}-crid-{i + 1}"),
            )
        conn.commit()
        rows = conn.execute(
            "SELECT id, content FROM messages WHERE session_id=? AND client_request_id LIKE ? ORDER BY id",
            (session_id, f"{tag}-crid-%"),
        ).fetchall()
    for r in rows:
        print(f"INSERTED {r['id']} {r['content'][:20]}")


def drop(session_id: str, tag: str) -> None:
    with _conn() as conn:
        cur = conn.execute(
            "DELETE FROM messages WHERE session_id=? AND client_request_id LIKE ?",
            (session_id, f"{tag}-crid-%"),
        )
        conn.commit()
        print(f"DELETED {cur.rowcount}")


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    cmd = sys.argv[1]
    if cmd == "add":
        add(sys.argv[2], int(sys.argv[3]), sys.argv[4])
    elif cmd == "drop":
        drop(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
