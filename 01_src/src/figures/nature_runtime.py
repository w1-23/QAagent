"""Pinned nature-figure helpers and final-artifact QA inside the figure worker.

Only explicit files beneath the packaged vendor root are executable. Uploaded
paths are never added to sys.path. Upstream originals remain byte-identical;
application policy and result adaptation live here.
"""
from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

SOURCE = "https://github.com/Yuan1z0825/nature-skills"
COMMIT = "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
SKILL_VERSION = "2.8.0"
_VENDOR_ROOT = Path(__file__).resolve().parents[1] / "prompts/skills/vendor/nature-skills"
_MODULES = frozenset({"validate_figure", "audit_panel_alignment", "audit_pdf_text",
                      "audit_figure_collisions", "figure_safety", "plot_templates"})


class NatureAuditError(ValueError):
    """A figure cannot be published; receipt retains every attempted check."""

    def __init__(self, receipt: dict[str, Any]):
        self.receipt = receipt
        blocked = [name for name, check in receipt["checks"].items()
                   if check["status"] in {"fail", "not_auditable"}]
        super().__init__("[NATURE_FIGURE_QA] " + receipt["status"] + ": " + ", ".join(blocked))


def _load(name: str):
    if name not in _MODULES:
        raise ValueError("Unapproved nature-figure module")
    root = _VENDOR_ROOT.resolve()
    provenance = json.loads((root / "provenance.json").read_bytes())
    if provenance.get("source") != SOURCE or provenance.get("commit") != COMMIT:
        raise ValueError("nature-figure provenance identity mismatch")
    relative = f"skills/nature-figure/scripts/{name}.py"
    entries = [row for row in provenance["files"] if row.get("path") == relative]
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or len(entries) != 1:
        raise ValueError("nature-figure provenance path mismatch")
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != entries[0].get("sha256"):
        raise ValueError("nature-figure source hash mismatch: " + name)
    module_name = "_citrus_nature_" + name + "_" + digest
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None:
        raise ImportError("Cannot load nature-figure module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module  # dataclasses resolve their module here
    try:
        # Execute the bytes just hashed, avoiding a second unverified read.
        exec(compile(data, str(path), "exec"), module.__dict__)
    except BaseException:
        sys.modules.pop(module_name, None)
        raise
    return module


def configure_style() -> None:
    """Apply the upstream manuscript palette/typography drawing configuration."""
    module = _load("plot_templates")
    module.require_dependencies()
    module.configure_style()


def interp_monotone(target: Any, xp: Any, fp: Any):
    return _load("figure_safety").interp_monotone(target, xp, fp)


def label_y_above(values: Any, spread: Any | None = None, pad_fraction: float = 0.04):
    return _load("figure_safety").label_y_above(values, spread, pad_fraction)


def _unavailable(exc: Exception) -> dict[str, Any]:
    return {"status": "not_auditable", "findings": [],
            "error": f"{type(exc).__name__}: {exc}"[:2000]}


def _geometry_status(report: dict, code: int) -> str:
    if code == 2:
        return "not_auditable"
    if code == 1:
        return "fail"
    if report.get("verdict") == "NOT APPLICABLE":
        return "not_applicable"
    if report.get("summary", {}).get("warn", 0):
        return "warn"
    return "pass"


def audit_fig(figure: Any, source: str, *,
              pdf_factory: Callable[[], bytes]) -> tuple[dict[str, Any], bytes]:
    """Audit final figure state and exactly one internal PDF, never a CLI.

    Returns (receipt, audited_pdf_bytes). Publication must reuse these bytes
    when PDF is requested. Rendered FAIL or any unavailable check raises
    NatureAuditError with the receipt. Source findings are advisory; raw FAIL
    and WARN remain visible as review_required rather than silently passing.
    """
    receipt: dict[str, Any] = {
        "version": 1, "skill": "nature-figure", "skill_version": SKILL_VERSION,
        "commit": COMMIT, "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "checks": {},
    }
    checks = receipt["checks"]
    try:
        module = _load("validate_figure")
        findings = module.validate_source(source, "python")
        summary = module.summarize(findings, strict=False)
        checks["source_preflight"] = {
            "status": "review_required" if summary["counts"]["FAIL"] or
                      summary["counts"]["WARN"] else "pass",
            "findings": [dataclasses.asdict(item) for item in findings],
            "summary": summary,
            "policy": "advisory_source_preflight",
            "adaptation": (
                "Original source is checked unchanged. CitrusSchalor manages font defaults, "
                "export formats and final-artifact QA outside the emit script. Raw upstream "
                "source findings remain advisory; rendered alignment, PDF text and collision "
                "failures block publication. This is not a claim of submission compliance."
            ),
        }
    except Exception as exc:
        checks["source_preflight"] = _unavailable(exc)
    try:
        module = _load("audit_panel_alignment")
        layout = module.matplotlib_layout_manifest(figure)
        report = module.audit_layout_manifest(layout)
        checks["panel_alignment"] = {
            "status": _geometry_status(report, module.exit_code(report)),
            "findings": report.get("findings", []), "report": report,
        }
    except Exception as exc:
        checks["panel_alignment"] = _unavailable(exc)
    pdf = b""
    try:
        pdf = pdf_factory()
        if not isinstance(pdf, bytes) or not pdf.startswith(b"%PDF-"):
            raise ValueError("Final figure export did not produce PDF bytes")
        if len(pdf) > 24 * 1024 * 1024:
            raise ValueError("Internal QA PDF exceeds the worker byte budget")
        receipt["pdf_sha256"] = hashlib.sha256(pdf).hexdigest()
        receipt["pdf_bytes"] = len(pdf)
    except Exception as exc:
        checks["pdf_text"] = _unavailable(exc)
        checks["pdf_collisions"] = _unavailable(exc)
    else:
        try:
            report = _load("audit_pdf_text").audit_pdf(pdf)
            status = ("not_auditable" if not report["auditable"] else
                      "fail" if report["below_minimum_count"] else
                      "warn" if report["warnings"] else "pass")
            checks["pdf_text"] = {"status": status,
                                  "findings": report["below_minimum"], "report": report}
        except Exception as exc:
            checks["pdf_text"] = _unavailable(exc)
        try:
            module = _load("audit_figure_collisions")
            # The upstream API takes a path. It sees only the bytes already
            # exported and hashed above, in a new private scratch directory.
            with tempfile.TemporaryDirectory(prefix="citrus-nature-qa-") as temp:
                path = Path(temp) / "figure.pdf"
                path.write_bytes(pdf)
                report = module.audit_pdf(path)
            report.pop("pdf", None)  # never persist an internal scratch path
            checks["pdf_collisions"] = {
                "status": _geometry_status(report, module.exit_code(report)),
                "findings": report.get("findings", []), "report": report,
            }
        except Exception as exc:
            checks["pdf_collisions"] = _unavailable(exc)
    statuses = {check["status"] for check in checks.values()}
    receipt["status"] = next((status for status in ("fail", "not_auditable", "review_required", "warn")
                              if status in statuses), "pass")
    if statuses & {"fail", "not_auditable"}:
        raise NatureAuditError(receipt)
    return receipt, pdf
