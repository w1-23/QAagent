"""Authorized SVG schematic projects: sanitize, audit, publish and edit.

F5 keeps a drawing created by ``draw_svg`` as an owner-bound figure project
without adding a second renderer or file store. The bytes are the product: the
stored SVG text is the exact sanitized markup, the account-library export is
exactly those bytes, and every revision records that SHA-256. Edits are limited
to text and presentation attributes on stable ids; geometry, data, statistics
and scientific procedures are never rewritten or recomputed.

Reuse boundaries
----------------
* sanitizer: ``sanitize_svg`` is the single sanitizer used by ``draw_svg``;
* authorization, CAS and storage: ``src.figures.projects``/``store``;
* QA: F4's ``src.figures.nature_runtime.verify_svg_bytes`` and
  ``current_security_findings``;
* publication: the existing account file library through
  ``projects.publish_rendered`` and ``src.research.artifacts.publish_artifact``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from src.figures import projects
from src.figures.projects import ProjectError

_LOG = logging.getLogger(__name__)

_NS = "http://www.w3.org/2000/svg"
_SVG_TAGS = {
    "svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline",
    "polygon", "text", "tspan", "title", "desc", "defs", "linearGradient",
    "radialGradient", "stop", "clipPath", "marker",
}
_SVG_ATTRS = {
    "viewBox", "preserveAspectRatio", "stroke-width", "stroke-linecap",
    "stroke-linejoin", "stroke-dasharray", "stroke-dashoffset", "fill-opacity",
    "stroke-opacity", "font-family", "font-size", "font-weight", "font-style",
    "text-anchor", "dominant-baseline", "gradientUnits", "gradientTransform",
    "stop-color", "stop-opacity", "clipPathUnits", "markerWidth",
    "markerHeight", "markerUnits", "marker-start", "marker-mid", "marker-end",
    "aria-label", "clip-path", "role", "id", "href", "x", "y", "x1", "y1", "x2", "y2",
    "cx", "cy", "r", "rx", "ry", "width", "height", "d", "points", "fill",
    "stroke", "opacity", "transform", "offset",
}
_FORBIDDEN_TAGS = frozenset({
    "script", "foreignObject", "iframe", "object", "embed", "handler",
    "listener", "audio", "video", "canvas",
})
_FORBIDDEN_ATTRS = frozenset({
    "src", "action", "formaction", "srcdoc", "target", "download",
    "ping", "poster", "data", "srcset",
})
_ID_RE = re.compile(r"[A-Za-z][\w:.-]{0,100}")
_LOCAL_URL_RE = re.compile(r"url\(\s*#[A-Za-z][\w:.-]{0,100}\s*\)", re.I)
_LOCAL_HREF_RE = re.compile(r"#[A-Za-z][\w:.-]{0,100}")
_ACTIVE_VALUE_RE = re.compile(r"javascript:|data:|https?:|file:|//", re.I)
_MAX_SOURCE_BYTES = 100_000
_MAX_NODES = 5000
_MAX_EDITS = 100

# ── The shared sanitizer ──────────────────────────────────────────────────

def sanitize_svg(source: str | bytes) -> str:
    """Return canonical, bounded SVG markup or raise ``ValueError``.

    The allowlist is unchanged in spirit from ``draw_svg``'s original
    sanitizer. Documents that use scripts, ``foreignObject`` or other active
    containers, event handlers, navigation attributes, external ``href``-like
    attributes or non-local ``url()`` references are rejected rather than
    silently rewritten. Unknown benign attributes are still dropped so legacy
    chat drawings keep working.
    """
    if isinstance(source, bytes):
        try:
            source = source.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("SVG must be UTF-8 text") from exc
    source = str(source or "").strip()
    if not source or len(source.encode("utf-8")) > _MAX_SOURCE_BYTES:
        raise ValueError("SVG is empty or too large")
    if re.search(r"<!DOCTYPE|<!ENTITY", source, re.I):
        raise ValueError("SVG contains a forbidden declaration")
    root = ET.fromstring(source)
    if root.tag != f"{{{_NS}}}svg":
        raise ValueError("Root element must be an SVG document")
    nodes = 0

    def clean(node: ET.Element) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > _MAX_NODES:
            raise ValueError("Unsupported SVG complexity")
        if node.tag.startswith("{"):
            namespace = node.tag.split("}", 1)[0][1:]
            if namespace != _NS:
                raise ValueError("Unsupported SVG namespace")
        tag = node.tag.split("}", 1)[-1]
        if tag in _FORBIDDEN_TAGS:
            raise ValueError(f"Forbidden SVG element: {tag}")
        if tag not in _SVG_TAGS:
            raise ValueError(f"Unsupported SVG element: {tag}")
        node.tag = tag
        for key in list(node.attrib):
            value = node.attrib[key]
            local = key.split("}", 1)[-1]
            if len(local) > 2 and local.casefold().startswith("on"):
                raise ValueError(f"Forbidden active SVG attribute: {local}")
            if local.casefold() in _FORBIDDEN_ATTRS:
                raise ValueError(f"Forbidden SVG attribute: {local}")
            if local.casefold() == "href":
                if not _LOCAL_HREF_RE.fullmatch(value):
                    raise ValueError("External SVG href reference")
                if key not in _SVG_ATTRS:
                    del node.attrib[key]
                continue
            if key not in _SVG_ATTRS:
                del node.attrib[key]
                continue
            if len(value) > 4000 or re.search(r"[<>\x00-\x1f]", value):
                del node.attrib[key]
                continue
            if key == "id" and not _ID_RE.fullmatch(value):
                del node.attrib[key]
                continue
            if re.search(r"url\s*\(", value, re.I) and not _LOCAL_URL_RE.fullmatch(value):
                raise ValueError("External or non-local SVG url() reference")
            if _ACTIVE_VALUE_RE.search(value):
                raise ValueError("External or active SVG attribute value")
        for child in list(node):
            clean(child)

    clean(root)
    if not root.get("viewBox") and not root.get("width"):
        raise ValueError("SVG must provide viewBox or width")
    root.set("xmlns", _NS)
    return ET.tostring(root, encoding="unicode", short_empty_elements=True)


# ── The closed edit vocabulary ────────────────────────────────────────────

_TEXT_TAGS = frozenset({"text", "tspan", "title", "desc"})
_PAINTABLE_TAGS = frozenset({
    "path", "rect", "circle", "ellipse", "line", "polyline", "polygon",
    "text", "tspan",
})
_EDITABLE_GROUPS = {
    "set_color": frozenset({"fill", "stroke", "stop-color"}),
    "set_stroke": frozenset({
        "stroke-width", "stroke-dasharray", "stroke-linecap", "stroke-linejoin",
    }),
    "set_font": frozenset({
        "font-size", "font-weight", "font-style", "font-family", "text-anchor",
        "dominant-baseline",
    }),
}
_ALL_EDITABLE_ATTRS = frozenset().union(*_EDITABLE_GROUPS.values())
_NUMBER_VALUE_RE = re.compile(
    r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,4})?(?:px|pt|mm|cm|in|em|%)?$")
_OPACITY_VALUE_RE = re.compile(r"^(?:0(?:\.[0-9]{1,4})?|1(?:\.0{1,4})?|0?\.[0-9]{1,4})$")
_DASHARRAY_RE = re.compile(
    r"^(?:none|[0-9]{1,4}(?:\.[0-9]{1,4})?(?:[ ,]+[0-9]{1,4}(?:\.[0-9]{1,4})?)*)$")
_COLOR_VALUE_RE = re.compile(
    r"^(?:#[0-9a-fA-F]{3,8}|none|currentColor|[A-Za-z]{1,32}"
    r"|(?:rgb|rgba|hsl|hsla)\(\s*[0-9A-Za-z.,% ]{1,60}\s*\))$")
_FONT_FAMILY_RE = re.compile(r"^[A-Za-z0-9 ,.'_\-]{1,200}$")
_FONT_SIZE_KEYWORDS = frozenset({
    "xx-small", "x-small", "small", "medium", "large", "x-large", "xx-large",
    "smaller", "larger", "inherit",
})


def list_edit_contract() -> dict[str, Any]:
    """The exact closed vocabulary shared with the API and the workspace UI."""
    return {
        "version": 1,
        "target": "unique id matching [A-Za-z][\\w:.-]{0,100}",
        "operations": {
            "set_text": {"fields": ["target", "value"],
                         "tags": sorted(_TEXT_TAGS)},
            "set_label": {"fields": ["target", "value"],
                          "equivalent_to": "set_text"},
            "set_color": {"fields": ["target", "attribute", "value"],
                          "attributes": sorted(_EDITABLE_GROUPS["set_color"]),
                          "default_attribute": "fill"},
            "set_stroke": {"fields": ["target", "attribute", "value"],
                           "attributes": sorted(_EDITABLE_GROUPS["set_stroke"])},
            "set_font": {"fields": ["target", "attribute", "value"],
                         "attributes": sorted(_EDITABLE_GROUPS["set_font"])},
            "set_attribute": {"fields": ["target", "attribute", "value"],
                              "attributes": sorted(_ALL_EDITABLE_ATTRS)},
        },
        "limits": {"max_edits": _MAX_EDITS, "max_text": 2000, "max_value": 200},
        "limitations": [
            "No geometry, coordinate, path, data, statistical or scientific-procedure edits.",
            "Text is escaped as SVG text; markup is never interpreted.",
        ],
    }


def _invalid(message: str, *, code: str = "invalid_edit") -> ProjectError:
    return ProjectError(code, message, 400)


def _validate_value(attribute: str, value: str) -> None:
    if not value or len(value) > 200 or re.search(r"[<>\x00-\x1f]", value):
        raise _invalid("修改值无效。", code="invalid_edit_value")
    if re.search(r"javascript:|data:|https?:|file:|url\s*\(", value, re.I):
        raise _invalid("修改值包含外部或活动引用。", code="invalid_edit_value")
    if attribute in {"fill", "stroke", "stop-color"}:
        if not _COLOR_VALUE_RE.fullmatch(value):
            raise _invalid("颜色值无效。", code="invalid_edit_value")
    elif attribute in {"stroke-width", "font-size"}:
        if value not in _FONT_SIZE_KEYWORDS and not _NUMBER_VALUE_RE.fullmatch(value):
            raise _invalid("尺寸值无效。", code="invalid_edit_value")
    elif attribute in {"opacity", "fill-opacity", "stroke-opacity", "stop-opacity"}:
        if not _OPACITY_VALUE_RE.fullmatch(value):
            raise _invalid("透明度值无效。", code="invalid_edit_value")
    elif attribute == "stroke-dasharray":
        if not _DASHARRAY_RE.fullmatch(value):
            raise _invalid("虚线值无效。", code="invalid_edit_value")
    elif attribute == "stroke-linecap":
        if value not in {"butt", "round", "square", "inherit"}:
            raise _invalid("线端值无效。", code="invalid_edit_value")
    elif attribute == "stroke-linejoin":
        if value not in {"miter", "round", "bevel", "arcs", "miter-clip", "inherit"}:
            raise _invalid("线接值无效。", code="invalid_edit_value")
    elif attribute == "font-family":
        if not _FONT_FAMILY_RE.fullmatch(value):
            raise _invalid("字体族值无效。", code="invalid_edit_value")
    elif attribute == "font-weight":
        if not re.fullmatch(r"normal|bold|bolder|lighter|[1-9]00|inherit", value):
            raise _invalid("字体粗细值无效。", code="invalid_edit_value")
    elif attribute == "font-style":
        if value not in {"normal", "italic", "oblique", "inherit"}:
            raise _invalid("字体样式值无效。", code="invalid_edit_value")
    elif attribute == "text-anchor":
        if value not in {"start", "middle", "end", "inherit"}:
            raise _invalid("文本对齐值无效。", code="invalid_edit_value")
    elif attribute == "dominant-baseline":
        if not re.fullmatch(r"[A-Za-z-]{1,30}", value):
            raise _invalid("基线值无效。", code="invalid_edit_value")


def normalize_edits(edits: Any) -> list[dict[str, str]]:
    """Validate and canonicalize the whole batch before any byte changes."""
    if not isinstance(edits, list) or not edits:
        raise _invalid("修改必须是非空对象列表。")
    if len(edits) > _MAX_EDITS:
        raise _invalid("单次修改项过多。")
    normalized: list[dict[str, str]] = []
    for raw in edits:
        if not isinstance(raw, dict):
            raise _invalid("每条修改必须是对象。")
        op = str(raw.get("op") or "").strip()
        target = str(raw.get("target") or "").strip()
        if not _ID_RE.fullmatch(target):
            raise _invalid("target 必须是安全稳定的 id。")
        if op in {"set_text", "set_label"}:
            if set(raw) - {"op", "target", "value"}:
                raise _invalid("文本修改包含不支持的字段。")
            value = raw.get("value")
            if not isinstance(value, str):
                raise _invalid("文本值必须是字符串。")
            value = value.strip()
            if not value or len(value) > 2000 or re.search(r"[\x00-\x1f]", value):
                raise _invalid("文本值无效。", code="invalid_edit_value")
            normalized.append({"op": "set_text", "target": target, "value": value})
            continue
        if op not in _EDITABLE_GROUPS and op != "set_attribute":
            raise _invalid(f"不支持的修改操作：{op or '(empty)'}。")
        allowed_fields = {"op", "target", "attribute", "value"}
        if set(raw) - allowed_fields:
            raise _invalid("属性修改包含不支持的字段。")
        attribute = str(raw.get("attribute") or "").strip()
        if not attribute and op == "set_color":
            attribute = "fill"
        group = _ALL_EDITABLE_ATTRS if op == "set_attribute" else _EDITABLE_GROUPS[op]
        if attribute not in group:
            raise _invalid(f"操作 {op} 不支持属性 {attribute or '(empty)'}。")
        value = raw.get("value")
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            raise _invalid("属性值必须是字符串或数字。", code="invalid_edit_value")
        text = str(value).strip()
        _validate_value(attribute, text)
        normalized.append({"op": op, "target": target,
                           "attribute": attribute, "value": text})
    return normalized


def _tag_allows(tag: str, attribute: str) -> bool:
    if attribute in {"stop-color", "stop-opacity"}:
        return tag == "stop"
    if attribute in {"font-size", "font-weight", "font-style", "font-family",
                     "text-anchor", "dominant-baseline"}:
        return tag in {"text", "tspan"}
    return tag in _PAINTABLE_TAGS


def editable_svg_targets(svg_text: str) -> list[dict[str, Any]]:
    """Expose editable object values without returning any SVG markup."""
    root = ET.fromstring(svg_text)
    by_id = {}
    duplicates = set()
    for node in root.iter():
        target = node.get("id") or ""
        if not _ID_RE.fullmatch(target):
            continue
        if target in by_id:
            duplicates.add(target)
        by_id[target] = node
    targets = []
    for target, node in by_id.items():
        if target in duplicates:
            continue
        tag = node.tag.split("}", 1)[-1]
        attributes = {key: node.get(key) for key in sorted(_ALL_EDITABLE_ATTRS)
                      if _tag_allows(tag, key)}
        can_edit_text = tag in _TEXT_TAGS and not len(node)
        if attributes or can_edit_text:
            targets.append({"id": target, "tag": tag, "attributes": attributes,
                            "text": (node.text or "") if can_edit_text else None})
    return targets


def apply_svg_edits(svg_text: str, edits: Any) -> str:
    """Apply a validated batch to one SVG document and re-sanitize the result."""
    normalized = normalize_edits(edits)
    safe = sanitize_svg(svg_text)
    try:
        root = ET.fromstring(safe)
    except ET.ParseError as exc:
        raise _invalid("SVG 无法解析。", code="svg_invalid") from exc
    by_id: dict[str, list[ET.Element]] = {}
    for node in root.iter():
        node_id = node.get("id")
        if node_id:
            by_id.setdefault(node_id, []).append(node)
    for edit in normalized:
        matches = by_id.get(edit["target"], [])
        if not matches:
            raise ProjectError("unknown_target",
                               f"找不到 id 为 {edit['target']} 的对象。", 400)
        if len(matches) != 1:
            raise ProjectError("ambiguous_target",
                               f"id {edit['target']} 不唯一，无法安全编辑。", 400)
        node = matches[0]
        tag = node.tag.split("}", 1)[-1]
        if edit["op"] == "set_text":
            if tag not in _TEXT_TAGS:
                raise ProjectError("unsupported_edit",
                                   "文本只能写入 text/tspan/title/desc 对象。", 400)
            if len(list(node)):
                raise ProjectError("unsupported_edit",
                                   "目标文本对象包含子元素，不能整体替换。", 400)
            node.text = edit["value"]
            continue
        attribute = edit["attribute"]
        if not _tag_allows(tag, attribute):
            raise ProjectError(
                "unsupported_edit", f"元素 {tag} 不支持属性 {attribute}。", 400)
        node.set(attribute, edit["value"])
    serialized = ET.tostring(root, encoding="unicode", short_empty_elements=True)
    try:
        return sanitize_svg(serialized)
    except (ET.ParseError, ValueError) as exc:
        raise ProjectError("svg_invalid", f"编辑后的 SVG 未通过安全校验：{exc}。", 400) from exc


# ── Project persistence ───────────────────────────────────────────────────

def _valid_owner(owner: Any) -> int:
    try:
        value = int(owner)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        raise ProjectError("unauthorized", "需要已授权的账号才能保存图件。", 401)
    return value


def _require_session(owner: int, session_id: Any) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise ProjectError("session_unavailable", "没有有效会话，无法保存图件。", 409)
    from src.session.manager import session_manager
    if not session_manager.session_belongs_to(sid, int(owner)):
        raise ProjectError("session_unavailable", "当前会话不可用或不属于该账号。", 409)
    return sid


def _label(value: Any) -> str:
    text = re.sub(r"[\r\n<>]", " ", str(value or "图形")).strip()[:160]
    return text or "图形"


def _verify_svg(blob: bytes, *, mode: str, supersedes: str | None = None,
                vendor_root: Any = None) -> tuple[dict, list]:
    from src.figures.nature_runtime import (current_security_findings,
                                            verify_svg_bytes)
    receipt = verify_svg_bytes(blob, mode=mode, supersedes=supersedes,
                               vendor_root=vendor_root)
    return receipt, current_security_findings(receipt)


def _reject_security(security: list) -> None:
    if security:
        kinds = ",".join(sorted({str(item.get("kind") or "security")
                                 for item in security}))
        raise ProjectError("svg_security",
                           f"SVG 安全检查未通过：{kinds}。", 400)


def _links(figure_id: str, revision: int, exports: Any) -> dict[str, Any]:
    entry = exports.get("svg") if isinstance(exports, dict) else None
    file_entry: dict[str, Any] = {}
    if isinstance(entry, dict) and entry.get("file_id"):
        for key in ("file_id", "filename", "sha256", "size",
                    "download_url", "artifact_url"):
            if entry.get(key) is not None:
                file_entry[key] = entry[key]
    return {
        "figure_id": str(figure_id),
        "revision": int(revision),
        "file": file_entry,
        "project_api_url": f"/api/v2/figures/projects/{figure_id}",
        "workbench_url": f"/figures?project={figure_id}",
    }


def _read_current_export(project: dict[str, Any], owner: int) -> bytes:
    exports = project.get("exports_json") or {}
    entry = exports.get("svg") if isinstance(exports, dict) else None
    if not isinstance(entry, dict) or not entry.get("file_id"):
        raise ProjectError("artifact_unavailable", "图件当前没有可读取的 SVG 文件。", 404)
    from src.session import files as session_files
    info = session_files.get_account_file(str(entry["file_id"]), int(owner))
    if not info or not info.get("path"):
        raise ProjectError("artifact_unavailable", "图件当前文件不存在或无权访问。", 404)
    path = Path(str(info["path"]))
    if not path.is_file() or path.is_symlink():
        raise ProjectError("artifact_unavailable", "图件当前文件不可读取。", 404)
    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise ProjectError("artifact_unavailable", "图件当前文件不可读取。", 404) from exc
    digest = hashlib.sha256(blob).hexdigest()
    if not entry.get("sha256") or digest != entry.get("sha256"):
        raise ProjectError("artifact_changed", "图件当前文件摘要不匹配。", 409)
    return blob


def _publish_receipt(owner: int, session_id: str, filename: str,
                     receipt: dict[str, Any]) -> dict[str, Any]:
    from src.core.tracing import get_client_request_id
    from src.research.artifacts import publish_artifact
    blob = (json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2,
                       allow_nan=False) + "\n").encode("utf-8")
    try:
        entry = publish_artifact(
            owner=owner, session_id=session_id, filename=filename, content=blob,
            mime="application/json", producer_tool="svg_current_artifact_qa",
            request_id=str(get_client_request_id() or ""))
    except Exception:
        raise ProjectError("publish_failed", "检查回执发布失败，未提供下载链接。", 503) from None
    if (not isinstance(entry, dict) or not entry.get("file_id")
            or not entry.get("download_url")
            or entry.get("sha256") != hashlib.sha256(blob).hexdigest()):
        raise ProjectError("publish_failed", "已发布回执校验失败，未提供下载链接。", 503)
    return entry


def create_svg_project(*, owner: Any, session_id: Any, svg: Any,
                       alt_text: Any = "图形", title: Any = "",
                       vendor_root: Any = None) -> dict[str, Any]:
    """Sanitize, audit and publish one authorized SVG as a figure project."""
    owner = _valid_owner(owner)
    sid = _require_session(owner, session_id)
    try:
        safe = sanitize_svg(svg)
    except (ET.ParseError, ValueError) as exc:
        raise ProjectError("svg_invalid", f"SVG 未通过安全校验：{exc}。", 400) from exc
    blob = safe.encode("utf-8")
    receipt, security = _verify_svg(blob, mode="initial", vendor_root=vendor_root)
    _reject_security(security)
    digest = hashlib.sha256(blob).hexdigest()
    label = _label(alt_text)
    name = str(title or label or "svg-figure").strip()[:120] or "svg-figure"
    source = {
        "kind": "svg",
        "svg": safe,
        "alt": label,
        "svg_sha256": digest,
        "svg_bytes": len(blob),
        "accepted_edits": [],
        "edit_contract": list_edit_contract(),
    }
    manifest = {
        "version": 1,
        "figure_kind": "svg_schematic",
        "svg_sha256": digest,
        "svg_bytes": len(blob),
        "initial_svg_receipt": receipt,
        "initial_qa_receipt": receipt,
        "current_qa_receipt": receipt,
        "edit_provenance": {
            "version": 1,
            "mode": "initial",
            "edits": [],
            "initial_receipt_id": receipt.get("receipt_id"),
            "current_receipt_id": receipt.get("receipt_id"),
        },
        "claims": [
            "Only text and presentation attributes on stable ids are editable; "
            "no numerical geometry, statistics or scientific procedure is recomputed.",
            "只有稳定 id 上的文字和有限表现属性可编辑；未重算数值几何、统计或科学流程。",
        ],
    }
    project = projects.create_project(
        owner_user_id=owner, title=name, source_kind="svg", source=source,
        session_id=sid)
    figure_id = str(project.get("figure_id") or "")
    if not figure_id:
        raise ProjectError("project_failed", "图件项目创建失败。", 500)
    published: dict[str, Any] = {}
    try:
        published = projects.publish_rendered(owner, sid, name, {"svg": blob})
        entry = published.get("svg") if isinstance(published, dict) else None
        if not isinstance(entry, dict) or entry.get("sha256") != digest:
            raise ProjectError("publish_failed",
                               "已发布 SVG 摘要与安全检查字节不一致。", 503)
        committed = projects.update_project(
            figure_id, owner, expected_revision=0, manifest_json=manifest,
            exports_json=published, overrides_json=[], render_version=1,
            status="ready", last_error="")
        if not committed:
            raise ProjectError("revision_conflict", "保存期间图件已更新，请重新生成。", 409)
    except BaseException:
        # No project row without its real export; an unavailable publisher must
        # never leave a half-created, linkless project behind.
        try:
            projects.delete_project(figure_id, owner)
        except Exception:
            _LOG.warning("svg project rollback failed", exc_info=True)
        raise
    revision = 1
    return {
        "figure_id": figure_id,
        "title": name,
        "render_version": revision,
        "svg_sha256": digest,
        "exports": published,
        "links": _links(figure_id, revision, published),
        "initial_receipt_id": receipt.get("receipt_id"),
        "current_receipt_id": receipt.get("receipt_id"),
        "receipt": receipt,
    }


def apply_svg_project_edits(*, figure_id: Any, owner: Any, edits: Any,
                            expected_revision: Any, session_id: Any = "",
                            vendor_root: Any = None) -> dict[str, Any]:
    """Apply supported edits and commit a new revision with product CAS."""
    owner = _valid_owner(owner)
    project = projects.get_for_owner(str(figure_id), owner)
    if project.get("source_kind") != "svg":
        raise ProjectError("not_svg_project", "该图件不是 SVG 图件。", 400)
    revision = int(project.get("render_version") or 0)
    if int(expected_revision) != revision:
        raise ProjectError("revision_conflict",
                           "图件已在其他页面更新，请重新加载后合并修改。", 409)
    sid = _require_session(owner, session_id or project.get("session_id", ""))
    source = project.get("source_json") or {}
    if source.get("kind") != "svg" or not isinstance(source.get("svg"), str):
        raise ProjectError("source_unavailable", "图件缺少可编辑的安全 SVG 源。", 409)
    previous_blob = _read_current_export(project, owner)
    if hashlib.sha256(previous_blob).hexdigest() != source.get("svg_sha256"):
        raise ProjectError("artifact_changed",
                           "当前导出与保存的 SVG 源摘要不一致。", 409)
    normalized = normalize_edits(edits)
    try:
        new_text = apply_svg_edits(source["svg"], normalized)
    except ProjectError:
        raise
    except (ET.ParseError, ValueError) as exc:
        raise ProjectError("svg_invalid",
                           f"编辑后的 SVG 未通过安全校验：{exc}。", 400) from exc
    new_blob = new_text.encode("utf-8")
    if new_blob == previous_blob:
        raise ProjectError("no_changes", "修改没有改变图件内容。", 400)
    previous_manifest = project.get("manifest_json") or {}
    previous_receipt = previous_manifest.get("current_qa_receipt") or {}
    previous_receipt_id = (previous_receipt.get("receipt_id")
                           if isinstance(previous_receipt, dict) else None)
    receipt, security = _verify_svg(new_blob, mode="edit", supersedes=previous_receipt_id,
                                    vendor_root=vendor_root)
    _reject_security(security)
    digest = hashlib.sha256(new_blob).hexdigest()
    accepted_by_property = {(item["target"], item.get("attribute")): item
                            for item in source.get("accepted_edits", [])}
    for item in normalized:
        accepted_by_property[(item["target"], item.get("attribute"))] = item
    accepted = list(accepted_by_property.values())
    new_source = {**source, "svg": new_text, "svg_sha256": digest,
                  "svg_bytes": len(new_blob), "accepted_edits": accepted}
    initial = previous_manifest.get("initial_svg_receipt",
                                    previous_manifest.get("initial_qa_receipt"))
    manifest = dict(previous_manifest)
    manifest.update({
        "version": 1,
        "figure_kind": "svg_schematic",
        "svg_sha256": digest,
        "svg_bytes": len(new_blob),
        "current_qa_receipt": receipt,
        "edit_provenance": {
            "version": 1,
            "mode": "edit",
            "edits": accepted,
            "initial_receipt_id": (initial.get("receipt_id")
                                   if isinstance(initial, dict) else None),
            "current_receipt_id": receipt.get("receipt_id"),
        },
    })
    if isinstance(initial, dict):
        manifest["initial_svg_receipt"] = initial
        manifest["initial_qa_receipt"] = initial
    for key in ("initial_nature_skill_receipt",):
        if key in previous_manifest:
            manifest[key] = previous_manifest[key]
    published: dict[str, Any] = {}
    try:
        published = projects.publish_rendered(
            owner, sid, str(project.get("title") or "figure"), {"svg": new_blob})
        entry = published.get("svg") if isinstance(published, dict) else None
        if not isinstance(entry, dict) or entry.get("sha256") != digest:
            raise ProjectError("publish_failed",
                               "已发布 SVG 摘要与编辑后字节不一致。", 503)
        committed = projects.update_project(
            str(figure_id), owner, expected_revision=revision,
            source_json=new_source, manifest_json=manifest,
            overrides_json=accepted, exports_json=published,
            render_version=revision + 1, status="ready", last_error="")
        if not committed:
            raise ProjectError("revision_conflict",
                               "保存期间图件已更新或删除，当前版本未被覆盖。", 409)
    except BaseException:
        # The project row still points at its previous verified export. A new
        # immutable file is never linked from a failed save.
        raise
    new_revision = revision + 1
    return {
        "figure_id": str(figure_id),
        "render_version": new_revision,
        "svg_sha256": digest,
        "exports": published,
        "links": _links(figure_id, new_revision, published),
        "changed": [item["target"] for item in normalized],
        "accepted_edits": accepted,
        "svg_targets": editable_svg_targets(new_text),
        "initial_receipt_id": (initial.get("receipt_id")
                               if isinstance(initial, dict) else None),
        "current_receipt_id": receipt.get("receipt_id"),
        "receipt": receipt,
    }


def reaudit_svg_project(*, figure_id: Any, owner: Any, expected_revision: Any,
                        supersedes_receipt_id: str | None = None,
                        publish_receipt: bool = False, session_id: Any = "",
                        vendor_root: Any = None) -> dict[str, Any]:
    """Audit the exact current export bytes through F4's SVG entry.

    Reauditing never rewrites the project or rerenders the figure. When
    ``publish_receipt`` is set, the receipt JSON becomes an ordinary account
    file with a real download link; a failed publication returns no link.
    """
    owner = _valid_owner(owner)
    project = projects.get_for_owner(str(figure_id), owner)
    if project.get("source_kind") != "svg":
        raise ProjectError("not_svg_project", "该图件不是 SVG 图件。", 400)
    revision = int(project.get("render_version") or 0)
    if int(expected_revision) != revision:
        raise ProjectError("revision_conflict",
                           "图件已更新，请重新加载当前版本。", 409)
    sid = _require_session(owner, session_id or project.get("session_id", ""))
    source = project.get("source_json") or {}
    blob = _read_current_export(project, owner)
    digest = hashlib.sha256(blob).hexdigest()
    if digest != source.get("svg_sha256"):
        raise ProjectError("artifact_changed",
                           "当前导出与保存的 SVG 源摘要不一致。", 409)
    manifest = project.get("manifest_json") or {}
    current = manifest.get("current_qa_receipt") or {}
    supersedes = supersedes_receipt_id or (
        current.get("receipt_id") if isinstance(current, dict) else None)
    receipt, security = _verify_svg(blob, mode="reaudit", supersedes=supersedes,
                                    vendor_root=vendor_root)
    _reject_security(security)
    result: dict[str, Any] = {
        "figure_id": str(figure_id),
        "render_version": revision,
        "svg_sha256": digest,
        "receipt": receipt,
        "receipt_id": receipt.get("receipt_id"),
        "supersedes": supersedes,
        "links": _links(figure_id, revision, project.get("exports_json") or {}),
    }
    if publish_receipt:
        result["receipt_file"] = _publish_receipt(
            owner, sid,
            f"figure_{figure_id}_r{revision}_svg_receipt.json", receipt)
        result["links"]["receipt_file"] = result["receipt_file"]
    return result


__all__ = [
    "SOURCE_KIND", "apply_svg_edits", "apply_svg_project_edits",
    "create_svg_project", "list_edit_contract", "normalize_edits",
    "reaudit_svg_project", "sanitize_svg",
]

SOURCE_KIND = "svg"
