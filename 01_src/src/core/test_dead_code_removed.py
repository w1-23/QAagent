"""Regression guard: the confirmed dead code must not come back.

Every symbol listed here was verified to have **no** caller in the repository
(only its own definition, or a comment). They were removed together with the
old multi-agent / separation-rendering paths. This test fails if one of them
reappears, so the cleanup cannot silently regress.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

# module path -> names that must not exist any more
REMOVED = {
    "src.retrieval.multi_retriever": ["search_dense_only"],
    "src.core.evidence": [
        "EVIDENCE_TOOL_MAX_CHARS",
        "WEB_CITE_SLOT",
        "filter_refs_by_answer",
        "renumber_refs",
        "normalize_citation_brackets",
        "SOURCE_TAG",
        "SOURCE_LABEL",
        "SOURCE_ORDER",
        "source_tag",
        "source_label",
        "is_variety_source",
        "evidence_id",
        "dedup_evidence_items",
        "canonical_evidence_items",
    ],
    "src.core.web_output_separator": [
        "TIER_LABELS",
        "DEFAULT_HEADING",
        "WARNING_LINE",
        "tier_label",
        "model_facing_web_notice",
        "prepare_display_items",
        "build_web_section",
        "append_web_section",
    ],
}


@pytest.mark.parametrize("module_path,name", [
    (module_path, name)
    for module_path, names in REMOVED.items()
    for name in names
])
def test_removed_symbol_does_not_exist(module_path: str, name: str) -> None:
    import importlib

    module = importlib.import_module(module_path)
    assert not hasattr(module, name), (
        f"{module_path}.{name} 已被删除，不应复活")


def test_the_kept_evidence_helpers_are_still_importable() -> None:
    """The cleanup must not take the live helpers with it."""
    from src.core.evidence import (  # noqa: F401
        EVIDENCE_RENDER_MAX_CHARS, EVIDENCE_SNIPPET_MAX_CHARS, clean_doi,
        normalize_source_key, render_evidence, src_of,
    )


def test_the_kept_separator_helpers_are_still_importable() -> None:
    from src.core.web_output_separator import (  # noqa: F401
        SEPARATION, SeparationConfig, clean_web_title,
    )


def test_the_kept_dense_search_is_still_importable() -> None:
    from src.retrieval.multi_retriever import MultiBatchRetriever  # noqa: F401

    assert hasattr(MultiBatchRetriever, "search_dense_filtered")
