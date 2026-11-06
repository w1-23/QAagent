"""Canonical source-scope policy shared by the B1 and B2 adapters."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable


LOCAL_RETRIEVAL_TOOLS = frozenset(
    {"local_hybrid_search", "local_bm25_search", "local_dense_search"}
)
WEB_RETRIEVAL_TOOLS = frozenset(
    {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}
)


@dataclass(frozen=True)
class SourceScopePolicy:
    raw: str
    canonical: str
    allow_local: bool
    allow_web: bool
    allow_attachment: bool
    local_selector: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "raw": self.raw,
            "canonical": self.canonical,
            "allow_local": self.allow_local,
            "allow_web": self.allow_web,
            "allow_attachment": self.allow_attachment,
            "local_selector": self.local_selector,
            "forbidden_retrieval_tools": sorted(
                (LOCAL_RETRIEVAL_TOOLS if not self.allow_local else frozenset())
                | (WEB_RETRIEVAL_TOOLS if not self.allow_web else frozenset())
            ),
        }


def resolve_source_scope(raw: Any) -> SourceScopePolicy:
    """Map every frozen scope spelling to one executable policy."""
    value = str(raw or "").strip()
    folded = value.casefold()
    if not value:
        raise ValueError("source_scope is empty")

    attachment_only = bool(
        re.search(r"(?:仅|只|only|just).{0,12}(?:附件|上传|attachment|upload)", folded)
        or folded in {"attachment", "attachments", "attachment_only", "upload_only"}
    )
    if attachment_only:
        return SourceScopePolicy(value, "attachment", False, False, True)

    local_only = bool(
        re.search(
            r"(?:仅|只|only|just).{0,12}(?:本地|语料|文献|local|corpus|literature)",
            folded,
        )
        or re.search(r"不要联网|不许联网|不联网|without\s+(?:the\s+)?web", folded)
        or folded in {"local", "local_only", "corpus_only"}
        or folded.startswith("仅本地:")
        or folded.startswith("local:")
    )
    web_only = bool(
        re.search(
            r"(?:仅|只|only|just).{0,12}(?:联网|网络|网页|web|internet|online)",
            folded,
        )
        or re.search(r"不要查本地|不查本地|without\s+(?:the\s+)?local", folded)
        or folded in {"web", "web_only", "internet_only", "online_only"}
    )
    if local_only and web_only:
        raise ValueError("source_scope simultaneously requires local-only and web-only")
    if local_only:
        selector = ""
        match = re.search(r"[:：]\s*([A-Za-z0-9_.-]+)\s*$", value)
        if match:
            selector = match.group(1)
        return SourceScopePolicy(value, "local", True, False, False, selector)
    if web_only:
        return SourceScopePolicy(value, "web", False, True, False)

    fusion = bool(
        re.search(r"融合|结合|本地.{0,12}联网|联网.{0,12}本地|fusion", folded)
    )
    automatic = folded in {
        "",
        "auto",
        "automatic",
        "general",
        "as_stated",
        "自动",
        "按题目",
    }
    if fusion:
        return SourceScopePolicy(value, "fusion", True, True, True)
    if automatic:
        return SourceScopePolicy(value, "auto", True, True, True)
    raise ValueError(f"unrecognized source_scope: {value}")


def validate_tool_trajectory(
    policy: SourceScopePolicy,
    tool_events: Iterable[dict[str, Any]],
) -> list[str]:
    """Reject runtime retrieval outside the canonical condition boundary."""
    errors: list[str] = []
    for index, event in enumerate(tool_events):
        if not isinstance(event, dict):
            continue
        name = str(event.get("tool_name") or "").strip()
        if name in LOCAL_RETRIEVAL_TOOLS and not policy.allow_local:
            errors.append(f"source_scope_forbidden_local_tool:{index}:{name}")
        if name in WEB_RETRIEVAL_TOOLS and not policy.allow_web:
            errors.append(f"source_scope_forbidden_web_tool:{index}:{name}")
    return errors


__all__ = [
    "LOCAL_RETRIEVAL_TOOLS",
    "SourceScopePolicy",
    "WEB_RETRIEVAL_TOOLS",
    "resolve_source_scope",
    "validate_tool_trajectory",
]
