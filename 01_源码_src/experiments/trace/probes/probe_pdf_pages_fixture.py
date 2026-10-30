# -*- coding: utf-8 -*-
"""给 PDF 预览补一份 3 页样本，验证内置查看器能翻页/缩放（不只 1 页）。"""
from __future__ import annotations
import json, os, pathlib, sys
import httpx
ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.session import files as session_files  # noqa: E402

BASE = os.environ.get("CITRUS_BASE", "http://127.0.0.1:8010").rstrip("/")
OUT = pathlib.Path(__file__).resolve().parents[1] / "runs" / "20261030"

def three_page_pdf() -> bytes:
    pages, objs = [], []
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{4 + i * 2} 0 R" for i in range(3))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count 3 >>")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    for i in range(3):
        content = f"BT /F1 18 Tf 40 700 Td (CitrusQA PDF page {i + 1} of 3) Tj ET"
        objs.append("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
                    "/Resources << /Font << /F1 3 0 R >> >> "
                    f"/Contents {5 + i * 2} 0 R >>")
        objs.append(f"<< /Length {len(content)} >>\nstream\n{content}\nendstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for idx, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{idx} 0 obj\n{body}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n").encode()
    return bytes(out)

def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    tok = os.environ["CITRUS_TOKEN"]
    H = {"Cookie": "citrus_auth=" + tok, "Origin": BASE}
    with httpx.Client(timeout=60, headers=H) as c:
        sid = c.post(f"{BASE}/api/v1/session/new").json()["session_id"]
    rec = session_files.register_file(
        session_id=sid, owner_user_id=1, filename="三页预览样本.pdf",
        content=three_page_pdf(), mime="application/pdf",
        origin=session_files.ORIGIN_UPLOADED, client_request_id="crid-preview-pdf3")
    data = json.loads((OUT / "preview_fixtures.json").read_text(encoding="utf-8"))
    data["pdf3"] = rec["file_id"]
    data["pdf3_preview"] = f"{BASE}/api/v2/files/{rec['file_id']}/preview"
    (OUT / "preview_fixtures.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"pdf3": rec["file_id"], "bytes": len(three_page_pdf())}, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
