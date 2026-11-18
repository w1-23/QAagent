"""Scene methods over the existing read-only skill registry, never executors.

The model sees applicability and acceptance in the catalog, then reads methods
with research_workspace. Initial Nature core loading remains deterministic;
only an explicitly established edit phase omits that initial-only guidance.
"""
from __future__ import annotations

import json

from src.prompts.skill_registry import core_guidance, list_skills


def scene_catalog_prompt(available_tools: set[str] | None = None) -> str:
    """Describe methods without selecting tasks or expanding source authority."""
    # Discovery needs applicability, not the full methods/provenance returned by
    # read_skill. Keep unavailable entries explicit and leave the registry's
    # complete metadata API intact for the actual skill-loading boundary.
    fields = ("id", "description", "applicability", "depends_on", "verified", "available", "reason")
    catalog = [{key: entry[key] for key in fields if key in entry}
               for entry in list_skills(available_tools)]
    return "<skill_catalog>" + json.dumps(catalog, ensure_ascii=False, separators=(",", ":")) + (
        "\n先按适用条件选择技能，再用research_workspace(operation='read_skill',skill_id=...)读取方法；"
        "按返回的next_start_char续读，引用资源用read_skill_resource按目录路径分页读取。"
        "depends_on指定的技能与required_resources必须实际加载；目录、版本号或方法摘要不是已读全文的证据。"
        "技能管方法，现有工具管执行，覆盖账和终态管完成；技能不注册工具、不安装依赖、不增加来源权限。"
        "保留请求级候选预取和按需读取，不把候选目录当已读证据。"
        "所有自然语言检索必须先将母语问题转成英文，再构造英文query；翻译失败只能显式报错、重试或复用有效翻译缓存，"
        "不得用原文、其他检索工具或新来源绕过。DOI、登记号、品种原名等精确标识保留，URL抓取不改写URL。"
        "文献核验按来源版本+记录ID+核验规则累计，数据审查按文件版本+工作表+检查项累计；"
        "只按实际回执报告覆盖、未完成对象与原因，不凭工具调用次数或导出成功宣告完成。"
        "</skill_catalog>"
    )


def figure_scene_guidance(phase: str | None = None, *, skill_reader: bool = False) -> str:
    """Always inject verified initial Nature core; disclose platform methods on demand.

    A missing phase preserves the initial-loading guarantee. Platform methods
    may be read lazily only when the actual read-only skill tool is available;
    otherwise retain their complete text so no requirement becomes inaccessible.
    """
    if phase not in {None, "initial", "edit"}:
        raise ValueError("Unknown figure scene phase")
    edit_rules = (
        "编辑前必须用research_workspace(operation='read_skill',skill_id='figure-edit')读取完整方法；"
        "按next_start_char续读。按用户意愿修改既有对象，保留初始审计，不重套Nature美学。"
        if skill_reader else core_guidance("figure-edit"))
    if phase == "edit":
        return "<figure_edit_guidance>\n" + edit_rules + "\n</figure_edit_guidance>"

    from src.prompts.nature_figure import guidance

    initial_rules = (
        "初始生成前必须用research_workspace(operation='read_skill',skill_id='figure-initial')读取完整平台方法；"
        "按next_start_char续读，依其方法确认完整数据、figure contract、执行与验收要求。"
        "nature-figure的SKILL、core contract、stance与Python backend校验全文已在上方实际注入，"
        "可复用，无需仅为证明加载再读相同核心；其他资源按实际需要从目录分页读取。"
        "图件完成仍须真实文件与必要Nature检查通过的回执；读取技能不等于执行或通过。"
        if skill_reader else core_guidance("figure-initial"))
    return (
        "<nature_figure_guidance scope='initial_generation_only'>\n"
        "以下固定版本资源仅约束初始生成。后续用户改色、标记、图例、文字、位置、可见性等编辑按用户意愿执行，"
        "不重套Nature美学，不改写初始审计结论。上游涉及修改后重审的描述仅适用于初始生成中的修复。\n"
        + guidance()
        + "\n</nature_figure_guidance>\n"
        "<figure_export_guidance scope='initial_generation_only'>\n"
        + initial_rules
        + "\n</figure_export_guidance>\n<figure_edit_guidance>\n"
        + edit_rules
        + "\n</figure_edit_guidance>"
    )
