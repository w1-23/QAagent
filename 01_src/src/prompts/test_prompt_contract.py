"""Prompt sources are generic, complete, and identical to the runtime prefix.

This is a non-overwriting verifier: it never touches the historical
``audit_verification.json`` or any file under ``engineering/``. It only reads the
five fixed source files, assembles them in a temporary directory, and asserts
that the result reaches both ``ensure_fixed_prompts(reload=True)`` and the
build file byte-for-byte.
"""
from __future__ import annotations

import shutil

from src.prompts import loader

# Historical task names, run ids, and audit-only phrasing must not become
# prompt rules. The principles below are the generic replacement.
AUDIT_SPECIFIC_MARKERS = (
    "FW08", "FW11", "FW15", "FW18", "FW20", "L-N04", "L-N07",
    "v8_work", "40-task", "audited 40",
)
GENERIC_DELIVERY_MARKERS = (
    "交付物与多份产物",
    "一份正文最多作为一个报告的正文",
    "内容相同/同一份正文",
    "不同身份、不同受众或不同内容范围",
    "不静默把多份变成一份",
)
KEPT_SAFETY_MARKERS = (
    "scope_limited",
    "deepseek_fetch_paper",
    "word_document",
    "下载链接必须来自本轮通过校验的产物",
)


def _combined_sources() -> str:
    return "\n\n".join(
        (loader.PROMPT_DIR / "source" / name).read_text(encoding="utf-8").strip()
        for name in loader.ROLE_SOURCE_FILES["supervisor"]
    )


def test_prompt_sources_are_generic_and_keep_safety_rules():
    combined = _combined_sources()
    for marker in AUDIT_SPECIFIC_MARKERS:
        assert marker not in combined
    for marker in GENERIC_DELIVERY_MARKERS:
        assert marker in combined
    for marker in KEPT_SAFETY_MARKERS:
        assert marker in combined


def test_runtime_prefix_and_build_match_the_five_sources(tmp_path, monkeypatch):
    root = tmp_path / "prompts"
    (root / "source").mkdir(parents=True)
    files = loader.ROLE_SOURCE_FILES["supervisor"]
    for filename in files:
        shutil.copyfile(loader.PROMPT_DIR / "source" / filename,
                        root / "source" / filename)
    monkeypatch.setattr(loader, "PROMPT_DIR", root)
    monkeypatch.setattr(loader, "_FIXED_PROMPTS", {"supervisor": "stale"})
    loader._read_prompt_cached.cache_clear()
    try:
        expected = "\n\n".join(
            (root / "source" / name).read_text(encoding="utf-8").strip()
            for name in files)
        compiled = loader.ensure_fixed_prompts(reload=True)["supervisor"]
        assert compiled == expected
        assert loader.get_system_prompt() == expected
        assert (root / "builds" / "supervisor_system.md").read_text(
            encoding="utf-8").strip() == expected
        assert len(files) == 5
    finally:
        loader._read_prompt_cached.cache_clear()
