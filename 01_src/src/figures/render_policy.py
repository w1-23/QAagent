"""Worker rendering modes are supplied by the host, never by figure scripts."""
from __future__ import annotations

import hashlib

RENDER_PROTOCOL = 2


def render_mode(value="initial") -> str:
    if value not in {"initial", "edit", "preview"}:
        raise ValueError("unknown figure render mode")
    return value


def export_figure(rendered, *, mode, exporter, formats, dpi=300, source=""):
    mode = render_mode(mode)
    receipt = None
    if mode == "initial":
        if __package__ == "figures":
            from figures.nature_runtime import audit_fig
        else:
            from src.figures.nature_runtime import audit_fig
        receipt, pdf = audit_fig(rendered.figure, source,
            pdf_factory=lambda: exporter.export_all(rendered, formats=("pdf",), dpi=dpi)["pdf"])
        blobs = exporter.export_all(rendered, formats=tuple(fmt for fmt in formats if fmt != "pdf"), dpi=dpi)
        if "pdf" in formats:
            blobs["pdf"] = pdf
    else:
        blobs = exporter.export_all(rendered, formats=tuple(formats), dpi=dpi)
    signatures = {"svg": b"<?xml", "png": b"\x89PNG\r\n\x1a\n", "pdf": b"%PDF-",
                  "tiff": (b"II", b"MM")}
    for fmt, blob in blobs.items():
        if not isinstance(blob, bytes) or not blob.startswith(signatures[fmt]):
            raise ValueError("invalid figure output format")
    acknowledgement = {"version": RENDER_PROTOCOL, "mode": mode,
                       "checks": {fmt: {"sha256": hashlib.sha256(blob).hexdigest(),
                                        "size_bytes": len(blob), "format": "pass"}
                                  for fmt, blob in blobs.items()},
                       "nature_audit": "performed" if mode == "initial" else "not_repeated_manual_edit"}
    return blobs, receipt, acknowledgement
