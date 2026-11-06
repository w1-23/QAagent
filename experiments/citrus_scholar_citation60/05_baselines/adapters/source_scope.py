"""Canonical source-scope policy shared by the B1 and B2 adapters."""
from __future__ import annotations

import json
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


def resolve_allowed_corpus_family_ids(
    raw: Any,
    policy: SourceScopePolicy,
    *,
    known_families: Iterable[str] | None = None,
) -> list[str]:
    """Parse the explicit local-family allow-list for one frozen case."""
    value = raw
    if isinstance(value, str):
        text = value.strip()
        if not text:
            value = []
        else:
            try:
                value = json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    "allowed_corpus_family_ids must be a JSON list"
                ) from exc
    if value is None:
        value = []
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ValueError("allowed_corpus_family_ids must be a list of strings")
    sequence = [
        item.strip().casefold()
        for item in value
        if item.strip()
    ]
    if len(sequence) != len(set(sequence)):
        raise ValueError("allowed_corpus_family_ids contains duplicates")
    normalized = sorted(set(sequence))
    if known_families is not None:
        known = {str(item).strip().casefold() for item in known_families}
        unknown = sorted(set(normalized) - known)
        if unknown:
            raise ValueError(
                "unregistered allowed corpus family id(s): " + ",".join(unknown)
            )
    if policy.allow_local and not normalized:
        raise ValueError(
            "source scope permits local retrieval but allowed_corpus_family_ids is empty"
        )
    if not policy.allow_local and normalized:
        raise ValueError(
            "source scope forbids local retrieval but allowed_corpus_family_ids is non-empty"
        )
    return normalized


def _family_values(value: Any, *, request: bool) -> set[str]:
    """Collect explicit family constraints/results without inferring from text."""
    result: set[str] = set()
    request_keys = {"corpus_types", "allowed_corpus_family_ids"}
    result_keys = {"corpus_family_id", "corpus_family", "family_id"}
    if isinstance(value, dict):
        for key, child in value.items():
            normalized_key = str(key).strip().casefold()
            if normalized_key in (request_keys if request else result_keys):
                items = child if isinstance(child, list) else [child]
                for item in items:
                    text = str(item or "").strip().casefold()
                    if text:
                        result.add(text)
            result.update(_family_values(child, request=request))
    elif isinstance(value, list):
        for child in value:
            result.update(_family_values(child, request=request))
    return result


def validate_tool_trajectory(
    policy: SourceScopePolicy,
    tool_events: Iterable[dict[str, Any]],
    *,
    allowed_corpus_family_ids: Iterable[str] | None = None,
) -> list[str]:
    """Reject runtime retrieval outside the canonical condition boundary."""
    errors: list[str] = []
    allowed_families = {
        str(value).strip().casefold()
        for value in (allowed_corpus_family_ids or [])
        if str(value).strip()
    }
    for index, event in enumerate(tool_events):
        if not isinstance(event, dict):
            continue
        name = str(event.get("tool_name") or "").strip()
        if name in LOCAL_RETRIEVAL_TOOLS and not policy.allow_local:
            errors.append(f"source_scope_forbidden_local_tool:{index}:{name}")
        if name in LOCAL_RETRIEVAL_TOOLS and policy.allow_local and allowed_families:
            arguments = event.get("arguments")
            result = event.get("result")
            requested = _family_values(arguments, request=True)
            requested.update(_family_values(result, request=True))
            observed = _family_values(result, request=False)
            if not requested:
                errors.append(
                    f"source_scope_local_family_request_unobservable:{index}:{name}"
                )
            for family_id in sorted(requested - allowed_families):
                errors.append(
                    f"source_scope_forbidden_local_family:{index}:{family_id}"
                )
            for family_id in sorted(observed - allowed_families):
                errors.append(
                    f"source_scope_forbidden_local_result_family:{index}:{family_id}"
                )
        if name in WEB_RETRIEVAL_TOOLS and not policy.allow_web:
            errors.append(f"source_scope_forbidden_web_tool:{index}:{name}")
    return errors


__all__ = [
    "LOCAL_RETRIEVAL_TOOLS",
    "SourceScopePolicy",
    "WEB_RETRIEVAL_TOOLS",
    "resolve_allowed_corpus_family_ids",
    "resolve_source_scope",
    "validate_tool_trajectory",
]
