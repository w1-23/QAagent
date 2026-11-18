"""Audit caller-supplied final bytes; never render or initialize a model."""
from __future__ import annotations
import dataclasses
import hashlib
import json
import tempfile
from pathlib import Path
from .nature_runtime import COMMIT, SKILL_VERSION, load_module

CHECKS = ("format_integrity", "source_preflight", "panel_alignment", "pdf_text", "pdf_collisions", "svg_schematic", "visual")
DEFAULT_BLOCKING = tuple(name for name in CHECKS if name != "source_preflight")


class Unauditable(ValueError):
    def __init__(self, report):
        self.report = report
        super().__init__("upstream not auditable")


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def _artifacts(artifacts):
    rows = []
    if isinstance(artifacts, dict):
        artifacts = [{"format": k, "bytes": v, "role": "figure"} for k, v in artifacts.items()]
    for item in artifacts:
        if not isinstance(item, dict) or not isinstance(item.get("bytes"), bytes):
            raise ValueError("artifacts require explicit bytes; resolve references before calling")
        fmt = str(item.get("format", "")).lower().lstrip(".")
        fmt = {"tif": "tiff", "jpg": "jpeg"}.get(fmt, fmt)
        blob = item["bytes"]
        identity = {"role": str(item.get("role", "figure")), "format": fmt,
                    "sha256": sha256(blob), "size": len(blob)}
        rows.append((identity, blob))
    return rows


