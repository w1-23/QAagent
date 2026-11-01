# -*- coding: utf-8 -*-
"""Editable account preferences injected into the General Agent prompt.

Only stable ``pref.<field>`` keys are accepted or rendered. Legacy inferred
preference keys remain readable for migration/record display but are never
silently treated as current user configuration.
"""
from __future__ import annotations

import threading
import unicodedata
from datetime import datetime
from typing import Any, Mapping

from src.core.db import connect_db

_LOCK = threading.RLock()
_KEY_PREFIX = "pref."
_MAX_CUSTOM_NOTE = 300

DEFAULT_PREFERENCES: dict[str, str] = {
    "response_language": "zh",
    "answer_length": "standard",
    "output_format": "markdown",
    "visualization": "auto",
    "citation_style": "standard",
    "limitations": "when_relevant",
    "custom_note": "",
}

PREFERENCE_OPTIONS: dict[str, tuple[str, ...]] = {
    "response_language": ("zh", "en", "bilingual"),
    "answer_length": ("brief", "standard", "detailed"),
    "output_format": ("markdown", "structured", "plain"),
    "visualization": ("auto", "always", "off"),
    "citation_style": ("standard", "strict", "minimal"),
    "limitations": ("when_relevant", "always", "minimal"),
    "custom_note": (),
}

PREFERENCE_FIELD_LABELS: dict[str, str] = {
    "response_language": "回答语言",
    "answer_length": "回答篇幅",
    "output_format": "输出格式",
    "visualization": "可视化",
    "citation_style": "引用与来源",
    "limitations": "局限与边界",
    "custom_note": "补充偏好",
}

PREFERENCE_OPTION_LABELS: dict[str, dict[str, str]] = {
    "response_language": {
        "zh": "简体中文",
        "en": "English",
        "bilingual": "中英双语",
    },
    "answer_length": {
        "brief": "简洁",
        "standard": "标准",
        "detailed": "详细",
    },
    "output_format": {
        "markdown": "Markdown",
        "structured": "结构化分节",
        "plain": "纯文本",
    },
    "visualization": {
        "auto": "按需自动",
        "always": "适合时优先图示",
        "off": "默认关闭",
    },
    "citation_style": {
        "standard": "标准",
        "strict": "严格核验",
        "minimal": "精简引用",
    },
    "limitations": {
        "when_relevant": "相关时说明",
        "always": "有真实边界时说明",
        "minimal": "尽量精简",
    },
    "custom_note": {},
}

_INSTRUCTIONS: dict[str, dict[str, str]] = {
    "response_language": {
        "zh": "默认使用简体中文回答。",
        "en": "Answer in English by default.",
        "bilingual": "关键结论和必要说明提供中英双语。",
    },
    "answer_length": {
        "brief": "默认简洁，先给结论和关键证据。",
        "standard": "默认保持标准篇幅，按问题复杂度展开。",
        "detailed": "默认详细回答，完整解释方法、证据和边界。",
    },
    "output_format": {
        "markdown": "默认使用清晰的 Markdown。",
        "structured": "默认按小标题和列表组织答案。",
        "plain": "默认使用易读的纯文本，避免复杂排版。",
    },
    "visualization": {
        "auto": "仅在确实有助于理解时使用图示。",
        "always": "问题适合且工具可用时优先给出图示；不要每轮强制画图。",
        "off": "除非本轮明确要求，否则不主动生成图示。",
    },
    "citation_style": {
        "standard": "引用保持可靠、清楚和可核验。",
        "strict": "提高引用与来源核验要求；不要为满足偏好而强制双源检索。",
        "minimal": "只保留支撑结论所必需的引用。",
    },
    "limitations": {
        "when_relevant": "存在真实局限或不确定性时说明。",
        "always": "存在真实边界时明确说明；没有真实边界时不要凑段。",
        "minimal": "局限说明保持简短，只保留会影响判断的内容。",
    },
}


class PreferenceValidationError(ValueError):
    """Raised when an account-preference patch is malformed."""


def _db_path(db_path: str | None = None) -> str:
    if db_path:
        return str(db_path)
    from src.guardrails.memory import memory_store

    return str(memory_store.db_path)


