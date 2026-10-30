"""Create a throwaway local session with 6 real user turns, so the right-edge
turn rail can be measured in a real browser. Local dev DB only."""
import sqlite3, time, uuid, os

# probes -> trace -> experiments -> 01_源码_src -> Citrus_QA_Agent
DB = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "agent", "state", "sessions.db")
DB = os.path.abspath(DB)
if not os.path.exists(DB):
    raise SystemExit("sessions.db not found at " + DB)

Q = [
    "帮我查一下柑橘黄龙病在亚洲的传播媒介研究，列出参考来源。",
    "把上面几篇的 DOI 和年份整理成表格。",
    "我上传的试验数据里，产量这一列有没有离群值？",
    "按品种分组再算一次平均值。",
    "把结果导出成 Excel，保留原始列名。",
    "最后用一段话总结这次分析的主要结论。",
]
A = [
    "已检索本地语料与联网来源，共整理 8 条可核对资料，主要传播媒介为柑橘木虱（Diaphorina citri）。",
    "已整理 5 篇文献的 DOI 与年份，见下表。",
    "已审查 120 行数据，产量列发现 3 个离群值（>3σ）。",
    "已按 4 个品种分组计算平均值，见结果表。",
    "已生成 Excel 文件，保留原始列名并新增汇总页。",
    "本次分析确认产量存在 3 个离群样本，品种间均值差异明显。",
]


def main():
    sid = "occ-rail-test-" + uuid.uuid4().hex[:8]
    now = time.time()
    c = sqlite3.connect(DB)
    c.execute(
        "insert into sessions(session_id, created_at, updated_at, title, owner_user_id) values(?,?,?,?,?)",
        (sid, now, now, "导航条遮挡验收（临时）", 1),
    )
    for i, (q, a) in enumerate(zip(Q, A)):
        ts = now + i * 2
        c.execute(
            "insert into messages(session_id, msg_type, content, timestamp, client_request_id) values(?,?,?,?,?)",
            (sid, "human", "<user_query>" + q + "</user_query>", ts, "occ-" + uuid.uuid4().hex[:8]),
        )
        c.execute(
            "insert into messages(session_id, msg_type, content, timestamp, client_request_id) values(?,?,?,?,?)",
            (sid, "ai", a, ts + 1, None),
        )
    c.commit()
    n = c.execute("select count(*) from messages where session_id=?", (sid,)).fetchone()[0]
    c.close()
    print(sid, n)


if __name__ == "__main__":
    main()
