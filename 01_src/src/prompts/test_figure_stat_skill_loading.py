"""The figure and statistics guidance load only when their tools are available.

The rule under test: capability guidance follows the environment (is the tool
in the offered set?), not the wording of the question. A keyword gate would
reproduce the "twelve-character question loses both retrieval tools" bug this
repository already had to fix once.
"""
from src.prompts import loader


def _install(monkeypatch, reads):
    """Patch the prompt source so every read is recorded in ``reads``."""
    monkeypatch.setattr(loader, "get_system_prompt", lambda *_: "fixed QA prefix")

    def read(name):
        reads.append(name)
        return "RULES for " + name

    monkeypatch.setattr(loader, "_read_prompt_cached", read)


def test_statistics_guidance_needs_the_statistics_tool(monkeypatch):
    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图", available_tools=set())
    assert prompt == "fixed QA prefix"
    assert reads == []

    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图", available_tools={"analyze_research_data"})
    assert "RULES for skills/statistics/SKILL.md" in prompt
    assert "analyze_research_data" in prompt
    assert reads == ["skills/statistics/SKILL.md"]


def test_figure_guidance_needs_the_figure_tool(monkeypatch):
    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图", available_tools={"inspect_uploaded_table"})
    assert prompt == "fixed QA prefix"
    assert reads == []

    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图", available_tools={"create_research_figure"})
    assert "RULES for skills/figure-export/SKILL.md" in prompt
    assert "create_research_figure" in prompt
    assert "run_research_figure_script" in prompt
    assert reads == ["skills/figure-export/SKILL.md"]


def test_the_question_wording_never_switches_guidance_on(monkeypatch):
    """Same tools, different questions → same guidance."""
    reads = []
    _install(monkeypatch, reads)
    loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图", available_tools={"analyze_research_data"})
    with_tool = list(reads)

    reads = []
    _install(monkeypatch, reads)
    loader.assemble_system_prompt(
        mode="auto", query="解释一下柑橘溃疡病", available_tools={"analyze_research_data"})
    assert reads == with_tool

    # And a question that asks for a figure but has no figure tool available
    # gets no figure guidance at all.
    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="帮我画一张柱状图", available_tools={"analyze_research_data"})
    assert "figure_export_guidance" not in prompt
    assert reads == ["skills/statistics/SKILL.md"]


def test_both_guidances_load_together_when_both_tools_exist(monkeypatch):
    reads = []
    _install(monkeypatch, reads)
    prompt = loader.assemble_system_prompt(
        mode="auto", query="画一张柱状图",
        available_tools={"analyze_research_data", "create_research_figure"})
    assert "<statistics_guidance>" in prompt
    assert "<figure_export_guidance>" in prompt
    assert reads == ["skills/statistics/SKILL.md", "skills/figure-export/SKILL.md"]


def test_a_missing_skill_file_is_a_hard_error(monkeypatch):
    """Claiming a capability whose rules file is gone would be a silent lie."""
    monkeypatch.setattr(loader, "get_system_prompt", lambda *_: "fixed QA prefix")
    monkeypatch.setattr(loader, "_read_prompt_cached", lambda name: "")
    try:
        loader.assemble_system_prompt(
            mode="auto", query="x", available_tools={"analyze_research_data"})
    except RuntimeError as exc:
        assert "统计规范" in str(exc)
    else:
        raise AssertionError("缺失规范文件时应当报错")
