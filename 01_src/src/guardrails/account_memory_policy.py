# -*- coding: utf-8 -*-
"""Input-driven account-memory policy.

Only the user's own message is classified. Assistant prose, paper conclusions,
current question parameters and inferred facts never enter account memory here.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

_EXPLICIT_SAVE = re.compile(
    r"(?:请|帮我|务必|you\s+must\s+)?记住(?:这个|一下|：|:)|"
    r"(?:请|帮我)?记一下|记下来|加入记忆|保存到(?:账号)?记忆|"
    r"以后(?:都|请|默认|回答|一律)|今后(?:都|请|默认|使用|采用)|"
    r"从现在起(?:都|请|默认)|长期约定|"
    r"(?:remember|save)\s+(?:this|that|the following)|from\s+now\s+on",
    re.I,
)
_AUTO_PREFERENCE = re.compile(
    r"(?:我|本人)(?:希望|要求)(?:你|回答|输出|今后|以后|始终|默认).{1,180}|"
    r"(?:我|本人)(?:通常|一般|更|偏好|喜欢|不喜欢).{0,80}"
    r"(?:回答|输出|语言|格式|风格|篇幅|中文|英文|称呼|局限|边界).{0,120}|"
    r"(?:回答|输出|写作|翻译)(?:请|要|应)?(?:默认|以后|始终|一律).{1,180}|"
    r"(?:我|本人)(?:请|要求你).{0,80}(?:回答|输出|格式|风格|语言).{1,160}",
    re.I,
)
_AUTO_PROJECT = re.compile(
    r"(?:我们|本课题组|本团队|本项目|实验室|项目组).{0,80}"
    r"(?:项目|课题|团队|实验|数据|代码|命名|名称|口径|规则|约定|使用|采用|统一)"
    r".{1,200}",
    re.I,
)
_PREFERENCE_HINT = re.compile(
    r"回答|输出|语言|中文|英文|格式|风格|篇幅|语气|称呼|局限|边界|引用|"
    r"偏好|喜欢|不喜欢|默认|请始终|以后都|style|format|language|brief",
    re.I,
)
_PROJECT_HINT = re.compile(
    r"项目|课题|团队|实验室|项目组|数据|代码|实验|命名|口径|规则|约定|"
    r"project|team|dataset|codebase|experiment|naming",
    re.I,
)
_IMPLEMENTATION_PREFIX = re.compile(
    r"^\s*(?:请|帮我|麻烦)?(?:记住|记一下|记下来|保存到(?:账号)?记忆)"
    r"(?:这个|一下)?\s*[：:]?\s*",
    re.I,
)


@dataclass(frozen=True)
class MemoryCandidate:
    kind: str
    key: str
    value: str
    confidence: float
    explicit: bool


def _normalise(query: str) -> str:
    text = " ".join(str(query or "").replace("\u00a0", " ").split())
    text = _IMPLEMENTATION_PREFIX.sub("", text).strip(" ：:，,")
    return text[:300]


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:10]


def extract_memory_candidates(query: str) -> list[MemoryCandidate]:
    """Return zero or more deterministic candidates derived from user input."""
    raw = " ".join(str(query or "").split())
    if not raw:
        return []
    explicit = bool(_EXPLICIT_SAVE.search(raw))
    auto_pref = bool(_AUTO_PREFERENCE.search(raw))
    auto_project = bool(_AUTO_PROJECT.search(raw))
    if not (explicit or auto_pref or auto_project):
        return []

    value = _normalise(raw)
    if len(value) < 2:
        return []
    project = auto_project or (
        explicit and _PROJECT_HINT.search(value) and not _PREFERENCE_HINT.search(value))
    preference = auto_pref or (explicit and _PREFERENCE_HINT.search(value))
    # Explicit statements are saved even when they do not match a narrow hint.
    kind = "project" if project else "preference"
    if kind == "project":
        label = "项目约定"
    elif re.search(r"中文|英文|语言|language", value, re.I):
        label = "回答语言"
    elif re.search(r"格式|篇幅|引用|format|length|citation", value, re.I):
        label = "回答格式"
    elif re.search(r"局限|边界|不确定|limitation|boundary", value, re.I):
        label = "回答边界"
    else:
        label = "用户偏好"
    key = f"{label}-{_short_hash(value)}"
    return [MemoryCandidate(
        kind=kind,
        key=key[:80],
        value=value,
        confidence=0.95 if explicit else 0.85,
        explicit=explicit,
    )]
