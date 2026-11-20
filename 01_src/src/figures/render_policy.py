"""Worker rendering modes are supplied by the host, never by figure scripts."""
from __future__ import annotations

import hashlib

RENDER_PROTOCOL = 2


def render_mode(value="initial") -> str:
    if value not in {"initial", "edit", "preview"}:
        raise ValueError("unknown figure render mode")
    return value


def _current_qa(*, mode, pdf, layout, blobs, source):
    """Audit this export's exact bytes; never re-export to certify them."""
    from .nature_runtime import (CurrentQaError, current_security_findings,
                                 layout_evidence, run_current_qa)
    artifacts = [{"format": "pdf", "bytes": pdf,
                  "role": "figure" if "pdf" in blobs else "internal_qa"}]
    artifacts.extend({"format": fmt, "bytes": blob, "role": "figure"}
                     for fmt, blob in blobs.items() if fmt != "pdf")
    receipt = run_current_qa(artifacts=artifacts, mode=mode,
                             source=source or None, layout_manifest=layout)
    evidence = layout_evidence(layout, hashlib.sha256(pdf).hexdigest())
    security = current_security_findings(receipt)
    blocked = list(receipt.get("overall", {}).get("blocking") or ())
    if security or blocked:
        raise CurrentQaError(receipt, evidence, security)
    return receipt, evidence


def export_figure(rendered, *, mode, exporter, formats, dpi=300, source="",
                  allow_format_failure=False):
    mode = render_mode(mode)
    receipt = None
    current_receipt = None
    current_layout = None
    export_errors = {}

    def export_formats(requested):
        blobs = {}
        for fmt in requested:
            try:
                blobs.update(exporter.export_all(rendered, formats=(fmt,), dpi=dpi))
            except exporter.ExportError as exc:
                reason = exc.args[0] if exc.args else ""
                if not allow_format_failure or reason != "unsupported_raster_svg":
                    raise
                # The rejected SVG bytes never leave the sanitizer. v2 has
                # independent slots, so audited PNG/PDF and tables can continue.
                export_errors[fmt] = {"reason_code": reason,
                                      "error": str(exc.args[1] if len(exc.args) > 1 else exc),
                                      "recovery_formats": ["png", "pdf"]}
        return blobs

    pdf = layout = None
    if mode in {"initial", "edit"}:
        # One PDF is exported and its draw-time geometry captured. Those same
        # bytes are audited and, when requested, published for download.
        from citrus_figures.render_policy import export_with_layout
        pdf, layout = export_with_layout(rendered, exporter=exporter, dpi=dpi)
    if mode == "initial":
        if __package__ == "figures":
            from figures.nature_runtime import audit_fig
        else:
            from src.figures.nature_runtime import audit_fig
        # The legacy v1 receipt is preserved; the pinned PDF above is audited
        # rather than exporting a second one.
        receipt, pdf = audit_fig(rendered.figure, source, pdf_factory=lambda: pdf)
        blobs = export_formats(fmt for fmt in formats if fmt != "pdf")
        if "pdf" in formats:
            blobs["pdf"] = pdf
        current_receipt, current_layout = _current_qa(
            mode="initial", pdf=pdf, layout=layout, blobs=blobs, source=source)
    elif mode == "edit":
        blobs = export_formats(fmt for fmt in formats if fmt != "pdf")
        if "pdf" in formats:
            blobs["pdf"] = pdf
        current_receipt, current_layout = _current_qa(
            mode="edit", pdf=pdf, layout=layout, blobs=blobs, source=source)
    else:
        blobs = export_formats(formats)
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
    if export_errors:
        acknowledgement["export_errors"] = export_errors
    if current_receipt is not None:
        acknowledgement["current_qa_receipt"] = current_receipt
    if current_layout is not None:
        acknowledgement["current_qa_layout"] = current_layout
    return blobs, receipt, acknowledgement
