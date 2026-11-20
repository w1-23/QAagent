"""Scientific figures, frozen in-process style revisions, and portable bundles."""
from __future__ import annotations
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any
from .core import RenderedFigure, build_resolved_figure, capture_figure, compile_spec
from .dataset_codec import encode_frame, decode_frame
from .frozen_render import render_receipt, typed_payload_sha256, geometry_signature
from .manifest import assign_locators, build_manifest
from .overrides import apply_overrides, validate_overrides
from .render_policy import export_figure
from .resolve import resolve_spec
from .runtime import render_context
from .spec import parse_spec, ResolvedFigure, Series, StyleSpec
from .verification import sha256
from .nature_runtime import COMMIT, SKILL_VERSION

VERSION = "2.0.0"


def _json(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _parameters(value):
    return json.loads(_json(value or {}))


@dataclass
class FigureSession:
    rendered: RenderedFigure
    source: str
    data: dict = field(default_factory=dict)
    parameters: dict = field(default_factory=dict)
    resolved: dict | None = None
    specification: dict | None = None
    provenance: dict = field(default_factory=dict)
    frozen: dict = field(default_factory=dict)
    revision: int = 1
    overrides: list = field(default_factory=list)


def render_figure(*, spec=None, source=None, data=None, parameters=None, tables=None):
    """Render a real spec or caller-authorized Python. This is not a sandbox.

    Source gets explicit `data` DataFrames and `parameters`. It must be trusted
    by the caller, and may have normal Python side effects when invoked.
    Importing this package never executes source or initializes Matplotlib.
    """
    if (spec is None) == (source is None):
        raise ValueError("provide exactly one of spec or source")
    parameters = _parameters(parameters)
    payloads = {str(name): encode_frame(frame) for name, frame in (data or {}).items()}
    frames = {name: decode_frame(payload) for name, payload in payloads.items()}
    provenance = {}
    resolved_payload = specification = None
    if spec is not None:
        validated = parse_spec(spec)
        resolved, provenance = resolve_spec(validated, tables=tables)
        rendered = build_resolved_figure(resolved)
        source = compile_spec(resolved)
        resolved_payload, specification = resolved.as_dict(), validated.as_dict()
        for index, (key, frame) in enumerate((tables or {}).items()):
            payloads[f"table_{index}"] = encode_frame(frame)
        provenance["table_bindings"] = [{"key": list(key), "dataset": f"table_{index}"} for index, key in enumerate(tables or {})]
    else:
        if not isinstance(source, str):
            raise ValueError("source must be UTF-8 text")
        with render_context():
            result = capture_figure(source, namespace={"data": frames, "parameters": parameters}, check_layout=False)
            if result.error:
                raise ValueError(f"{result.error_type}: {result.error}")
            if result.figures_seen != 1:
                raise ValueError("source must create exactly one figure")
            import matplotlib
            rendered = RenderedFigure(result.figure, list(result.figure.axes), rc_params=dict(matplotlib.rcParams))
        provenance = {"source_execution": "caller_authorized_local_python", "sandboxed": False}
    assign_locators(rendered)
    with render_context(rendered.rc_params):
        rendered.figure.canvas.draw()
        frozen = render_receipt(source, payloads, rendered.figure)
    frozen["parameters_sha256"] = sha256(_json(parameters))
    frozen["spec_sha256"] = sha256(_json(specification)) if specification is not None else None
    frozen["resolved_sha256"] = sha256(_json(resolved_payload)) if resolved_payload is not None else None
    return FigureSession(rendered, source, payloads, parameters, resolved_payload, specification, provenance, frozen)


def _check_frozen_inputs(session):
    if (sha256(session.source.encode()) != session.frozen["renderer_sha256"] or
        sha256(_json(session.parameters)) != session.frozen["parameters_sha256"] or
        {name: typed_payload_sha256(payload) for name, payload in session.data.items()} != session.frozen["data_sha256"] or
        (sha256(_json(session.specification)) if session.specification is not None else None) != session.frozen["spec_sha256"] or
        (sha256(_json(session.resolved)) if session.resolved is not None else None) != session.frozen["resolved_sha256"]):
        raise ValueError("computation_changed: session inputs were mutated")


def revise_figure(session, *, overrides, source=None, data=None, parameters=None):
    """Apply styles to the existing figure without rerunning source/statistics.

    If computation inputs changed, create a new render session instead. Frozen
    signatures retain their narrow supported-artist coverage from the host.
    """
    if source is not None and sha256(source.encode()) != session.frozen["renderer_sha256"]:
        raise ValueError("computation_changed: source requires a new render")
    if parameters is not None and sha256(_json(_parameters(parameters))) != session.frozen["parameters_sha256"]:
        raise ValueError("computation_changed: parameters require a new render")
    if data is not None:
        hashes = {str(name): typed_payload_sha256(encode_frame(frame)) for name, frame in data.items()}
        if hashes != session.frozen["data_sha256"]:
            raise ValueError("computation_changed: data requires a new render")
    # Also detect mutation of a session's public data/parameters/source fields.
    _check_frozen_inputs(session)
    manifest = build_manifest(session.rendered, source="spec" if session.resolved else "script")
    validated = validate_overrides(overrides, manifest)
    with render_context(session.rendered.rc_params):
        apply_overrides(session.rendered, manifest, validated)
    session.overrides.extend(validated)
    session.revision += 1
    session.provenance["style_revision"] = {"source_reexecuted": False, "statistics_recomputed": False,
                                            "frozen_input_receipt": session.frozen,
                                            "current_geometry": geometry_signature(session.rendered.figure)}
    return session


def _reproduction_source(session):
    # Both branches save their own outputs on direct execution. The script
    # invokes only local library code and explicit bundle inputs.
    original = session.source
    return '\n'.join([
        '"""Reproduce this bundle with citrus_figures installed. Run trusted source only."""',
        'import json', 'from pathlib import Path',
        'from citrus_figures import render_figure, revise_figure',
        'from citrus_figures.dataset_codec import decode_frame',
        'from citrus_figures.exporter import export_all',
        'root = Path(__file__).resolve().parent',
        'inputs = json.loads((root / "data/inputs.json").read_text(encoding="utf-8"))',
        'parameters = json.loads((root / "data/parameters.json").read_text(encoding="utf-8"))',
        'data = {name: decode_frame(json.loads((root / relative).read_text(encoding="utf-8"))) for name, relative in inputs.items()}',
        f'session = render_figure(source={original!r}, data=data, parameters=parameters)',
        f'overrides = {session.overrides!r}',
        'if overrides: revise_figure(session, overrides=overrides)',
        'for fmt, blob in export_all(session.rendered).items():',
        '    (root / ("reproduced." + fmt)).write_bytes(blob)',
        '',
    ])


def write_bundle(session, output, *, vendor_root, formats=("pdf", "svg", "png"),
                 mode=None, checks=None, policy=None, supersedes=None):
    """Write a new directory; never overwrite a source or an older receipt."""
    _check_frozen_inputs(session)
    mode = mode or ("edit" if session.revision > 1 else "initial")
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError("bundle output must be a new directory")
    blobs, receipt, layout = export_figure(session.rendered, source=session.source,
                                          formats=formats, mode=mode, vendor_root=vendor_root,
                                          checks=checks, policy=policy, supersedes=supersedes)
    members = {"figure." + fmt: blob for fmt, blob in blobs.items()}
    members["plot.py"] = _reproduction_source(session).encode("utf-8")
    members["source.py"] = session.source.encode("utf-8")
    inputs = {}
    for index, (name, payload) in enumerate(session.data.items()):
        path = f"data/dataset_{index}.json"
        members[path] = _json(payload)
        inputs[name] = path
    members["data/inputs.json"] = _json(inputs)
    members["data/parameters.json"] = _json(session.parameters)
    members["data/provenance.json"] = _json({"frozen": session.frozen, "resolution": session.provenance})
    if session.specification is not None:
        members["data/spec.json"] = _json(session.specification)
        members["data/resolved.json"] = _json(session.resolved)
    members["qa/receipt.json"] = _json(receipt)
    for index, check in enumerate(receipt["checks"]):
        members[f"qa/reports/{index:02d}_{check['check']}.json"] = _json(check)
    if layout is not None:
        members["qa/layout.json"] = _json(layout)
    inventory = build_manifest(session.rendered, source="spec" if session.resolved else "script")
    members["objects.json"] = _json(inventory)
    limits = ["Only properties listed per object in objects.json are editable.",
              "Arbitrary Python executes with caller permissions; it is not sandboxed.",
              "External files and Python dependencies referenced by arbitrary source must be supplied separately.",
              "Style edits reuse the in-process figure; reopening arbitrary-source bundles requires explicit source execution.",
              "Geometry equivalence covers only artists supported by frozen_render; unsupported artists retain explicit coverage gaps.",
              "No visual model is initialized; inspect receipt for unexecuted checks."]
    manifest = {"schema_version": 2, "package": "citrus_figures", "package_version": VERSION,
                "vendor": {"commit": COMMIT, "skill_version": SKILL_VERSION},
                "revision": session.revision, "mode": mode, "overrides": session.overrides,
                "receipt_id": receipt["receipt_id"], "editable_limitations": limits,
                "source_sha256": session.frozen["renderer_sha256"], "parameters_sha256": session.frozen["parameters_sha256"],
                "data_sha256": session.frozen["data_sha256"],
                "members": [{"path": name, "sha256": sha256(blob), "size": len(blob)} for name, blob in sorted(members.items())],
                "manifest_self_hash": "excluded to avoid circular hashing"}
    # Everything is prepared before publishing a directory; a failed check is
    # preserved as an actual receipt in the diagnostic bundle, never a pass.
    output.mkdir(parents=True, exist_ok=False)
    for name, blob in members.items():
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(blob)
    (output / "figure.bundle.json").write_bytes(_json(manifest))
    return {"path": str(output), "manifest": manifest, "receipt": receipt}


def _verified_members(root, manifest):
    """Verify declared member paths, containment, sizes and SHA-256 digests."""
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 2:
        raise ValueError("bundle manifest schema mismatch")
    verified = {}
    for member in manifest.get("members") or ():
        if not isinstance(member, dict):
            raise ValueError("bundle member entry invalid")
        name = member.get("path")
        if not isinstance(name, str) or not name or name in verified:
            raise ValueError("bundle member path mismatch")
        target = (root / name).resolve(strict=True)
        if not target.is_relative_to(root):
            raise ValueError("bundle member path mismatch")
        blob = target.read_bytes()
        if sha256(blob) != member.get("sha256") or len(blob) != member.get("size"):
            raise ValueError("bundle member hash mismatch")
        verified[name] = blob
    return verified


def load_spec_bundle(path):
    """Rebuild from frozen resolved values; never recompute grouped statistics.

    General source bundles require an existing FigureSession for frozen edits.
    This explicit limitation avoids unsafe pickle loading or silent execution.
    """
    root = Path(path).resolve(strict=True)
    manifest = json.loads((root / "figure.bundle.json").read_bytes())
    verified = _verified_members(root, manifest)
    def read_json(name):
        if name not in verified:
            raise ValueError("bundle member not declared: " + str(name))
        return json.loads(verified[name])
    if "data/resolved.json" not in verified:
        raise ValueError("frozen_session_required: arbitrary-source edits require the existing in-process FigureSession")
    def decode(payload):
        payload = dict(payload)
        payload["series"] = tuple(Series(**item) for item in payload["series"])
        payload["style"] = StyleSpec(**payload["style"])
        payload["panels"] = tuple(decode(p) for p in payload.get("panels", []))
        return ResolvedFigure(**payload)
    resolved = read_json("data/resolved.json")
    rendered = assign_locators(build_resolved_figure(decode(resolved)))
    provenance = read_json("data/provenance.json")
    inputs = read_json("data/inputs.json")
    session = FigureSession(rendered, verified["source.py"].decode("utf-8"),
                            {name: read_json(relative) for name, relative in inputs.items()},
                            read_json("data/parameters.json"), resolved,
                            read_json("data/spec.json"), provenance["resolution"], provenance["frozen"], manifest["revision"])
    if manifest["overrides"]:
        saved_revision = session.revision
        revise_figure(session, overrides=manifest["overrides"])
        session.revision = saved_revision
    return session


def verify_saved_bundle(path, *, mode="reaudit", supersedes=None, source=None,
                        vendor_root=None, checks=None, policy=None):
    """Audit a saved bundle's stored bytes; never render, re-export or run source.

    ``qa/layout.json`` is trusted only when it is a declared, hash-verified
    member and its ``artifact_sha256`` equals the stored ``figure.pdf`` digest.
    Bundles written before layout capture keep ``panel_alignment`` as an
    explicit not_run entry instead of gaining evidence from a rerender. A
    superseding receipt must name the real prior receipt identity, as required
    by the reaudit contract.
    """
    root = Path(path).resolve(strict=True)
    manifest_bytes = (root / "figure.bundle.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    verified = _verified_members(root, manifest)
    artifacts = [{"role": "figure", "format": fmt, "bytes": verified["figure." + fmt]}
                 for fmt in ("pdf", "svg", "png", "tiff")
                 if "figure." + fmt in verified]
    if not artifacts:
        raise ValueError("bundle contains no auditable figure artifact")
    formats = {row["format"] for row in artifacts}
    pdf = verified.get("figure.pdf")
    stored = None
    if "qa/layout.json" in verified:
        try:
            candidate = json.loads(verified["qa/layout.json"])
        except ValueError:
            candidate = None
        if (isinstance(candidate, dict) and isinstance(candidate.get("layout"), dict)
                and isinstance(candidate.get("artifact_sha256"), str)):
            stored = candidate
    layout_manifest = None
    captured_sha256 = stored.get("artifact_sha256") if stored else None
    pdf_sha256 = sha256(pdf) if pdf is not None else None
    if stored is not None and pdf_sha256 is not None and captured_sha256 == pdf_sha256:
        layout_manifest = {"artifact_sha256": pdf_sha256, "layout": stored["layout"]}
        binding = "bound_to_current_pdf"
    else:
        binding = "unbound" if stored is not None else "absent"
    if source is None and "source.py" in verified:
        source = verified["source.py"].decode("utf-8")
    if checks is None:
        checks = ["format_integrity", "source_preflight", "panel_alignment"]
        if "pdf" in formats:
            checks.extend(("pdf_text", "pdf_collisions"))
        if "svg" in formats:
            checks.append("svg_schematic")
        checks.append("visual")
    from .verification import verify_current_figure
    receipt = verify_current_figure(artifacts=artifacts, source=source,
                                    layout_manifest=layout_manifest, mode=mode,
                                    checks=checks, policy=policy,
                                    vendor_root=vendor_root, supersedes=supersedes)
    receipt["bundle"] = {
        "manifest_sha256": sha256(manifest_bytes),
        "bundle_kind": str(manifest.get("bundle_kind") or "frozen_session"),
        "member_count": len(verified),
        "layout_binding": binding,
        "source_origin": "bundle_member:source.py" if source is not None else None}
    receipt["layout"] = {
        "binding": binding, "artifact_sha256": pdf_sha256,
        "captured_artifact_sha256": captured_sha256,
        "capture": stored.get("capture") if stored else None, "rerendered": False,
        "layout": layout_manifest["layout"] if layout_manifest else None}
    receipt["receipt_id"] = sha256(json.dumps(
        {key: value for key, value in receipt.items() if key != "receipt_id"},
        sort_keys=True, ensure_ascii=False, default=str).encode())
    return receipt
