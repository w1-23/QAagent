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
import os
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


CURRENT_QA_VENDOR_RELATIVE = "skills/nature-figure/vendor/nature-skills"
CURRENT_QA_CHECKS = ("format_integrity", "source_preflight", "panel_alignment",
                     "pdf_text", "pdf_collisions", "svg_schematic", "visual")
# Reception gates on measured format integrity. The legacy v1 initial audit
# keeps its stricter rendered gate; after an explicit save, source preflight,
# PDF text, collisions and panel geometry remain visible advisories. SVG
# security findings are enforced separately from typography advisories.
CURRENT_QA_BLOCKING = {
    "initial": ("format_integrity", "panel_alignment", "pdf_text", "pdf_collisions"),
    "edit": ("format_integrity",),
    "reaudit": ("format_integrity",),
}
_SVG_SECURITY_KINDS = frozenset({"forbidden", "external-ref"})


class CurrentQaError(ValueError):
    """A current-byte check failed reception; the receipt remains evidence."""

    def __init__(self, receipt, layout=None, security=None):
        self.current_qa_receipt = receipt
        self.current_qa_layout = layout
        self.security = list(security or [])
        blocked = [*(receipt.get("overall", {}).get("blocking") or ()),
                   *(item.get("kind", "security") for item in self.security)]
        super().__init__("[NATURE_CURRENT_QA] " + ", ".join(blocked or ["blocked"]))


def _vendor_candidates():
    seen, candidates = set(), []

    def add(path):
        try:
            resolved = Path(path).expanduser().resolve()
        except OSError:
            return
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            candidates.append(resolved)

    explicit = os.environ.get("CITRUSQA_NATURE_VENDOR_ROOT", "").strip()
    if explicit:
        add(explicit)
    project = os.environ.get("CITRUSQA_PROJECT_ROOT", "").strip()
    if project:
        add(Path(project) / CURRENT_QA_VENDOR_RELATIVE)
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:6]:
        add(parent / CURRENT_QA_VENDOR_RELATIVE)
    root = os.environ.get("CITRUS_FIGURES_ROOT", "").strip()
    if root:
        base = Path(root)
        add(base / CURRENT_QA_VENDOR_RELATIVE)
        add(base.parent / CURRENT_QA_VENDOR_RELATIVE)
    return candidates


def current_vendor_root():
    """Return the first fully verified 44-file vendor root, or ``None``.

    The legacy 40-file root remains for the pinned v1 receipt. Only the current
    release inventory (which includes ``audit_svg_schematic``) is accepted
    here; an unverifiable root disables checks instead of trusting unhashed
    bytes or a partially populated directory.
    """
    try:
        from citrus_figures.nature_runtime import validate_vendor
    except ImportError:
        return None
    for candidate in _vendor_candidates():
        try:
            validate_vendor(candidate)
        except (OSError, ValueError, KeyError, TypeError):
            continue
        return candidate
    return None


def layout_evidence(layout, pdf_sha256):
    """Return capture evidence only when it names the exact exported PDF."""
    if not isinstance(layout, dict) or not isinstance(layout.get("layout"), dict):
        return None
    if not isinstance(pdf_sha256, str) or layout.get("artifact_sha256") != pdf_sha256:
        return None
    return {"artifact_sha256": pdf_sha256, "layout": layout["layout"],
            "capture": str(layout.get("capture") or "pdf_draw_event"),
            "rerendered": False}


def current_security_findings(receipt):
    """Extract SVG production-safety failures from an executed SVG check."""
    findings = []
    for check in receipt.get("checks") or ():
        if check.get("check") != "svg_schematic" or check.get("conclusion") != "fail":
            continue
        diagnostics = check.get("diagnostics") or {}
        for finding in diagnostics.get("findings") or ():
            if (finding.get("severity") == "FAIL"
                    and finding.get("kind") in _SVG_SECURITY_KINDS):
                findings.append({"kind": finding.get("kind"),
                                 "message": finding.get("message"),
                                 "artifact_sha256": (check.get("artifact") or {}).get("sha256")})
    return findings