def verify_current_figure(*, artifacts, source=None, layout_manifest=None, figure=None,
                          backend="python", mode="initial", checks=None, policy=None,
                          vendor_root=None, supersedes=None, visual_inspector=None):
    """Return a receipt with execution and quality as independent axes.

    layout_manifest must be {artifact_sha256, layout}; its producer attests that
    layout was captured at that export. A bare live figure cannot certify old
    bytes. visual_inspector is an explicitly supplied callable, never discovered.
    policy accepts blocking_checks; it does not authorize external operations.
    """
    if mode not in {"initial", "edit", "reaudit"}:
        raise ValueError("invalid verification mode")
    if mode == "reaudit" and not supersedes:
        raise ValueError("reaudit requires supersedes receipt identity")
    selected = tuple(CHECKS if checks is None else checks)
    if not selected or len(set(selected)) != len(selected) or set(selected) - set(CHECKS):
        raise ValueError("request at least one known check, without duplicates")
    rows = _artifacts(artifacts)
    by_format = {}
    for identity, blob in rows:
        by_format.setdefault(identity["format"], []).append((identity, blob))
    receipts, errors = [], []
    blocking_checks = set((policy or {}).get("blocking_checks", DEFAULT_BLOCKING))
    if blocking_checks - set(CHECKS):
        raise ValueError("unknown blocking check")

    def record(name, artifact=None, *, executed="not_run", conclusion="undetermined", reason="capability_absent", diagnostics=None, coverage=None):
        entry = {"check": name, "executed": executed, "conclusion": conclusion,
                 "reason_code": reason, "artifact": artifact, "diagnostics": diagnostics or {},
                 "coverage": coverage or {}}
        receipts.append(entry)
        return entry

    def run(name, identity, function):
        try:
            conclusion, reason, report, coverage = function()
            record(name, identity, executed="executed", conclusion=conclusion,
                   reason=reason, diagnostics=report, coverage=coverage)
        except Unauditable as exc:
            record(name, identity, executed="attempted", reason="upstream_not_auditable", diagnostics=exc.report)
        except (ImportError, ModuleNotFoundError) as exc:
            record(name, identity, reason="dependency_absent", diagnostics={"type": type(exc).__name__, "message": str(exc)[:500]})
        except FileNotFoundError as exc:
            record(name, identity, reason="resource_absent", diagnostics={"message": str(exc)[:500]})
        except Exception as exc:
            error = {"code": "check_error", "check": name, "message": f"{type(exc).__name__}: {exc}"[:1000], "retryable": False}
            errors.append(error)
            record(name, identity, executed="attempted", reason="program_error", diagnostics=error)

    def module(name):
        if vendor_root is None:
            raise FileNotFoundError("vendor_root not supplied")
        return load_module(name, vendor_root=vendor_root)

    def geometry(report, code):
        if code == 2:
            raise Unauditable(report)
        conclusion = ("fail" if code == 1 else "not_applicable" if report.get("verdict") == "NOT APPLICABLE"
                      else "warn" if report.get("summary", {}).get("warn", 0) else "pass")
        return conclusion, "upstream_result", report, {"visual_semantics": False}

    for name in selected:
        if name == "format_integrity":
            if not rows:
                record(name, reason="artifact_absent")
            for identity, blob in rows:
                def integrity(identity=identity, blob=blob):
                    fmt = identity["format"]
                    signatures = {"pdf": (b"%PDF-",), "png": (b"\x89PNG\r\n\x1a\n",), "tiff": (b"II*\x00", b"MM\x00*"), "jpeg": (b"\xff\xd8\xff",)}
                    if fmt == "svg":
                        import xml.etree.ElementTree as ET
                        try:
                            valid = ET.fromstring(blob).tag in {"svg", "{http://www.w3.org/2000/svg}svg"}
                        except ET.ParseError:
                            valid = False
                    elif fmt in signatures:
                        valid = blob.startswith(signatures[fmt])
                    else:
                        return "not_applicable", "unsupported_format", {}, {"signature_only": True}
                    return ("pass" if valid else "fail"), ("signature_valid" if valid else "invalid_signature"), {}, {"signature_only": True, "full_decode": False}
                run(name, identity, integrity)
        elif name == "source_preflight":
            if source is None:
                record(name, reason="source_absent")
            else:
                source_bytes = source if isinstance(source, bytes) else str(source).encode("utf-8")
                identity = {"role": "source", "format": backend, "sha256": sha256(source_bytes), "size": len(source_bytes)}
                def preflight():
                    source_text = source_bytes.decode("utf-8")
                    mod = module("validate_figure")
                    findings = mod.validate_source(source_text, backend)
                    summary = mod.summarize(findings, strict=False)
                    counts = summary["counts"]
                    return ("fail" if counts["FAIL"] else "warn" if counts["WARN"] else "pass"), "source_only", {"findings": [dataclasses.asdict(v) for v in findings], "summary": summary}, {"rendered_artifact": False}
                run(name, identity, preflight)
        elif name == "panel_alignment":
            bound = next((ident for ident, _ in rows if layout_manifest and ident["sha256"] == layout_manifest.get("artifact_sha256")), None)
            if bound is None or "layout" not in layout_manifest:
                record(name, reason="bound_layout_absent", coverage={"live_figure_supplied": figure is not None, "rerendered": False})
            else:
                def alignment():
                    mod = module("audit_panel_alignment")
                    report = mod.audit_layout_manifest(layout_manifest["layout"])
                    return geometry(report, mod.exit_code(report))
                run(name, bound, alignment)
        elif name in {"pdf_text", "pdf_collisions"}:
            if not by_format.get("pdf"):
                record(name, reason="format_absent")
            for identity, blob in by_format.get("pdf", []):
                def audit_pdf(blob=blob, name=name):
                    if not blob.startswith(b"%PDF-"):
                        return "fail", "invalid_signature", {}, {}
                    if name == "pdf_text":
                        report = module("audit_pdf_text").audit_pdf(blob)
                        if not report["auditable"]:
                            raise Unauditable(report)
                        conclusion = "fail" if report["below_minimum_count"] else "warn" if report["warnings"] else "pass"
                        return conclusion, "upstream_result", report, {"supported_pdf_streams_only": True, "visual_semantics": False}
                    mod = module("audit_figure_collisions")
                    with tempfile.TemporaryDirectory(prefix="citrus-figure-qa-") as temp:
                        path = Path(temp) / "figure.pdf"
                        path.write_bytes(blob)
                        report = mod.audit_pdf(path)
                    report.pop("pdf", None)
                    return geometry(report, mod.exit_code(report))
                run(name, identity, audit_pdf)
        elif name == "svg_schematic":
            if not by_format.get("svg"):
                record(name, reason="format_absent")
            for identity, blob in by_format.get("svg", []):
                def audit_svg(blob=blob):
                    mod = module("audit_svg_schematic")
                    report = mod.audit_svg(blob)
                    if not report["auditable"]:
                        raise Unauditable(report)
                    verdict = mod.verdict(report)
                    return ("fail" if verdict == "FAIL" else "warn" if verdict == "PASS WITH WARNINGS" else "pass"), "upstream_result", report, {"schematic_rules_only": True, "visual_semantics": False,
                        "adapter_compatibility": list(getattr(mod, "__citrus_compatibility__", []))}
                run(name, identity, audit_svg)
        elif name == "visual":
            if not rows:
                record(name, reason="artifact_absent")
            elif visual_inspector is None:
                record(name, reason="capability_absent", coverage={"model_initialized": False})
            else:
                for identity, blob in rows:
                    def inspect(identity=identity, blob=blob):
                        report = visual_inspector(blob, identity)
                        conclusion = report["conclusion"]
                        if conclusion not in {"pass", "warn", "fail", "not_applicable"}:
                            raise ValueError("invalid visual inspector conclusion")
                        return conclusion, "explicit_inspector", report, {"inspector_injected": True}
                    run(name, identity, inspect)
    unexecuted = [r["check"] for r in receipts if r["executed"] != "executed"]
    blocking = [r["check"] for r in receipts if r["check"] in blocking_checks and r["conclusion"] == "fail"]
    advisory = [r["check"] for r in receipts if r["conclusion"] == "warn" or (r["conclusion"] == "fail" and r["check"] not in blocking_checks)]
    conclusion = "error" if errors else "fail" if blocking else "incomplete" if unexecuted else "warn" if advisory else "pass"
    receipt = {"version": 2, "mode": mode, "skill": "nature-figure", "skill_version": SKILL_VERSION,
               "vendor_commit": COMMIT, "artifacts": [r[0] for r in rows], "checks": receipts, "errors": errors,
               "overall": {"execution": "partial" if unexecuted and any(r["executed"] != "not_run" for r in receipts) else "not_started" if unexecuted else "complete",
                           "conclusion": conclusion, "blocking": blocking, "advisory": advisory, "unexecuted": unexecuted,
                           "claims": ["Checks certify only listed hashes and stated coverage.", "Visual inspection was not run: no visual model capability supplied."] if visual_inspector is None else ["Visual inspector supplied explicitly."]},
               "policy": {"blocking_checks": sorted(blocking_checks)}}
    if supersedes:
        receipt["supersedes"] = supersedes
    receipt["receipt_id"] = sha256(json.dumps(receipt, sort_keys=True, ensure_ascii=False, default=str).encode())
    return receipt
