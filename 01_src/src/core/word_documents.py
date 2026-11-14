"""Transactional, account-scoped DOCX drafts. No model code or arbitrary paths.

SQLite commits the structured source, actual DOCX checkpoint and batch receipt
in one transaction. A killed writer leaves the preceding checkpoint intact.
Publication is injected by the adapter and is separate from draft mutations.
CITRUS_WORD_MAX_VERSIONS is a positive per-draft retained checkpoint cap (default
100, including creation). At capacity new mutations fail without pruning history
or source pins; inspection, publication and successful batch retries still work.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
import hashlib
import io
import json
import os
import re
import sqlite3
import time
import uuid
import zipfile
from pathlib import Path
from typing import Callable, Any

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

PROFILES = {"experiment_plan", "methods_comparison", "data_audit", "research_report", "general"}
MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_ID = re.compile(r"[A-Za-z0-9_-]{1,96}\Z")
_TYPES = {"heading", "paragraph", "list_item", "emphasis", "table", "image", "caption", "reference", "section_end", "document_end"}
_PLACEHOLDER = re.compile(r"(?:TODO|TBD|待补充|此处填写|占位符|placeholder)[.。…\s]*", re.I)


class DocumentError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        super().__init__(detail)


def _version_limit() -> int:
    try:
        limit = int(os.getenv("CITRUS_WORD_MAX_VERSIONS", "100"))
        if limit > 0:
            return limit
    except ValueError:
        pass
    raise DocumentError("invalid_configuration", "CITRUS_WORD_MAX_VERSIONS 必须是正整数；本批未写入。")


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _identifier(value, label):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise DocumentError("invalid_argument", f"{label} 须为 1–96 位字母、数字、下划线或短横线。")
    return value


def _text(value, label="text", maximum=16000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise DocumentError("invalid_argument", f"{label} 须为非空文字，最多 {maximum} 字符；本批未写入。")
    if any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise DocumentError("invalid_argument", f"{label} 含非法控制字符。")
    if _PLACEHOLDER.fullmatch(value.strip()):
        raise DocumentError("placeholder", "占位文字不能作为已完成正文。")
    return value


def _font(style, size, color="253044", bold=False):
    style.font.name = "Arial"
    style.font.size = Pt(size)
    style.font.bold = bold
    style.font.color.rgb = RGBColor.from_string(color)
    fonts = style.element.get_or_add_rPr().get_or_add_rFonts()
    fonts.set(qn("w:eastAsia"), "Microsoft YaHei")


def _runs(paragraph, block):
    if block.get("runs"):
        for item in block["runs"]:
            run = paragraph.add_run(item["text"])
            for key in ("bold", "italic", "subscript", "superscript"):
                if key in item:
                    setattr(run.font, key, item[key])
    else:
        # Convert common inline emphasis into editable runs, never literal stars.
        for part in re.split(r"(\*\*[^*]+\*\*)", block.get("text", "")):
            if part:
                run = paragraph.add_run(part[2:-2] if part.startswith("**") and part.endswith("**") else part)
                run.bold = block["type"] == "emphasis" or part.startswith("**")


def render_document(state: dict) -> bytes:
    doc = Document()
    page = doc.sections[0]
    page.page_width, page.page_height = Cm(21), Cm(29.7)
    page.top_margin = page.bottom_margin = page.left_margin = page.right_margin = Cm(2.2)
    normal = doc.styles["Normal"]
    _font(normal, 10.5)
    normal.paragraph_format.line_spacing = 1.25
    normal.paragraph_format.space_before = Pt(0)
    normal.paragraph_format.space_after = Pt(5)
    for name, size, before, after, color in [
        ("Title", 16, 0, 12, "2F62AB"), ("Heading 1", 14, 12, 6, "2F62AB"),
        ("Heading 2", 11.5, 9, 4, "2F62AB"), ("Heading 3", 10.5, 9, 4, "253044")]:
        style = doc.styles[name]
        _font(style, size, color, True)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
    _font(doc.styles["Caption"], 9, "253044")
    doc.add_paragraph(state["title"], "Title")
    for block in state["blocks"]:
        kind = block["type"]
        if kind in {"section_end", "document_end"}:
            continue
        if kind == "table":
            if block.get("caption"):
                doc.add_paragraph(block["caption"], "Caption")
            table = doc.add_table(rows=1, cols=len(block["columns"]))
            table.style = "Table Grid"
            for cell, value in zip(table.rows[0].cells, block["columns"]):
                cell.text = value
                shade = OxmlElement("w:shd")
                shade.set(qn("w:fill"), "EDF3FA")
                cell._tc.get_or_add_tcPr().append(shade)
                for run in cell.paragraphs[0].runs:
                    run.bold = True
            repeat = OxmlElement("w:tblHeader")
            table.rows[0]._tr.get_or_add_trPr().append(repeat)
            for values in block["rows"]:
                for cell, value in zip(table.add_row().cells, values):
                    cell.text = "" if value is None else str(value)
            for row in table.rows:
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        for run in paragraph.runs:
                            run.font.size = Pt(9.5)
            borders = OxmlElement("w:tblBorders")
            for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
                edge = OxmlElement("w:" + side)
                for key, value in (("val", "single"), ("sz", "4"), ("color", "BCCBDD")):
                    edge.set(qn("w:" + key), value)
                borders.append(edge)
            table._tbl.tblPr.append(borders)
        elif kind == "image":
            doc.add_picture(io.BytesIO(base64.b64decode(block["_image_data"])), width=Cm(block["width_cm"]))
            doc.add_paragraph(block["caption"], "Caption")
        else:
            style = (f"Heading {block.get('level', 1)}" if kind == "heading" else
                     "List Number" if kind == "list_item" and block.get("ordered") else
                     "List Bullet" if kind == "list_item" else
                     "Caption" if kind == "caption" else "Normal")
            paragraph = doc.add_paragraph(style=style)
            _runs(paragraph, block)
    out = io.BytesIO()
    doc.save(out)
    payload = out.getvalue()
    validate_docx(payload)
    return payload


def validate_docx(payload: bytes) -> dict:
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            if not {"[Content_Types].xml", "word/document.xml", "word/styles.xml"}.issubset(archive.namelist()):
                raise ValueError("missing OOXML members")
            if archive.testzip() is not None:
                raise ValueError("CRC check failed")
        document = Document(io.BytesIO(payload))
        return {"zip_valid": True, "reopened": True, "paragraphs": len(document.paragraphs),
                "tables": len(document.tables), "images": len(document.inline_shapes)}
    except Exception as exc:
        raise DocumentError("invalid_docx", "DOCX 无法完整重新打开。") from exc


class _DraftConnection(sqlite3.Connection):
    """Track only this transaction's uniquely named external pins for rollback."""
    pending_resource_ids: list[str]


