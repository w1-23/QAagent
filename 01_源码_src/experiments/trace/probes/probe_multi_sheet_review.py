import io, json, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import httpx
from openpyxl import Workbook, load_workbook
from src.core.data_audit_xlsx import annotate_workbook_multi
import src.session.files as session_files

BASE = "http://127.0.0.1:8010"


def build_source() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "产量"
    ws.append(["品种", "单株产量", "糖度"])
    ws.append(["纽荷尔", 42, 11.5])
    ws.append(["纽荷尔", 42, 11.5])
    ws.append(["国庆一号", "", 15])
    wb2 = wb.create_sheet("品质")
    wb2.append(["品种", "可滴定酸"])
    wb2.append(["纽荷尔", 0.8])
    wb2.append(["纽荷尔", 0.8])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def main() -> int:
    tok = os.environ["CITRUS_TOKEN"]
    H = {"Cookie": "citrus_auth=" + tok, "Origin": BASE}
    with httpx.Client(timeout=60, headers=H) as c:
        r = c.post(f"{BASE}/api/v1/session/new")
        r.raise_for_status()
        sid = r.json()["session_id"]
    content, stats = annotate_workbook_multi(build_source(), targets=[
        {"sheet_name": "产量", "header_row": 1, "row_count": 3, "column_count": 3, "findings": [
            {"check": "duplicate_row", "row": 3, "detail": "与第 2 行完全相同"},
            {"check": "empty_value", "row": 4, "field": "单株产量", "detail": "数值为空"}]},
        {"sheet_name": "品质", "header_row": 1, "row_count": 2, "column_count": 2, "findings": [
            {"check": "duplicate_row", "row": 3, "detail": "与第 2 行完全相同"}]},
    ])
    rec = session_files.register_file(
        session_id=sid, owner_user_id=1, filename="试验数据_数据审查.xlsx", content=content,
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        origin=session_files.ORIGIN_GENERATED, client_request_id="crid-multi-review")
    book = load_workbook(io.BytesIO(content))
    out = {"session_id": sid, "file_id": rec["file_id"], "stats": stats["issues"],
           "sheets": book.sheetnames, "单元格标注": stats["cells"]}
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    raise SystemExit(main())