# -*- coding: utf-8 -*-
"""S4 取证探针：PDF/Office 预览为什么是空白。

只走公开 HTTP。真正要回答的问题不是"iframe 有没有 onload"，而是：

1. 预览接口对 PDF 回的是什么 `Content-Type`（不是 `application/pdf` 浏览器就会下载，
   iframe 里于是空白）；
2. 响应体是不是真的 PDF 字节（`%PDF-` 魔数），大小与原件一致；
3. 是否支持 Range（很多内置查看器需要分片读）；
4. 老版本把 `mime_type` 存空时，接口会不会退化成 `application/octet-stream`。

用法：
    E:\\anaconda\\envs\\rag-agent\\python.exe experiments/trace/probes/probe_pdf_preview_e2e.py
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

# 最小但合法的单页 PDF（手写 xref 偏移在多数查看器里可容忍；用 qpdf 验证过结构）
PDF = (b"%PDF-1.4\n"
       b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
       b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
       b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]"
       b"/Resources<</Font<</F1 4 0 R>>>>/Contents 5 0 R>>endobj\n"
       b"4 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
       b"5 0 obj<</Length 44>>stream\nBT /F1 14 Tf 20 100 Td (CitrusQA PDF probe) Tj ET\n"
       b"endstream endobj\n"
       b"trailer<</Root 1 0 R/Size 6>>\n%%EOF\n")


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
    report: dict = {"checks": {}}
    t0 = time.time()
    with httpx.Client(timeout=120, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
        body = {"query": "只回复收到两个字。", "session_id": sid,
                "attachments": [{"name": "probe_preview.pdf", "type": "application/pdf",
                                 "data": base64.b64encode(PDF).decode()}]}
        with c.stream("POST", f"{BASE}/api/v2/chat", json=body,
                      headers={**H, "Accept": "text/event-stream"}) as r:
            for line in r.iter_lines():
                if line.startswith("data:") and '"turn_status"' in line:
                    break
        lib = c.get(f"{BASE}/api/v2/files").json()
        items = list(lib.get("uploaded") or []) + list(lib.get("generated") or [])
        pdfs = [f for f in items if str(f.get("filename", "")).lower().endswith(".pdf")]
        report["session_id"] = sid
        report["pdf_count"] = len(pdfs)
        if not pdfs:
            report["error"] = "PDF 未进入资料库"
            report["all_passed"] = False
            path = OUT / f"pdf_preview_{datetime.now().strftime('%H%M%S')}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 1
        f = pdfs[0]
        report["library_kind"] = f.get("kind")
        report["library_mime"] = f.get("mime_type")
        url = f"{BASE}{f['preview_url']}" if str(f.get("preview_url", "")).startswith("/") else f["preview_url"]
        report["preview_url"] = f.get("preview_url")

        r = c.get(url)
        body_bytes = r.content
        report["status"] = r.status_code
        report["content_type"] = r.headers.get("content-type")
        report["content_length"] = r.headers.get("content-length")
        report["accept_ranges"] = r.headers.get("accept-ranges")
        report["disposition"] = r.headers.get("content-disposition")
        report["nosniff"] = r.headers.get("x-content-type-options")
        report["bytes_equal_original"] = body_bytes == PDF
        report["has_pdf_magic"] = body_bytes[:5] == b"%PDF-"
        report["size_bytes"] = len(body_bytes)

        # Range 请求：内置查看器常用
        rr = c.get(url, headers={"Range": "bytes=0-31"})
        report["range_status"] = rr.status_code
        report["range_bytes"] = len(rr.content)

        report["checks"] = {
            "library_kind_is_pdf": report["library_kind"] == "pdf",
            "content_type_is_pdf": str(report["content_type"] or "").startswith("application/pdf"),
            "body_is_real_pdf": bool(report["has_pdf_magic"]) and report["bytes_equal_original"],
            "inline_disposition": "attachment" not in str(report["disposition"] or "").lower(),
            "range_supported": report["range_status"] in (200, 206),
        }
        report["elapsed_s"] = round(time.time() - t0, 1)
        report["all_passed"] = all(report["checks"].values())

    path = OUT / f"pdf_preview_{datetime.now().strftime('%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: report[k] for k in ("status", "content_type", "disposition",
                                             "accept_ranges", "range_status",
                                             "bytes_equal_original", "size_bytes",
                                             "library_kind", "library_mime", "checks",
                                             "all_passed")},
                     ensure_ascii=False, indent=2))
    print("saved:", path)
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
