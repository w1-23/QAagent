"""Safe declarative SVG drawing tool available when enabled or explicitly requested."""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET

from langchain_core.tools import tool

from src.core import figures
from src.figures.svg_projects import sanitize_svg

# Kept as a module alias for existing callers and tests that imported the
# sanitizer from this tool before the shared F5 module owned it.
_sanitize_svg = sanitize_svg

_MAX_ATTEMPTS_PER_REQUEST = 4
_LOG = logging.getLogger(__name__)


def _persist_svg(safe: str, label: str) -> dict | None:
    """Save an authorized drawing without turning a chat answer into an error.

    Persistence is optional by design: a direct tool call, a test without an
    authenticated request context, or a storage outage must still yield the
    exact same ``[[FIGURE:n]]`` chat placeholder. Only a successful save adds
    links; a failure never fabricates one.
    """
    try:
        from src.core import tracing
        owner = tracing.get_user_id()
        session_id = tracing.get_session_id()
        if not owner or not session_id:
            return None
        from src.figures import svg_projects
        return svg_projects.create_svg_project(
            owner=owner, session_id=session_id, svg=safe, alt_text=label)
    except Exception:
        _LOG.warning("draw_svg project persistence failed", exc_info=True)
        return None


def _with_project_links(text: str, label: str, saved: dict | None) -> str:
    base = text + f"（图示说明：{label}）"
    if not isinstance(saved, dict):
        return base
    links = saved.get("links") if isinstance(saved.get("links"), dict) else {}
    file_entry = links.get("file") if isinstance(links.get("file"), dict) else {}
    url = str(file_entry.get("download_url") or file_entry.get("artifact_url") or "")
    parts = [base, f"已保存为图件 {saved.get('figure_id')}（版本 {saved.get('render_version')}）。"]
    if url:
        parts.append(f"下载：{url}")
    if links.get("workbench_url"):
        parts.append(f"工作台：{links['workbench_url']}")
    return " ".join(parts)


@tool
def draw_svg(svg: str = "", alt_text: str = "图形") -> str:
    """Validate and register an SVG diagram, returning a short figure placeholder.

    Call this at most once per diagram. The validated markup is stored for the
    request, so the answer only needs the `[[FIGURE:n]]` placeholder.
    """
    attempt = figures.bump_attempts()
    if attempt > _MAX_ATTEMPTS_PER_REQUEST:
        return ("[SVG_ATTEMPT_LIMIT] 本次请求已多次提交 SVG 校验，停止重试。"
                "请在回答中直接说明该图未能生成，并给出文字版结构说明，不要再调用本工具。")
    source = str(svg or "").strip()
    fenced = re.fullmatch(r"```(?:svg|xml)?\s*([\s\S]*?)\s*```", source, re.I)
    if fenced:
        source = fenced.group(1).strip()
    else:
        start = source.lower().find("<svg")
        end = source.lower().rfind("</svg>")
        if start >= 0 and end >= start:
            source = source[start:end + len("</svg>")]
    if not source:
        return "[SVG_INPUT_MISSING] 请提供完整的 SVG 文档；需要以 <svg> 开始并以 </svg> 结束。"
    try:
        safe = sanitize_svg(source)
    except (ET.ParseError, ValueError) as exc:
        return f"[SVG_FORMAT_ERROR] SVG 未通过安全校验：{exc}。请修正结构后重试。"
    label = re.sub(r"[\r\n<>]", " ", str(alt_text or "图形")).strip()[:160] or "图形"
    saved = _persist_svg(safe, label)
    links = (saved or {}).get("links") or {}
    _index, _text = figures.register(safe, label,
                                    download_url=(links.get("file") or {}).get("download_url") or "",
                                    workbench_url=links.get("workbench_url") or "")
    return _with_project_links(_text, label, saved)
