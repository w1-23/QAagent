# -*- coding: utf-8 -*-
"""S4 补充验收：把真实 xlsx（双 Sheet）与 HTML 报告放进本地库，用于预览渲染验收。

不做模型调用：文件通过产品自身的存储层 `src.session.files.register_file` 注册
（与聊天附件走同一条落库路径），预览仍由真实 HTTP 路由渲染。
"""
from __future__ import annotations

import io
import json
import os
import pathlib
import sys
from datetime import datetime

import httpx
from openpyxl import Workbook

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.session import files as session_files  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8010").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")


def make_xlsx() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "试验数据"
    ws.append(["处理", "重复", "落果率(%)", "单果重(g)"])
    for row in [("对照", 1, 12.5, 148.2), ("对照", 2, 13.1, 150.0),
                ("GA3", 1, 8.4, 161.3), ("GA3", 2, 9.0, 158.7)]:
        ws.append(list(row))
    ws2 = wb.create_sheet("离群检查")
    ws2.append(["行号", "字段", "数值", "判定"])
    ws2.append([5, "单果重(g)", 402.5, "疑似离群"])
    ws2.append([6, "落果率(%)", 55.0, "疑似离群"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>数据审查报告</title></head><body>
<h1>数据审查报告（探针）</h1>
<p>共检查 4 行 4 列，发现 2 处疑似离群值。</p>
<table border="1"><tr><th>行号</th><th>字段</th><th>数值</th><th>判定</th></tr>
<tr><td>5</td><td>单果重(g)</td><td>402.5</td><td>疑似离群</td></tr>
<tr><td>6</td><td>落果率(%)</td><td>55.0</td><td>疑似离群</td></tr></table>
</body></html>"""


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tok = os.environ["CITRUS_TOKEN"]
    H = {"Cookie": "citrus_auth=" + tok, "Origin": BASE}
    with httpx.Client(timeout=60, headers=H) as c:
        r = c.post(f"{BASE}/api/v1/session/new")
        if r.status_code != 200:
            print("session/new failed", r.status_code, r.text[:200])
            return 2
        sid = r.json()["session_id"]
    rec_x = session_files.register_file(
        session_id=sid, owner_user_id=1, filename="试验数据_双表.xlsx",
        content=make_xlsx(), mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        origin=session_files.ORIGIN_UPLOADED, client_request_id="crid-preview-xlsx")
    rec_h = session_files.register_file(
        session_id=sid, owner_user_id=1, filename="数据审查报告.html",
        content=HTML.encode("utf-8"), mime="text/html",
        origin=session_files.ORIGIN_UPLOADED, client_request_id="crid-preview-html")
    out = {"session_id": sid, "xlsx": rec_x["file_id"], "html": rec_h["file_id"],
           "xlsx_preview": f"{BASE}/api/v2/files/{rec_x['file_id']}/preview",
           "html_preview": f"{BASE}/api/v2/files/{rec_h['file_id']}/preview"}
    (OUT / "preview_fixtures.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


