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


def test_all_fixed_sources_reach_the_runtime_prefix_and_build_file(tmp_path, monkeypatch):
    """A rule edit must reach the five-source system prompt, not just a source file."""
    import shutil
    source_root = loader.PROMPT_DIR
    root = tmp_path / "prompts"
    (root / "source").mkdir(parents=True)
    files = loader.ROLE_SOURCE_FILES["supervisor"]
    for filename in files:
        shutil.copyfile(source_root / "source" / filename, root / "source" / filename)
    monkeypatch.setattr(loader, "PROMPT_DIR", root)
    monkeypatch.setattr(loader, "_FIXED_PROMPTS", {"supervisor": "stale prefix"})
    loader._read_prompt_cached.cache_clear()
    try:
        compiled = loader.ensure_fixed_prompts(reload=True)["supervisor"]
        expected = "\n\n".join((root / "source" / name).read_text(encoding="utf-8").strip()
                                 for name in files)
        assert compiled == expected
        assert loader.get_system_prompt() == expected
        assert (root / "builds" / "supervisor_system.md").read_text(encoding="utf-8").strip() == expected
        assert "必须先执行一次 `local_hybrid_search`" in compiled
        assert "DOI、PMID、标题不是它的参数" in compiled
        assert "scope_limited" in compiled
    finally:
        loader._read_prompt_cached.cache_clear()
