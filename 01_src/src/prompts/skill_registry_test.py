import hashlib
import json
import pytest
from src.prompts import skill_registry as registry


def test_nature_source_and_core_are_real():
    info = registry.skill_info("nature-figure")
    assert info["source"] == "https://github.com/Yuan1z0825/nature-skills"
    assert info["commit"] == "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"
    assert info["license"] == "Apache-2.0"
    text = registry.core_guidance("nature-figure")
    assert "Nature Figure Making" in text
    assert "static/fragments/backend/python.md" in text
    assert "static/fragments/backend/r.md\">" not in text


def test_resource_paging_and_provenance():
    page = registry.read_resource("nature-figure", max_chars=71)
    next_page = registry.read_resource("nature-figure", start_char=page["next_start_char"], max_chars=71)
    source = registry.verified_sources("nature-figure")[page["resource"]]
    assert page["text"] + next_page["text"] == source[:142]
    assert page["sha256"] == hashlib.sha256(source.encode()).hexdigest()


@pytest.mark.parametrize("path", ["../LICENSE", "/etc/passwd", "C:\\secrets", "missing.py"])
def test_only_installed_resources_are_readable(path):
    with pytest.raises(ValueError):
        registry.read_resource("nature-figure", path)


def test_tampered_skill_cannot_load(tmp_path, monkeypatch):
    root = tmp_path / "demo"
    root.mkdir()
    (root / "SKILL.md").write_text("changed", encoding="utf-8")
    (root / "LICENSE").write_text("license", encoding="utf-8")
    (root / "provenance.json").write_text(json.dumps({"source": "test", "commit": "pinned", "files": [
        {"path": "SKILL.md", "sha256": hashlib.sha256(b"original").hexdigest()},
        {"path": "LICENSE", "sha256": hashlib.sha256(b"license").hexdigest()},
    ]}), encoding="utf-8")
    entry = {"id": "demo", "description": "test", "root": "demo", "version": "1", "source": "test",
             "commit": "pinned", "provenance": "provenance.json", "entrypoint": "SKILL.md", "execution": "none"}
    (tmp_path / "catalog.json").write_text(json.dumps({"schema_version": 1, "skills": [entry]}), encoding="utf-8")
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    assert registry.list_skills()[0]["verified"] is False
    with pytest.raises(ValueError, match="integrity"):
        registry.read_resource("demo")


def test_adding_skill_does_not_require_a_new_tool(tmp_path, monkeypatch):
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo/SKILL.md").write_text("method guidance", encoding="utf-8")
    (tmp_path / "catalog.json").write_text(json.dumps({"schema_version": 1, "skills": [{
        "id": "demo", "version": "1", "description": "new scene guidance", "root": "demo",
        "source": "bundled:test", "entrypoint": "SKILL.md", "execution": "existing_tools",
        "requires_any": ["existing_tool"]}]}), encoding="utf-8")
    monkeypatch.setattr(registry, "ROOT", tmp_path)
    assert registry.list_skills({"unrelated"}) == []
    assert registry.list_skills({"existing_tool"})[0]["verified"]
    assert registry.read_resource("demo")["text"] == "method guidance"


def test_default_figure_prompt_uses_registered_nature_skill():
    from src.prompts.loader import verify_figure_prompt_loading
    status = verify_figure_prompt_loading()
    assert status["prompt_loaded"] and status["verified"]
    assert status["source"] == "https://github.com/Yuan1z0825/nature-skills"