def purge_account_drafts(db_path: Path, owner: int) -> None:
    """After account admission drains, release pins before deleting private drafts.

    Keep the inventory discoverable until all releases succeed so a failed purge
    can retry. Account file/artifact deletion must happen after this function.
    """
    if type(owner) is not int or owner <= 0:
        raise DocumentError("unauthorized", "需要有效账号。")
    if not Path(db_path).exists():
        return
    conn = sqlite3.connect(db_path, timeout=30)
    try:
        with conn:
            conn.execute("PRAGMA secure_delete=ON")
            conn.execute("BEGIN IMMEDIATE")
            if conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='word_version_sources'").fetchone():
                from src.session.files import release_resource_references
                resources = conn.execute(
                    "SELECT resource_id FROM word_version_sources WHERE id IN "
                    "(SELECT id FROM word_drafts WHERE owner=?)", (owner,)).fetchall()
                for (resource_id,) in resources:
                    release_resource_references(owner, "word_draft", resource_id)
                conn.execute("DELETE FROM word_version_sources WHERE id IN "
                             "(SELECT id FROM word_drafts WHERE owner=?)", (owner,))
            for table in ("word_versions", "word_batches"):
                conn.execute(f"DELETE FROM {table} WHERE id IN "
                             "(SELECT id FROM word_drafts WHERE owner=?)", (owner,))
            conn.execute("DELETE FROM word_drafts WHERE owner=?", (owner,))
    finally:
        conn.close()


