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


def test_scene_catalog_discloses_applicability_and_defers_full_methods():
    from src.graph.scene_skills import scene_catalog_prompt
    prompt = scene_catalog_prompt({"audit_uploaded_data"})
    catalog, _ = json.JSONDecoder().raw_decode(prompt.removeprefix("<skill_catalog>"))
    assert len(catalog) == 1 and catalog[0]["id"] == "data-audit"
    assert catalog[0]["applicability"] and catalog[0]["verified"]
    assert "method" not in catalog[0] and "acceptance" not in catalog[0]
    info = registry.skill_info("data-audit")
    assert info["method"] and info["acceptance"] and info["required_resources"]
    assert registry.read_resource("data-audit")["text"]
    assert "必须实际加载" in prompt


def test_scene_catalog_keeps_unavailable_skills_explicit(monkeypatch):
    from src.graph import scene_skills
    monkeypatch.setattr(scene_skills, "list_skills", lambda _: [
        {"id": "broken", "description": "unavailable method", "verified": False,
         "available": False, "reason": "ValueError"}])
    prompt = scene_skills.scene_catalog_prompt(set())
    catalog, _ = json.JSONDecoder().raw_decode(prompt.removeprefix("<skill_catalog>"))
    assert catalog == [{"id": "broken", "description": "unavailable method",
                        "verified": False, "available": False, "reason": "ValueError"}]


@pytest.mark.parametrize("reader", [False, True])
def test_initial_prompt_keeps_verified_nature_core_and_reachable_platform_methods(monkeypatch, reader):
    from src.prompts import loader, nature_figure
    monkeypatch.setattr(loader, "get_system_prompt", lambda _: "fixed QA prefix")
    tools = {"run_research_figure_script"}
    if reader:
        tools.add("research_workspace")
    prompt = loader.assemble_system_prompt(mode="expert", available_tools=tools)
    sources = registry.verified_sources("nature-figure")
    for name in nature_figure.GUIDANCE_FILES:
        assert sources[name] in prompt
    assert prompt.count(nature_figure.ADAPTER) == 1
    platform = registry.read_resource("figure-initial")
    assert platform["next_start_char"] is None
    if reader:
        assert platform["text"] not in prompt
        assert "初始生成前必须" in prompt and "skill_id='figure-initial'" in prompt
        assert "校验全文已在上方实际注入" in prompt
        assert "真实文件与必要Nature检查通过的回执" in prompt
    else:
        assert platform["text"] in prompt
        assert registry.read_resource("figure-edit")["text"] in prompt


def test_deferred_platform_method_is_complete_through_actual_skill_tool(monkeypatch):
    from src.research import workspace_tools
    from src.core.model_context import project_tool_message
    from langchain_core.messages import ToolMessage
    monkeypatch.setattr(workspace_tools, "_identity", lambda: (1, "synthetic-skill-test"))
    response = workspace_tools.research_workspace.invoke(
        {"operation": "read_skill", "skill_id": "figure-initial"})
    projected = project_tool_message(ToolMessage(name="research_workspace",
        tool_call_id="skill", content=response))
    payload = json.loads(projected.content)
    assert payload["entrypoint"]["text"] == registry.verified_sources("figure-initial")["SKILL.md"]
    assert payload["entrypoint"]["next_start_char"] is None
    assert payload["skill"]["depends_on"] == ["nature-figure"]
    assert payload["skill"]["acceptance"]
    assert payload["_tool_outcome"]["status"] == "complete"


def test_explicit_edit_phase_defers_only_edit_method_without_initial_aesthetics(monkeypatch):
    from src.prompts import loader, nature_figure
    monkeypatch.setattr(loader, "get_system_prompt", lambda _: "fixed QA prefix")
    prompt = loader.assemble_system_prompt(mode="expert", figure_phase="edit",
        available_tools={"run_research_figure_script", "research_workspace"})
    assert "skill_id='figure-edit'" in prompt and "编辑前必须" in prompt
    assert nature_figure.guidance() not in prompt
    assert "<figure_export_guidance" not in prompt