def run_current_qa(*, artifacts, mode, source=None, layout_manifest=None,
                   supersedes=None, vendor_root=None, checks=None, backend="python"):
    """Audit exact caller bytes with the pinned, hash-verified vendor set.

    Artifacts are explicit bytes; this never renders, re-exports or executes
    source. ``layout_manifest`` is meaningful only when its ``artifact_sha256``
    matches a supplied artifact, which ``verify_current_figure`` enforces. A
    missing layout or vendor root stays an explicit ``not_run`` entry instead
    of being reported as a pass.
    """
    if mode not in CURRENT_QA_BLOCKING:
        raise ValueError("unknown current-QA mode")
    from citrus_figures.verification import verify_current_figure
    if isinstance(artifacts, dict):
        artifacts = [{"format": fmt, "bytes": blob} for fmt, blob in artifacts.items()]
    rows = []
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("bytes"), bytes):
            raise ValueError("current QA artifacts require explicit bytes")
        rows.append({"format": str(item.get("format") or "").lower().lstrip("."),
                     "bytes": item["bytes"], "role": str(item.get("role") or "figure")})
    if isinstance(source, bytes):
        source_bytes = source
    elif isinstance(source, str) and source.strip():
        source_bytes = source.encode("utf-8")
    else:
        source_bytes = None
    if vendor_root is None:
        vendor_root = current_vendor_root()
    return verify_current_figure(
        artifacts=rows, source=source_bytes, layout_manifest=layout_manifest,
        mode=mode, checks=checks, backend=backend,
        policy={"blocking_checks": list(CURRENT_QA_BLOCKING[mode])},
        vendor_root=vendor_root, supersedes=supersedes, visual_inspector=None)


def verify_svg_bytes(blob, *, mode="edit", supersedes=None, source=None,
                     vendor_root=None):
    """F5 entry: audit already-sanitized SVG bytes; never renders or executes."""
    if not isinstance(blob, bytes):
        raise ValueError("SVG verification requires explicit bytes")
    if not blob or len(blob) > 24 * 1024 * 1024:
        raise ValueError("SVG exceeds the current-QA byte budget")
    return run_current_qa(
        artifacts=[{"format": "svg", "bytes": blob, "role": "schematic"}],
        mode=mode, source=source, supersedes=supersedes, vendor_root=vendor_root)


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
                "Original source is checked unchanged. CitrusScholar manages font defaults, "
                "export formats and final-artifact QA outside the emit script. Raw upstream "
                "source findings remain advisory; rendered alignment, PDF text and collision "
                "failures block publication. This is not a claim of submission compliance."
            ),
        }
    except Exception as exc:
        checks["source_preflight"] = _unavailable(exc)
    try:
        module = _load("audit_panel_alignment")
        # Explicit cax= axes do not receive Matplotlib's '<colorbar>' label.
        # Identify the actual Colorbar attachment rather than guessing from
        # axis shape/position; PDF audits still inspect every rendered artist.
        from matplotlib.colorbar import Colorbar
        colorbars = [ax for ax in getattr(figure, "axes", ())
                     if isinstance(getattr(ax, "_colorbar", None), Colorbar)
                     and ax._colorbar.ax is ax]
        # Upstream also skips axes by label alone. A data axis named
        # '<colorbar>' is still a data panel, not an exemption.
        mislabeled = [(ax, ax.get_label()) for ax in getattr(figure, "axes", ())
                      if ax not in colorbars and str(ax.get_label()).startswith("<colorbar")]
        try:
            for ax, _ in mislabeled:
                ax.set_label("")
            layout = (module.matplotlib_layout_manifest(figure, exclude_axes=colorbars)
                      if colorbars else module.matplotlib_layout_manifest(figure))
        finally:
            for ax, label in mislabeled:
                ax.set_label(label)
        report = module.audit_layout_manifest(layout)
        if colorbars:
            report["excluded_axes"] = [
                {"id": ax.get_gid() or f"axes_{figure.axes.index(ax)}",
                 "reason": "Matplotlib Colorbar axis; not a data panel. PDF text and collision audits retained."}
                for ax in colorbars]

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
