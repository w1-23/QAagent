"""Export existing account-library bytes, without analysis or format conversion.

The library is the sole storage authority. A figure's saved exports may offer
other formats of that exact stored render; filenames and chat history never
establish that relationship. New byte generation, if added later, must use
research.artifacts.publish_artifact and wait for committed library registration.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


class ExportError(ValueError):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.status = code, status
        super().__init__(message)


def _checked_file(owner: int, file_id: str) -> dict[str, Any]:
    from src.session import files
    entry = files.get_account_file(file_id, int(owner))
    if not entry:
        raise ExportError("not_found", "文件不存在、已过期或不属于当前账号。", 404)
    path = entry["path"]
    expected = str(entry.get("sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", expected):
        raise ExportError("unverified_source", "文件缺少可信校验值，不能确认导出版本。", 409)
    try:
        before = path.stat()
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
    except OSError as exc:
        raise ExportError("source_unavailable", "文件暂时不可读取。", 409) from exc
    if (digest.hexdigest() != expected or before.st_size != int(entry["size_bytes"])
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)):
        raise ExportError("source_changed", "文件校验失败，请刷新资料库后重试。", 409)
    return entry


def _format(entry: dict) -> str:
    suffix = Path(str(entry.get("filename") or "")).suffix.lower().lstrip(".")
    return suffix if re.fullmatch(r"[a-z0-9]{1,16}", suffix) else "original"


def _option(entry: dict) -> dict:
    return {"target_format": _format(entry), "file_id": entry["file_id"],
            "filename": entry["filename"], "sha256": entry["sha256"],
            "size_bytes": entry["size_bytes"], "download_url": entry["download_url"],
            "mode": "existing"}


def _saved_figure_exports(owner: int, source: dict):
    """Read saved exports only; never call detail/preview/render or execute scripts.

    Limit candidates to snapshots containing this precise library file identity.
    Old render files without a retained snapshot still support original download.
    """
    from src.figures import store
    if source.get("origin") != "generated":
        return
    with store._connect() as conn:
        rows = conn.execute("SELECT figure_id,render_version,exports_json FROM figure_projects "
                            "WHERE owner_user_id=? AND exports_json LIKE ?",
                            (int(owner), "%" + source["file_id"] + "%")).fetchall()
    for row in rows:
        try:
            exports = json.loads(row["exports_json"])
        except (ValueError, TypeError):
            continue
        if not isinstance(exports, dict):
            continue
        if not any(isinstance(ref, dict) and ref.get("file_id") == source["file_id"]
                   and ref.get("sha256") == source["sha256"] for ref in exports.values()):
            continue
        for fmt, ref in exports.items():
            if not isinstance(ref, dict) or not ref.get("file_id") or not ref.get("sha256"):
                continue
            try:
                entry = _checked_file(owner, str(ref["file_id"]))
            except ExportError:
                continue
            if (entry["sha256"] != ref["sha256"] or _format(entry) != str(fmt).lower()
                    or entry.get("origin") != "generated"):
                continue
            yield entry


def export_options(owner: int, file_id: str) -> dict:
    """Only advertise verified, already registered formats of this exact source."""
    source = _checked_file(owner, file_id)
    original = _option(source)
    by_format: dict[str, dict[str, dict]] = {}
    for entry in _saved_figure_exports(owner, source):
        option = _option(entry)
        by_format.setdefault(option["target_format"], {})[option["file_id"]] = option
    # A source referenced by conflicting snapshots cannot choose a peer version
    # implicitly. Ambiguous alternative formats are omitted, never guessed.
    options = [original] + [next(iter(candidates.values()))
        for fmt, candidates in sorted(by_format.items())
        if fmt != original["target_format"] and len(candidates) == 1]
    return {"file_id": source["file_id"], "source_sha256": source["sha256"],
            "filename": source["filename"], "options": options,
            "conversion_available": False,
            "note": "仅提供已经生成并入库的格式；下载原件保留批注、工作表和原始内容。"}


def export_file(owner: int, file_id: str, target_format: str, *, source_sha256: str = "",
                target_file_id: str = "", target_sha256: str = "") -> dict:
    """Return an immutable existing reference, idempotently; never create copies."""
    fmt = str(target_format).strip().lower().lstrip(".")
    payload = export_options(owner, file_id)
    if source_sha256 and source_sha256 != payload["source_sha256"]:
        raise ExportError("source_changed", "导出来源已改变，请重新选择文件。", 409)
    chosen = next((item for item in payload["options"] if item["target_format"] == fmt), None)
    if chosen is None:
        raise ExportError("unsupported_format", "此成果没有所选格式的已生成版本；请下载已有格式。", 422)
    if ((target_file_id and target_file_id != chosen["file_id"])
            or (target_sha256 and target_sha256 != chosen["sha256"])):
        raise ExportError("target_changed", "所选成果版本已改变，请重新打开导出。", 409)
    return {"status": "complete", "source_file_id": payload["file_id"],
            "source_sha256": payload["source_sha256"], **chosen}
