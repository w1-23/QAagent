"""Make an OOXML package byte-stable for identical content.

An XLSX/DOCX is a ZIP. Two runs that write the same cells produce different
bytes because every ZIP entry carries the current wall clock, and openpyxl also
stamps ``docProps/core.xml`` with ``created``/``modified``. The publisher hashes
the output bytes to recognise an exact retry, so that instability turned every
re-export into a *new* immutable version: the audited 40-task run published 76
artifacts, 25 of which were duplicate versions of a file it had already made.

Normalising the container is not a change to publication semantics. Different
content still produces different bytes and therefore a new ``artifact_id``;
historical download URLs still return their original bytes. Only a genuine
re-export of identical content collapses onto the row it already has.

Real provenance stays where it belongs: the artifact row records ``created_at``,
the source file hash and the producing request. Nothing here invents a
scientific timestamp inside the document body.
"""
from __future__ import annotations

import io
import re
import zipfile

# The ZIP format's own epoch. A fixed date is what makes the container
# reproducible; it is metadata about the file format, not a research result.
FIXED_ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
FIXED_CORE_TIMESTAMP = "1980-01-01T00:00:00Z"
_CORE_PART = "docProps/core.xml"
_TIMESTAMP_PART_RE = re.compile(
    rb"(<(?:\w+:)?(?:created|modified)\b[^>]*>)([^<]*)(</(?:\w+:)?(?:created|modified)>)")
_OOXML_SUFFIXES = frozenset({".xlsx", ".xlsm", ".xltx", ".xltm",
                             ".docx", ".docm", ".dotx", ".dotm",
                             ".pptx", ".pptm"})
# A ZIP local file header. Checking it keeps a mislabelled file untouched.
_ZIP_MAGIC = b"PK\x03\x04"


def is_ooxml_filename(filename: str) -> bool:
    name = str(filename or "").casefold()
    return any(name.endswith(suffix) for suffix in _OOXML_SUFFIXES)


def normalize_ooxml_bytes(content: bytes) -> bytes:
    """Return ``content`` with container timestamps fixed, or unchanged.

    Any file that is not a readable OOXML ZIP is returned as-is: this is a
    determinism helper, never a validator, and it must not be able to corrupt a
    deliverable it does not understand.
    """
    if not content or not content.startswith(_ZIP_MAGIC):
        return content
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as source:
            entries = source.infolist()
            if not entries:
                return content
            payloads = [(info, source.read(info.filename)) for info in entries]
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError, ValueError):
        return content

    buffer = io.BytesIO()
    try:
        with zipfile.ZipFile(buffer, "w") as target:
            for info, payload in payloads:
                if info.filename == _CORE_PART:
                    payload = _TIMESTAMP_PART_RE.sub(
                        lambda m: m.group(1) + FIXED_CORE_TIMESTAMP.encode("ascii") + m.group(3),
                        payload)
                entry = zipfile.ZipInfo(info.filename, date_time=FIXED_ZIP_DATE_TIME)
                entry.compress_type = info.compress_type
                entry.external_attr = info.external_attr
                entry.internal_attr = info.internal_attr
                entry.create_system = info.create_system
                entry.comment = b""
                entry.extra = b""
                target.writestr(entry, payload)
    except (zipfile.BadZipFile, OSError, ValueError):
        return content
    return buffer.getvalue()