class WordDocuments:
    def __init__(self, db_path: Path, *, owner: int, session: str,
                 image_reader: Callable[[str], bytes] | None = None):
        if type(owner) is not int or owner <= 0 or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", session):
            raise DocumentError("unauthorized", "需要有效登录账号和会话。")
        self.db_path, self.owner, self.session = Path(db_path), owner, session
        self.image_reader = image_reader

    @contextmanager
    def _connect(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30, factory=_DraftConnection)
        conn.pending_resource_ids = []
        conn.row_factory = sqlite3.Row
        conn.execute("CREATE TABLE IF NOT EXISTS word_drafts (id TEXT PRIMARY KEY, owner INTEGER NOT NULL, session TEXT NOT NULL, version INTEGER NOT NULL, state TEXT NOT NULL, created REAL NOT NULL)")
        conn.execute("CREATE TABLE IF NOT EXISTS word_batches (id TEXT NOT NULL, batch TEXT NOT NULL, digest TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(id,batch))")
        conn.execute("CREATE TABLE IF NOT EXISTS word_versions (id TEXT NOT NULL, version INTEGER NOT NULL, payload BLOB NOT NULL, publication TEXT, PRIMARY KEY(id,version))")
        conn.execute("CREATE TABLE IF NOT EXISTS word_version_sources (id TEXT NOT NULL, version INTEGER NOT NULL, resource_id TEXT NOT NULL UNIQUE, sources TEXT NOT NULL, PRIMARY KEY(id,version))")
        try:
            with conn:
                yield conn
        except BaseException:
            # The Word transaction is already rolled back. Unique pin IDs ensure
            # compensation cannot release a later writer's successful CAS pins.
            if conn.pending_resource_ids:
                from src.session.files import release_resource_references
                for resource_id in conn.pending_resource_ids:
                    release_resource_references(self.owner, "word_draft", resource_id)
            raise
        finally:
            conn.close()

    def _pin_version_sources(self, conn, document_id, version, state, source_files):
        expected = {}
        sources = list(source_files or [])
        for block in state["blocks"]:
            if block["type"] == "image":
                sources.append({"file_id": block["file_id"], "sha256": hashlib.sha256(
                    base64.b64decode(block["_image_data"])).hexdigest()})
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"file_id", "sha256"}:
                raise DocumentError("source_integrity", "源文件须包含 file_id 和不可变 SHA-256。")
            fid, digest = source["file_id"], source["sha256"]
            if (not isinstance(fid, str) or not re.fullmatch(r"[a-f0-9]{32}", fid)
                    or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                    or (fid in expected and expected[fid] != digest)):
                raise DocumentError("source_integrity", "源文件版本或 SHA-256 不一致；本批未写入。")
            expected[fid] = digest
        if not expected:
            return
        from src.session.files import replace_resource_references
        resource_id = f"{document_id}:{version}:{uuid.uuid4().hex}"
        refs = replace_resource_references(self.owner, "word_draft", resource_id, sorted(expected))
        # Register compensation immediately after the atomic pin, before any
        # hash comparison or Word SQL can fail.
        conn.pending_resource_ids.append(resource_id)
        if {ref["file_id"]: ref["sha256"] for ref in refs} != expected:
            raise DocumentError("source_integrity", "源文件与文档引用的不可变版本不一致；本批未写入。")
        conn.execute("INSERT INTO word_version_sources VALUES(?,?,?,?)",
                     (document_id, version, resource_id, _json(refs)))

    def _load(self, conn, document_id):
        _identifier(document_id, "document_id")
        row = conn.execute("SELECT * FROM word_drafts WHERE id=? AND owner=? AND session=?",
                           (document_id, self.owner, self.session)).fetchone()
        if not row:
            raise DocumentError("not_found", "此账号和会话下没有该文档草稿。")
        return row, json.loads(row["state"])

    def _summary(self, document_id, version, state):
        blocks = state["blocks"]
        complete = set()
        for section in state["outline"]:
            members = [b for b in blocks if b.get("section_id") == section["section_id"]]
            if (members and members[-1]["type"] == "section_end"
                    and any(b["type"] == "heading" for b in members)
                    and any(b["type"] not in {"heading", "caption", "section_end", "document_end"} for b in members)):
                complete.add(section["section_id"])
        return {"document_id": document_id, "version": version,
                "expected_sections": [s["section_id"] for s in state["outline"]],
                "completed_sections": [s["section_id"] for s in state["outline"] if s["section_id"] in complete],
                "block_count": len(blocks), "last_block_id": blocks[-1]["block_id"] if blocks else "",
                "total_chars": sum(len(b.get("text", "")) + sum(len(r["text"]) for r in b.get("runs", [])) +
                                   sum(len(str(v)) for row in b.get("rows", []) for v in row if v is not None) for b in blocks),
                "total_tables": sum(b["type"] == "table" for b in blocks),
                "total_images": sum(b["type"] == "image" for b in blocks)}

    def _blocks(self, blocks, state):
        if not isinstance(blocks, list) or not blocks or len(blocks) > 80:
            raise DocumentError("invalid_argument", "每批须有 1–80 个完整结构块。")
        if len(_json(blocks)) > 60000:
            raise DocumentError("batch_too_large", "本批超过 60000 字符，请缩小批次；未写入。")
        sections = {s["section_id"] for s in state["outline"]}
        allowed = {"block_id", "type", "section_id", "text", "runs", "level", "ordered", "columns", "rows", "caption", "file_id", "width_cm"}
        result = []
        for source in blocks:
            if not isinstance(source, dict) or set(source) - allowed:
                raise DocumentError("invalid_argument", "结构块类型或字段不受支持。")
            block = dict(source)
            _identifier(block.get("block_id"), "block_id")
            kind = block.get("type")
            if kind not in _TYPES:
                raise DocumentError("invalid_argument", "未知结构块类型。")
            if kind != "document_end" and block.get("section_id") not in sections:
                raise DocumentError("invalid_section", "结构块须指向大纲中的 section_id。")
            if kind == "table":
                columns, rows = block.get("columns"), block.get("rows")
                if not isinstance(columns, list) or not 1 <= len(columns) <= 12:
                    raise DocumentError("invalid_table", "Word 表格须为 1–12 列；宽表请导出 XLSX。")
                block["columns"] = [_text(c, "列名", 200) for c in columns]
                if not isinstance(rows, list) or not rows or len(rows) * len(columns) > 5000:
                    raise DocumentError("invalid_table", "表格须有实际数据行，且本块不超过 5000 个单元格。")
                for row in rows:
                    if not isinstance(row, list) or len(row) != len(columns):
                        raise DocumentError("invalid_table", "表格行列数不一致。")
                    for value in row:
                        if value is not None and type(value) not in (str, int, float, bool):
                            raise DocumentError("invalid_table", "表格单元格只允许文本、数字或 null。")
                        if value is not None and str(value).strip():
                            _text(str(value), "单元格", 4000)
                if not any(v is not None and str(v).strip() for row in rows for v in row):
                    raise DocumentError("invalid_table", "空数据表不能标记为完整。")
            elif kind == "image":
                if not self.image_reader:
                    raise DocumentError("image_unavailable", "没有授权的图片读取器。")
                data = self.image_reader(_identifier(block.get("file_id"), "file_id"))
                from PIL import Image
                with Image.open(io.BytesIO(data)) as image:
                    if image.format not in {"PNG", "JPEG"} or image.width * image.height > 40000000:
                        raise DocumentError("invalid_image", "仅支持不超过 4000 万像素的 PNG/JPEG。")
                    image.verify()
                block["_image_data"] = base64.b64encode(data).decode("ascii")
                block["caption"] = _text(block.get("caption"), "图题", 1000)
                width = block.get("width_cm", 15)
                if type(width) not in (int, float) or not 1 <= width <= 16.6:
                    raise DocumentError("invalid_image", "图片宽度须为 1–16.6 cm。")
                block["width_cm"] = width
            elif kind not in {"section_end", "document_end"}:
                if block.get("runs"):
                    if not isinstance(block["runs"], list) or len(block["runs"]) > 200:
                        raise DocumentError("invalid_argument", "runs 须为不超过 200 项的文字格式数组。")
                    for run in block["runs"]:
                        if not isinstance(run, dict) or set(run) - {"text", "bold", "italic", "subscript", "superscript"}:
                            raise DocumentError("invalid_argument", "runs 含不支持的格式。")
                        _text(run.get("text"))
                        if any(type(v) is not bool for k, v in run.items() if k != "text"):
                            raise DocumentError("invalid_argument", "文字格式属性须为布尔值。")
                else:
                    block["text"] = _text(block.get("text"))
                if kind == "heading" and (type(block.get("level", 1)) is not int or block.get("level", 1) not in (1, 2, 3)):
                    raise DocumentError("invalid_argument", "标题层级须为 1、2 或 3。")
            if block.get("caption"):
                _text(block["caption"], "题注", 1000)
            result.append(block)
        if len({b["block_id"] for b in result}) != len(result):
            raise DocumentError("duplicate_block", "本批 block_id 重复。")
        return result

    def create(self, *, title, profile, outline, batch_id):
        _identifier(batch_id, "batch_id")
        _text(title, "title", 240)
        if profile not in PROFILES:
            raise DocumentError("invalid_profile", "不支持的文档类型。")
        if not isinstance(outline, list) or not 1 <= len(outline) <= 100:
            raise DocumentError("invalid_outline", "须先提供 1–100 节的大纲。")
        for section in outline:
            if not isinstance(section, dict) or set(section) != {"section_id", "title"}:
                raise DocumentError("invalid_outline", "大纲每项须仅有 section_id 和 title。")
            _identifier(section["section_id"], "section_id")
            _text(section["title"], "章节标题", 200)
        if len({s["section_id"] for s in outline}) != len(outline):
            raise DocumentError("invalid_outline", "大纲 section_id 重复。")
        # A lost create response is recoverable using the same account/session/batch.
        document_id = hashlib.sha256(f"{self.owner}:{self.session}:{batch_id}".encode()).hexdigest()[:32]
        state = {"title": title, "profile": profile, "outline": outline, "blocks": []}
        digest = hashlib.sha256(_json(state).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            prior = conn.execute("SELECT * FROM word_batches WHERE id=? AND batch=?", (document_id, batch_id)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise DocumentError("batch_conflict", "同一创建 batch_id 的内容不同。")
                return json.loads(prior["receipt"])
            payload = render_document(state)
            receipt = self._summary(document_id, 1, state)
            conn.execute("INSERT INTO word_drafts VALUES(?,?,?,?,?,?)", (document_id, self.owner, self.session, 1, _json(state), time.time()))
            conn.execute("INSERT INTO word_versions VALUES(?,?,?,NULL)", (document_id, 1, payload))
            conn.execute("INSERT INTO word_batches VALUES(?,?,?,?)", (document_id, batch_id, digest, _json(receipt)))
            return receipt

    def inspect(self, document_id, *, offset=0, limit=40):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise DocumentError("invalid_argument", "inspect 需要非负 offset、1–100 的 limit。")
        with self._connect() as conn:
            row, state = self._load(conn, document_id)
            summary = self._summary(document_id, row["version"], state)
            summary["blocks"] = [{k: b[k] for k in ("block_id", "type", "section_id") if k in b}
                                 for b in state["blocks"][offset:offset + limit]]
            summary["next_offset"] = offset + len(summary["blocks"])
            return summary

    def mutate(self, document_id, *, operation, batch_id, expected_version, blocks, block_ids=None,
               source_files: list[dict[str, str]] | None = None):
        _identifier(batch_id, "batch_id")
        if operation not in {"append_blocks", "replace_blocks"} or type(expected_version) is not int:
            raise DocumentError("invalid_argument", "需要有效 operation 和整数 expected_version。")
        digest_input = [operation, blocks, block_ids]
        if source_files:
            digest_input.append(source_files)
        digest = hashlib.sha256(_json(digest_input).encode()).hexdigest()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row, state = self._load(conn, document_id)
            prior = conn.execute("SELECT * FROM word_batches WHERE id=? AND batch=?", (document_id, batch_id)).fetchone()
            if prior:
                if prior["digest"] != digest:
                    raise DocumentError("batch_conflict", "同一 batch_id 的内容不同；没有追加。")
                return json.loads(prior["receipt"])
            if row["version"] != expected_version:
                raise DocumentError("version_conflict", f"当前版本 {row['version']}；请 inspect 后从已提交块继续。")
            limit = _version_limit()
            retained = conn.execute("SELECT count(*) FROM word_versions WHERE id=?", (document_id,)).fetchone()[0]
            if retained >= limit:
                raise DocumentError("version_limit", f"草稿已保留 {retained} 个版本，达到 CITRUS_WORD_MAX_VERSIONS={limit} 上限；"
                                    "本批未写入。历史版本和源文件保护已保留；请创建新草稿或由管理员提高上限。")
            normalized = self._blocks(blocks, state)
            existing = state["blocks"]
            if operation == "replace_blocks":
                if not isinstance(block_ids, list) or not block_ids or len(set(block_ids)) != len(block_ids):
                    raise DocumentError("invalid_argument", "替换需要非空、无重复的 block_ids。")
                indices = [i for i, b in enumerate(existing) if b["block_id"] in block_ids]
                if len(indices) != len(block_ids) or indices != list(range(min(indices), max(indices) + 1)):
                    raise DocumentError("invalid_replacement", "仅支持替换已存在、连续的结构块；其余块保留。")
                existing = existing[:indices[0]] + normalized + existing[indices[-1] + 1:]
            else:
                existing = existing + normalized
            if len({b["block_id"] for b in existing}) != len(existing):
                raise DocumentError("duplicate_block", "block_id 已存在；请重用原 batch_id 重试或 replace_blocks。")
            if len(existing) > 3000 or len(_json(existing)) > 8000000:
                raise DocumentError("document_too_large", "文档超过本工具容量，请拆分文档。")
            state["blocks"] = existing
            payload = render_document(state)
            version = row["version"] + 1
            receipt = self._summary(document_id, version, state)
            receipt["accepted_block_ids"] = [b["block_id"] for b in normalized]
            self._pin_version_sources(conn, document_id, version, state, source_files)
            conn.execute("UPDATE word_drafts SET version=?,state=? WHERE id=?", (version, _json(state), document_id))
            conn.execute("INSERT INTO word_versions VALUES(?,?,?,NULL)", (document_id, version, payload))
            conn.execute("INSERT INTO word_batches VALUES(?,?,?,?)", (document_id, batch_id, digest, _json(receipt)))
            return receipt

    def finalize(self, document_id, *, expected_version, expected_last_block_id, filename,
                 publisher: Callable[[str, bytes], dict], verifier: Callable[[dict], None], publication_scope: str = ""):
        if type(expected_version) is not int:
            raise DocumentError("invalid_argument", "finalize 需要整数 expected_version。")
        _identifier(expected_last_block_id, "expected_last_block_id")
        if not isinstance(filename, str) or filename != filename.strip(" .") or not filename.lower().endswith(".docx") or len(filename) > 180 or re.search(r'[\\/:*?"<>|\x00-\x1f]', filename):
            raise DocumentError("invalid_filename", "需要不含路径的 .docx 文件名（最多180字符）。")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row, state = self._load(conn, document_id)
            if row["version"] != expected_version:
                raise DocumentError("version_conflict", "版本已改变，请 inspect 后确认最终块。")
            blocks = state["blocks"]
            if not blocks or blocks[-1]["type"] != "document_end" or blocks[-1]["block_id"] != expected_last_block_id:
                raise DocumentError("incomplete_document", "末块须是明确的 document_end，且与 expected_last_block_id 一致。")
            for section in state["outline"]:
                members = [b for b in blocks if b.get("section_id") == section["section_id"]]
                if (not members or members[-1]["type"] != "section_end"
                        or not any(b["type"] not in {"heading", "caption", "section_end", "document_end"} for b in members)):
                    raise DocumentError("incomplete_sections", f"章节 {section['title']} 没有实际正文或 section_end，未发布。")
                if not any(b["type"] == "heading" for b in members):
                    raise DocumentError("incomplete_sections", f"章节 {section['title']} 缺少真实标题块。")
            snapshot = conn.execute("SELECT * FROM word_versions WHERE id=? AND version=?", (document_id, expected_version)).fetchone()
            payload = bytes(snapshot["payload"])
            validation = validate_docx(payload)
            summary = self._summary(document_id, expected_version, state)
            if validation["tables"] != summary["total_tables"] or validation["images"] != summary["total_images"]:
                raise DocumentError("content_mismatch", "DOCX 实际表格/图片数与结构块不一致。")
            if snapshot["publication"]:
                result = json.loads(snapshot["publication"])
                if result.get("requested_filename", result["filename"]) != filename:
                    raise DocumentError("publication_conflict", "此版本已用其他文件名发布；修改草稿后再发布新版本。")
                if result.get("publication_scope", "") == publication_scope:
                    verifier(result)
                    return result
            result = publisher(filename, payload)
            # The existing publisher normalizes download names. Verify its actual
            # registry name, while retaining the requested name for retry identity.
            result.setdefault("filename", filename)
            # The publisher may canonicalize the container (OOXML timestamps) so
            # that an identical re-export reuses its row. The receipt must record
            # the bytes actually registered, not the pre-publication snapshot,
            # or the download check compares against a hash nobody stored.
            published_sha256 = str(result.get("sha256") or "") or hashlib.sha256(payload).hexdigest()
            published_size = int(result.get("size") or 0) or len(payload)
            result.update(document_id=document_id, version=expected_version, validation={**validation, **summary},
                          sha256=published_sha256, size=published_size, requested_filename=filename,
                          publication_scope=publication_scope)
            verifier(result)
            conn.execute("UPDATE word_versions SET publication=? WHERE id=? AND version=?", (_json(result), document_id, expected_version))
            return result
