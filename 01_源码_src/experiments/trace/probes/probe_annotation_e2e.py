"""标注 Excel 端到端验收（真实模型 + 真实 HTTP）。

流程：造一张有明显可疑值的小表 → 带附件发起真实对话 → 模型调用 audit_uploaded_data
→ 从会话文件里取回“*_标注.xlsx” → 断言结构与标注内容 → 记录证据。

用法：python experiments/trace/probes/probe_annotation_e2e.py
环境变量：CITRUS_BASE（默认 http://127.0.0.1:8010）
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import pathlib
import secrets
import sys
import time
from datetime import datetime, timedelta, timezone

import httpx
from openpyxl import Workbook, load_workbook

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src import auth  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8010").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / datetime.now().strftime("%Y%m%d")
QUERY = ("请逐行审查我上传的这张盆栽试验数据表，用 audit_uploaded_data 生成标注 Excel："
         "对需要核对的行写明结论和理由（行号按原文件，第一行数据是第 2 行）。"
         "重点看对照组的产量是否异常。不要改动我的原表。")


def mint() -> str:
    token = secrets.token_urlsafe(36)
    stamp = auth._stamp()
    expires = auth._stamp(datetime.now(timezone.utc) + timedelta(seconds=3600))
    with auth._connect() as c:
        c.execute("INSERT INTO browser_sessions VALUES(?,?,?,?)",
                  (hashlib.sha256(token.encode()).hexdigest(), 1, stamp, expires))
    return token


def build_table() -> bytes:
    book = Workbook()
    sheet = book.active
    sheet.title = "盆栽试验"
    sheet.append(["处理", "重复", "产量(kg/株)", "备注"])
    rows = [
        ["对照", 1, 41.2, ""],
        ["对照", 2, 41.5, ""],
        ["对照", 3, 141.5, "疑似录入多打一个 1"],
        ["低氮", 1, 45.8, ""],
        ["低氮", 2, 46.1, ""],
        ["低氮", 3, 45.5, ""],
    ]
    for row in rows:
        sheet.append(row)
    sheet2 = book.create_sheet("说明")
    sheet2.append(["项目", "内容"])
    sheet2.append(["试验", "标注验收用合成表，数值为编造"])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    data = build_table()
    token = mint()
    headers = {"Cookie": f"citrus_auth={token}", "Origin": BASE}
    events: list[dict] = []
    t0 = time.time()
    with httpx.Client(timeout=240, headers=headers) as client:
        sid = client.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        print(f"[probe] session={sid} base={BASE}")
        body = {
            "query": QUERY,
            "session_id": sid,
            "attachments": [{
                "name": "盆栽试验_验收.xlsx",
                "type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "data": base64.b64encode(data).decode(),
            }],
        }
        with client.stream("POST", f"{BASE}/api/v2/chat", json=body,
                           headers={**headers, "Accept": "text/event-stream"}) as resp:
            event = ""
            for line in resp.iter_lines():
                if line.startswith("event:"):
                    event = line[6:].strip()
                    continue
                if not line.startswith("data:"):
                    continue
                try:
                    payload = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                events.append({"t": round(time.time() - t0, 2), "event": event, "data": payload})
                if event == "tool_call_start":
                    print(f"[probe] +{time.time()-t0:.1f}s tool {payload.get('display_name')}")
                if event in {"done", "error"}:
                    break
        time.sleep(2.0)
        files = client.get(f"{BASE}/api/v2/session/{sid}/files").json()
        items = (files.get("generated") or []) + (files.get("uploaded") or [])
        candidates = [f for f in items if "标注" in str(f.get("filename") or "")]
        print(f"[probe] generated={len(files.get('generated') or [])} annotated={len(candidates)}")
        report: dict = {"session": sid, "events": len(events),
                        "tools": sorted({e["data"].get("display_name") for e in events
                                         if e["event"] == "tool_call_start"}),
                        "files": [{"name": f.get("filename"), "id": f.get("file_id"),
                                   "producer": f.get("producer_tool")} for f in items]}
        if candidates:
            target = candidates[0]
            blob = client.get(f"{BASE}/api/v2/files/{target['file_id']}/download").content
            path = OUT / f"annotated_{int(time.time())}.xlsx"
            path.write_bytes(blob)
            book = load_workbook(io.BytesIO(blob))
            sheet = book[book.sheetnames[0]]
            header = [cell.value for cell in sheet[1]]
            annotated_rows = []
            base_cols = len(header) - 3
            for row in sheet.iter_rows(min_row=2, values_only=True):
                if row[base_cols] not in (None, ""):
                    annotated_rows.append({"row_values": list(row[:base_cols]),
                                           "verdict": row[base_cols],
                                           "reason": row[base_cols + 1],
                                           "field": row[base_cols + 2]})
            verdicts = [item["verdict"] for item in annotated_rows]
            report.update({
                "xlsx": str(path),
                "verdict_counts": {v: verdicts.count(v) for v in set(verdicts)},
                "bytes": len(blob),
                "sheets": book.sheetnames,
                "header": header,
                "annotated_rows": annotated_rows,
                "has_guide": "标注说明" in book.sheetnames,
            })
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        (OUT / f"probe_annotation_{int(time.time())}.json").write_text(
            json.dumps({"report": report, "events": events}, ensure_ascii=False, indent=2,
                       default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
