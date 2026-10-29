from pathlib import Path

from src.prompts.loader import BUILD_FILE_NAMES, PROMPT_DIR, ROLE_SOURCE_FILES
from src.prompts.snapshot import render_all


def test_every_source_prompt_is_registered():
    registered = {name for names in ROLE_SOURCE_FILES.values() for name in names}
    present = {path.name for path in (PROMPT_DIR / "source").glob("*.md")}
    assert present == registered


def test_only_registered_prompt_builds_remain():
    present = {path.name for path in (PROMPT_DIR / "builds").glob("*.md")}
    assert present == set(BUILD_FILE_NAMES.values())


def test_snapshot_inventory_matches_active_prompt_sources():
    snapshots = render_all()
    present = {path.name for path in (PROMPT_DIR / "snapshots").glob("*.txt")}
    assert present == set(snapshots)
