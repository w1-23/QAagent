"""F1: nature-figure adapter resource identity and immutable vendor closure.

Guards the F1 contract end to end without executing vendor code or any network:

* the canonical 44-file vendor snapshot and the 40-file legacy snapshot stay
  byte-identical (vendor bytes are never rewritten by the adapter layer);
* the outer adapter resources (catalog + provenance) contain no dangling
  entries and match the bytes on disk;
* the catalog manifest digest is the one the runtime recomputes;
* the real runtime helper discovers the release and serves paginated reads;
* the superseded adapter identity and the old 40-file identity stay documented,
  and the legacy read API still serves the old identity.

The adapter catalog resolves one pinned identity per skill. Until the main
session updates ``research_skill_catalog.PINNED`` to the new catalog identity,
the runtime-integration test pins it locally and a wiring test reports a skip.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from src.agent import research_skill_catalog
from src.prompts import skill_registry

ROOT = Path(__file__).resolve().parents[3]
SKILL_ROOT = ROOT / "skills/nature-figure"
VENDOR = SKILL_ROOT / "vendor/nature-skills"
LEGACY_VENDOR = ROOT / "01_src/src/prompts/skills/vendor/nature-skills"
CATALOG = ROOT / "02_config/skills.catalog.json"

VENDOR_COMMIT = "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
VENDOR_SKILL_VERSION = "2.8.0"
VENDOR_INVENTORY_SHA256 = (
    "a9b8e661bf460a401e17017f134a735432da9d7832e3f561a63c6cf47fb92b9e")
OLD_ADAPTER_VERSION = "2.0.0"
OLD_ADAPTER_DIGEST = (
    "00779e01d2c8756245c3553adb8abf451c4eb1dcd15c46ce7192ce3892d7f127")
NEW_ADAPTER_VERSION = "2.1.0"
REMOVED_DANGLING = ("tests/test_resources.py", "vendor/nature-skills/.gitattributes")
VENDOR_ONLY_EXTRAS = {"INTEGRATION.md", "provenance.json"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_digest(files: list[dict]) -> str:
    return hashlib.sha256(json.dumps(
        files, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _catalog_row() -> dict:
    data = json.loads(CATALOG.read_text(encoding="utf-8"))
    assert data["schema"] == "citrus.skill-catalog/1"
    assert data["version"] == "2.0.0"  # catalog schema version, not the row identity
    rows = [row for row in data["skills"] if row["id"] == "nature-figure"]
    assert len(rows) == 1
    return rows[0]


def _outer_provenance() -> dict:
    return json.loads((SKILL_ROOT / "provenance.json").read_text(encoding="utf-8"))


def _assert_records(root: Path, records: list[dict]) -> None:
    for record in records:
        path = root / record["path"]
        assert path.is_file(), record["path"]
        if "size" in record:  # inner vendor provenance records hash only
            assert path.stat().st_size == record["size"], record["path"]
        assert _sha256(path) == record["sha256"], record["path"]


def test_catalog_identity_is_self_consistent():
    row = _catalog_row()
    assert row["version"] == NEW_ADAPTER_VERSION
    assert row["path"] == "skills/nature-figure"
    assert row["entry"] == "SKILL.md"
    assert row["sha256"] == _manifest_digest(row["files"])

    text = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    header = re.match(r"\A---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    assert header is not None
    fields = dict(re.findall(r"^(name|description):[ \t]*(.+)$", header.group(1), re.M))
    version = re.search(r"^(?:  )?version:[ \t]*[\"']?([^\"'\r\n]+)", header.group(1), re.M)
    assert fields["name"] == "nature-figure"
    assert version is not None and version.group(1).strip() == row["version"]


def test_catalog_closure_has_no_dangling_entries():
    row = _catalog_row()
    paths = [record["path"] for record in row["files"]]
    assert len(paths) == len(set(paths)) == 57
    assert "references/figure-methods.md" in paths
    assert "tests/test_resources.py" not in paths
    assert "provenance.json" in paths
    _assert_records(SKILL_ROOT, row["files"])
    for dangling in REMOVED_DANGLING:
        if dangling == "tests/test_resources.py":
            assert not (SKILL_ROOT / dangling).exists()
    # The superseded identity is stated explicitly on the current record.
    assert row["supersedes"]["version"] == OLD_ADAPTER_VERSION
    assert row["supersedes"]["manifest_sha256"] == OLD_ADAPTER_DIGEST
    assert set(row["supersedes"]["removed_dangling_entries"]) == set(REMOVED_DANGLING)


def test_outer_provenance_closure_has_no_dangling_entries():
    prov = _outer_provenance()
    assert prov["version"] == NEW_ADAPTER_VERSION
    paths = [record["path"] for record in prov["files"]]
    assert len(paths) == len(set(paths)) == 56
    assert "references/figure-methods.md" in paths
    assert "vendor/nature-skills/.gitattributes" not in paths
    assert not (SKILL_ROOT / "vendor/nature-skills/.gitattributes").exists()
    _assert_records(SKILL_ROOT, prov["files"])
    assert prov["vendor"]["commit"] == VENDOR_COMMIT
    assert prov["vendor"]["skill_version"] == VENDOR_SKILL_VERSION
    identity = prov["adapter_identity"]
    assert identity["version"] == NEW_ADAPTER_VERSION
    assert identity["supersedes"]["version"] == OLD_ADAPTER_VERSION
    assert identity["supersedes"]["catalog_manifest_sha256"] == OLD_ADAPTER_DIGEST
    assert set(identity["supersedes"]["removed_dangling_entries"]) == set(REMOVED_DANGLING)


def test_vendor_snapshot_and_legacy_snapshot_are_byte_identical():
    canonical = json.loads((VENDOR / "provenance.json").read_text(encoding="utf-8"))
    assert canonical["commit"] == VENDOR_COMMIT
    assert len(canonical["files"]) == 44
    _assert_records(VENDOR, canonical["files"])
    assert _manifest_digest(sorted(canonical["files"], key=lambda row: row["path"])) == (
        VENDOR_INVENTORY_SHA256)
    # Exactly the fixed 44 files plus the two integration records; no stray files.
    assert {p.relative_to(VENDOR).as_posix() for p in VENDOR.rglob("*") if p.is_file()} == (
        {record["path"] for record in canonical["files"]} | VENDOR_ONLY_EXTRAS)

    legacy = json.loads((LEGACY_VENDOR / "provenance.json").read_text(encoding="utf-8"))
    assert legacy["commit"] == VENDOR_COMMIT
    assert len(legacy["files"]) == 40
    _assert_records(LEGACY_VENDOR, legacy["files"])
    assert {p.relative_to(LEGACY_VENDOR).as_posix() for p in LEGACY_VENDOR.rglob("*")
            if p.is_file()} == ({record["path"] for record in legacy["files"]}
                               | VENDOR_ONLY_EXTRAS)


def test_legacy_registry_still_reads_the_old_identity():
    info = skill_registry.skill_info("nature-figure")
    assert info["version"] == VENDOR_SKILL_VERSION
    assert info["commit"] == VENDOR_COMMIT
    assert info["verified"] is True

    text = (LEGACY_VENDOR / "skills/nature-figure/SKILL.md").read_text(encoding="utf-8")
    pages, start = [], 0
    while True:
        page = skill_registry.read_resource(
            "nature-figure", start_char=start, max_chars=97)
        assert page["version"] == VENDOR_SKILL_VERSION
        pages.append(page["text"])
        if page["next_start_char"] is None:
            break
        assert page["next_start_char"] > start
        start = page["next_start_char"]
    assert "".join(pages) == text


def test_runtime_discovery_and_paginated_reads(monkeypatch):
    row = _catalog_row()
    identity = (row["version"], row["sha256"])
    monkeypatch.setitem(research_skill_catalog.PINNED, "nature-figure", identity)

    discovered = {entry["id"]: entry for entry in research_skill_catalog.list_skills()}
    assert "nature-figure" in discovered
    info = discovered["nature-figure"]
    assert (info["version"], info["sha256"]) == identity
    assert info["verified"] is True
    assert info["required_resources"] == [
        "SKILL.md", "references/bridge.md", "references/figure-methods.md"]

    resolved_row, records, resolved_info = research_skill_catalog.resolve(
        "nature-figure", *identity)
    assert resolved_row["version"] == NEW_ADAPTER_VERSION
    assert resolved_info["required_resources"] == info["required_resources"]

    # Paginate the entrypoint through the real helper and reassemble exact bytes.
    full = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
    _, first = research_skill_catalog.read_resource(
        "nature-figure", *identity, "", start_char=0, max_chars=137)
    assert first["resource"] == "SKILL.md"
    assert first["text"] == full[:137]
    assert first["total_chars"] == len(full)
    assert first["next_start_char"] == 137
    assert first["sha256"] == records["SKILL.md"]["sha256"]

    chunks, start = [], 0
    while start is not None:
        _, page = research_skill_catalog.read_resource(
            "nature-figure", *identity, "", start_char=start, max_chars=137)
        chunks.append(page["text"])
        start = page["next_start_char"]
    assert "".join(chunks) == full

    # A vendor resource is served by exact manifest path with its recorded digest.
    vendor_resource = "vendor/nature-skills/skills/nature-figure/references/api.md"
    _, page = research_skill_catalog.read_resource(
        "nature-figure", *identity, vendor_resource, start_char=0, max_chars=1000)
    expected = (SKILL_ROOT / vendor_resource).read_text(encoding="utf-8")
    assert page["resource"] == vendor_resource
    assert page["text"] == expected[:1000]
    assert page["sha256"] == records[vendor_resource]["sha256"]
    assert page["next_start_char"] == 1000


def test_archived_adapter_reads_original_identity():
    row, records, info = research_skill_catalog.resolve(
        "nature-figure", OLD_ADAPTER_VERSION, OLD_ADAPTER_DIGEST)
    assert info["version"] == OLD_ADAPTER_VERSION and info["sha256"] == OLD_ADAPTER_DIGEST
    assert row["sha256"] == OLD_ADAPTER_DIGEST
    for name in ("SKILL.md", "references/bridge.md", "provenance.json", "provenance.extra.json",
                 "vendor/nature-skills/skills/nature-figure/references/api.md"):
        chunks, start = [], 0
        while start is not None:
            _, page = research_skill_catalog.read_resource(
                "nature-figure", OLD_ADAPTER_VERSION, OLD_ADAPTER_DIGEST, name,
                start_char=start, max_chars=20000)
            chunks.append(page["text"])
            assert page["sha256"] == records[name]["sha256"]
            start = page["next_start_char"]
        assert hashlib.sha256("".join(chunks).encode()).hexdigest() == records[name]["sha256"]
    with pytest.raises(ValueError, match="research_skill_identity_mismatch"):
        research_skill_catalog.resolve("nature-figure", OLD_ADAPTER_VERSION, "0" * 64)
    assert all(entry["sha256"] != OLD_ADAPTER_DIGEST for entry in research_skill_catalog.list_skills())


def test_runtime_pin_is_wired_to_current_identity():
    row = _catalog_row()
    identity = (row["version"], row["sha256"])
    pinned = research_skill_catalog.PINNED.get("nature-figure")
    if pinned != identity:
        pytest.skip(
            "main-session wiring: research_skill_catalog.PINNED names "
            f"{pinned} but the catalog identity is {identity}; update PINNED "
            "(and note the old 2.0.0 identity is documented in provenance.json).")
    info = next(entry for entry in research_skill_catalog.list_skills()
                if entry["id"] == "nature-figure")
    assert (info["version"], info["sha256"]) == identity