def _ensure_schema(conn) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS account_preferences (
            user_id INTEGER NOT NULL,
            pref_key TEXT NOT NULL,
            pref_value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, pref_key)
        )"""
    )


def _clean_text(value: object, *, limit: int) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = "".join(
        ch for ch in text
        if ch in {"\n", "\t"} or not unicodedata.category(ch).startswith("C")
    )
    return text.strip()[:limit]


def _validate_patch(patch: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(patch, Mapping):
        raise PreferenceValidationError("偏好数据必须是 JSON 对象。")
    unknown = sorted(set(str(key) for key in patch) - set(DEFAULT_PREFERENCES))
    if unknown:
        raise PreferenceValidationError("未知偏好字段：" + "、".join(unknown))

    cleaned: dict[str, str] = {}
    for field, raw in patch.items():
        field = str(field)
        if not isinstance(raw, str):
            raise PreferenceValidationError(f"{PREFERENCE_FIELD_LABELS[field]}必须是字符串。")
        if field == "custom_note":
            value = _clean_text(raw, limit=_MAX_CUSTOM_NOTE + 1)
            if len(value) > _MAX_CUSTOM_NOTE:
                raise PreferenceValidationError("补充偏好不能超过 300 个字符。")
        else:
            value = _clean_text(raw, limit=32)
            if value and value not in PREFERENCE_OPTIONS[field]:
                raise PreferenceValidationError(
                    f"{PREFERENCE_FIELD_LABELS[field]}的取值无效。"
                )
        cleaned[field] = value
    return cleaned


def _read_rows(user_id: int, db_path: str | None = None) -> dict[str, str]:
    uid = int(user_id or 0)
    if uid <= 0:
        return {}
    with _LOCK, connect_db(_db_path(db_path)) as conn:
        _ensure_schema(conn)
        rows = conn.execute(
            """SELECT pref_key, pref_value FROM account_preferences
               WHERE user_id=? AND pref_key LIKE 'pref.%'""",
            (uid,),
        ).fetchall()
    values: dict[str, str] = {}
    for key, value in rows:
        field = str(key or "").removeprefix(_KEY_PREFIX)
        if field in DEFAULT_PREFERENCES:
            values[field] = str(value or "")
    return values


def effective_preferences(user_id: int, db_path: str | None = None) -> dict[str, str]:
    """Return every fixed field, filling omitted fields with defaults."""
    values = dict(DEFAULT_PREFERENCES)
    values.update({
        field: value for field, value in _read_rows(user_id, db_path).items()
        if field in DEFAULT_PREFERENCES
    })
    return values


def preference_payload(user_id: int, db_path: str | None = None) -> dict[str, Any]:
    return {
        "status": "ok",
        "preferences": effective_preferences(user_id, db_path),
        "defaults": dict(DEFAULT_PREFERENCES),
        "options": {key: list(value) for key, value in PREFERENCE_OPTIONS.items()},
        "labels": {
            "fields": dict(PREFERENCE_FIELD_LABELS),
            "options": {key: dict(value) for key, value in PREFERENCE_OPTION_LABELS.items()},
        },
        "custom_note_max_length": _MAX_CUSTOM_NOTE,
    }


def update_preferences(
    user_id: int,
    patch: Mapping[str, Any],
    db_path: str | None = None,
) -> dict[str, Any]:
    """Validate once, then update all supplied fields in one transaction."""
    uid = int(user_id or 0)
    if uid <= 0:
        raise PreferenceValidationError("账号无效。")
    cleaned = _validate_patch(patch)
    now = datetime.now().isoformat()
    with _LOCK, connect_db(_db_path(db_path)) as conn:
        _ensure_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        try:
            for field, value in cleaned.items():
                key = _KEY_PREFIX + field
                if not value or value == DEFAULT_PREFERENCES[field]:
                    conn.execute(
                        "DELETE FROM account_preferences WHERE user_id=? AND pref_key=?",
                        (uid, key),
                    )
                else:
                    conn.execute(
                        """INSERT INTO account_preferences
                           (user_id, pref_key, pref_value, updated_at)
                           VALUES (?, ?, ?, ?)
                           ON CONFLICT(user_id, pref_key) DO UPDATE SET
                             pref_value=excluded.pref_value,
                             updated_at=excluded.updated_at""",
                        (uid, key, value, now),
                    )
            conn.commit()
        except Exception:
            conn.rollback()
            raise
    return preference_payload(uid, db_path)


def reset_preferences(user_id: int, db_path: str | None = None) -> dict[str, Any]:
    uid = int(user_id or 0)
    if uid <= 0:
        raise PreferenceValidationError("账号无效。")
    with _LOCK, connect_db(_db_path(db_path)) as conn:
        _ensure_schema(conn)
        conn.execute(
            "DELETE FROM account_preferences WHERE user_id=? AND pref_key LIKE 'pref.%'",
            (uid,),
        )
        conn.commit()
    return preference_payload(uid, db_path)


def render_preferences_for_prompt(
    user_id: int,
    max_chars: int = 500,
    db_path: str | None = None,
) -> str:
    """Render changed fields in a fixed order with a hard character budget."""
    values = effective_preferences(user_id, db_path)
    lines: list[str] = []
    for field in DEFAULT_PREFERENCES:
        value = values.get(field, "")
        if not value or value == DEFAULT_PREFERENCES[field]:
            continue
        if field == "custom_note":
            line = f"- 补充偏好：{_clean_text(value, limit=_MAX_CUSTOM_NOTE)}"
        else:
            line = "- " + _INSTRUCTIONS[field].get(value, "")
        if line and line != "- ":
            lines.append(line)
    if not lines:
        return ""

    header = (
        "## 账号偏好\n"
        "这些是用户保存的长期偏好；优先级低于本轮明确指令、当前任务约束、权限和安全规则。"
    )
    budget = max(80, int(max_chars or 500))
    output = header
    for line in lines:
        if len(output) + 1 + len(line) > budget:
            break
        output += "\n" + line
    return output if output != header else ""


__all__ = [
    "DEFAULT_PREFERENCES",
    "PREFERENCE_FIELD_LABELS",
    "PREFERENCE_OPTION_LABELS",
    "PREFERENCE_OPTIONS",
    "PreferenceValidationError",
    "effective_preferences",
    "preference_payload",
    "render_preferences_for_prompt",
    "reset_preferences",
    "update_preferences",
]
