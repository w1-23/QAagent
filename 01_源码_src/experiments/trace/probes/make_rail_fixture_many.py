# -*- coding: utf-8 -*-
"""D-A3 验收夹具：造一个 60 轮的临时会话，用来在真实浏览器里验证
「导航条全量 + 可滚动」。

只写本地开发库（`agent/state/sessions.db`），不触碰生产。用法：
    python experiments/trace/probes/make_rail_fixture_many.py [轮数]
输出：`<session_id> <消息条数>`，供 CDP 脚本定位。
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "agent", "state", "sessions.db"))

TOPICS = [
    "柑橘黄龙病在亚洲的传播媒介有哪些研究？",
    "把上面几篇文献的 DOI 和年份整理成表格。",
    "我上传的试验数据里产量列有离群值吗？",
    "按品种分组再算一次平均值。",
    "把结果导出成 Excel，保留原始列名。",
    "用一段话总结这次分析的主要结论。",
    "帮我查一下柑橘木虱的防治措施。",
    "这些措施里哪些有田间试验证据？",
    "把证据等级标出来，分成强/中/弱。",
    "再看看有没有抗病品种的相关资料。",
]


def main() -> int:
    rounds = int(sys.argv[1]) if len(sys.argv) > 1 else 60
    if not os.path.exists(DB):
        raise SystemExit("sessions.db not found at " + DB)
    sid = "rail-many-" + uuid.uuid4().hex[:8]
    now = time.time()
    conn = sqlite3.connect(DB)
    conn.execute(
        "insert into sessions(session_id, created_at, updated_at, title, owner_user_id)"
        " values(?,?,?,?,?)", (sid, now, now, "导航条全量验收（临时）", 1))
    for i in range(rounds):
        q = TOPICS[i % len(TOPICS)] + f"（第 {i + 1} 轮）"
        ts = now + i * 2
        conn.execute(
            "insert into messages(session_id, msg_type, content, timestamp,"
            " client_request_id) values(?,?,?,?,?)",
            (sid, "human", "<user_query>" + q + "</user_query>", ts,
             "rail-" + uuid.uuid4().hex[:8]))
        conn.execute(
            "insert into messages(session_id, msg_type, content, timestamp,"
            " client_request_id) values(?,?,?,?,?)",
            (sid, "ai", f"这是第 {i + 1} 轮的回答（夹具，无模型调用）。", ts + 1, None))
    conn.commit()
    n = conn.execute("select count(*) from messages where session_id=?",
                     (sid,)).fetchone()[0]
    conn.close()
    print(sid, n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
