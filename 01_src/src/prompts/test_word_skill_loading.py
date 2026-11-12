"""Word guidance stays out of the ordinary QA prefix and honors availability."""
from src.prompts import loader


def test_word_guidance_only_for_available_document_tool(monkeypatch):
    reads = []
    monkeypatch.setattr(loader, "get_system_prompt", lambda *_: "fixed QA prefix")
    def read(name):
        reads.append(name)
        return "WORD RULES"
    monkeypatch.setattr(loader, "_read_prompt_cached", read)
    assert loader.assemble_system_prompt(mode="auto", query="解释一下实验随机化", available_tools={"word_document"}) == "fixed QA prefix"
    assert loader.assemble_system_prompt(mode="auto", query="生成 Word 报告", available_tools=set()) == "fixed QA prefix"
    assert not reads
    prompt = loader.assemble_system_prompt(mode="auto", query="生成 Word 报告", available_tools={"word_document"})
    assert "WORD RULES" in prompt and "expected_last_block_id" in prompt
    assert reads == ["skills/word-export/SKILL.md"]
    assert all("SKILL" not in path for paths in loader.ROLE_SOURCE_FILES.values() for path in paths)


def test_reload_clears_source_and_assembled_cache(monkeypatch):
    source = {"version": "old"}
    monkeypatch.setattr(loader, "_FIXED_PROMPTS", None)
    monkeypatch.setattr(loader, "_read_prompt", lambda _: source["version"])
    monkeypatch.setattr(loader, "build_fixed_prompts", lambda: {"supervisor": loader._read_prompt_cached("source/test")})
    loader._read_prompt_cached.cache_clear()
    try:
        assert loader.ensure_fixed_prompts()["supervisor"] == "old"
        source["version"] = "new"
        assert loader.ensure_fixed_prompts()["supervisor"] == "old"
        assert loader.ensure_fixed_prompts(reload=True)["supervisor"] == "new"
    finally:
        loader._read_prompt_cached.cache_clear()
