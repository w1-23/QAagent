"""Prompt loader for the single active General Agent."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Iterable

# ─────────────────────────────────────────────────────────────
# 角色 → 固定源文件映射（唯一维护入口，改这里即改拼接结果）
# ─────────────────────────────────────────────────────────────
ROLE_SOURCE_FILES: dict[str, list[str]] = {
    "supervisor": [
        "01_global_role_domain.md",
        "02_global_data_fidelity_citation.md",
        "09_output_style.md",
        "16_tool_usage_file_rules.md",
        "20_terminology_domain.md",
    ],
}

# 角色 → builds/ 中构建文件名
BUILD_FILE_NAMES: dict[str, str] = {"supervisor": "supervisor_system.md"}

PROMPT_DIR = Path(__file__).resolve().parent


def _read_prompt(relative_path: str) -> str:
    path = PROMPT_DIR / relative_path
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""


@lru_cache(maxsize=256)
def _read_prompt_cached(relative_path: str) -> str:
    return _read_prompt(relative_path)


def _join_parts(parts: Iterable[str]) -> str:
    """固定拼接：源文件内容逐字保留，以 \n\n 分隔（与源文件间空行一致）。"""
    cleaned = [part.strip() for part in parts if part and part.strip()]
    return "\n\n".join(cleaned)


# ─────────────────────────────────────────────────────────────
# 固定 system prompt：构建 / 缓存 / 读取
# ─────────────────────────────────────────────────────────────

_FIXED_PROMPTS: dict[str, str] | None = None


def _concat_source_files(filenames: list[str]) -> str:
    parts = [_read_prompt_cached(f"source/{fn}") for fn in filenames]
    return _join_parts(parts)


def build_fixed_prompts() -> dict[str, str]:
    """启动时执行一次：按角色固定映射拼接完整 system prompt 并写入 builds/。

    返回 {role: prompt}；随后每次请求复用缓存字符串，不再重新拼接。
    """
    out: dict[str, str] = {}
    for role, filenames in ROLE_SOURCE_FILES.items():
        out[role] = _concat_source_files(filenames)

    # 落盘 builds/（best-effort：只读环境失败不影响内存缓存）
    try:
        builds_dir = PROMPT_DIR / "builds"
        builds_dir.mkdir(parents=True, exist_ok=True)
        for role, prompt in out.items():
            target = builds_dir / BUILD_FILE_NAMES[role]
            try:
                if target.exists() and target.read_text(encoding="utf-8").strip() == prompt:
                    continue  # 已存在且内容一致 → 跳过写入（幂等）
            except Exception:
                pass
            target.write_text(prompt + "\n", encoding="utf-8")
    except Exception:
        pass
    return out


def ensure_fixed_prompts(reload: bool = False) -> dict[str, str]:
    """进程级缓存：首次调用构建一次，之后保持不变（除非显式 reload）。"""
    global _FIXED_PROMPTS
    if _FIXED_PROMPTS is None or reload:
        if reload:
            _read_prompt_cached.cache_clear()
        _FIXED_PROMPTS = build_fixed_prompts()
    return _FIXED_PROMPTS


def get_system_prompt(role: str = "supervisor") -> str:
    """Return the active General Agent prompt."""
    return ensure_fixed_prompts().get(role, "")


# ─────────────────────────────────────────────────────────────
# Stable entry point used by the active graph.
# ─────────────────────────────────────────────────────────────

def assemble_system_prompt(
    *,
    mode: str,
    format_hint: str | None = None,
    query: str | None = None,
    available_tools: set[str] | None = None,
    figure_phase: str | None = None,
) -> str:
    """Keep the QA prefix fixed; expose methods through the existing skill reader.

    Unknown figure phase retains verified initial core loading. A caller with
    structured evidence of an existing-figure edit may pass ``figure_phase='edit'``.
    """
    prompt = get_system_prompt("supervisor")
    word_guidance = ""
    from src.graph.export_contract import deliverable_contract
    needs_word = any("docx" in r.get("formats", []) for r in deliverable_contract(query or ""))
    if needs_word and (available_tools is None or "word_document" in available_tools):
        rules = _read_prompt_cached("skills/word-export/SKILL.md")
        if not rules:
            raise RuntimeError("Word 写作规范文件缺失，不能声明已加载该能力。")
        word_guidance = "\n\n<word_export_guidance>\n" + rules + (
            "\n工具映射：使用 word_document(operation=...)，不要执行 Python。"
            "create 的 outline 每项为 {section_id,title}；create 也须有唯一 batch_id。"
            "每节写 heading 和正文后提交 section_end，整篇最后提交 document_end。"
            "finalize 传 expected_version、expected_last_block_id 和 .docx 文件名。"
            "inspect 可分页恢复块状态。草稿动作不产生下载文件；仅 finalize 成功才交付。"
            "本版本仅支持本工具草稿按块修改；不支持任意上传 DOCX 的保真编辑或模板导入，须如实说明。"
            "\n</word_export_guidance>")
    # Capabilities remain environment-selected. Detailed skills are discoverable
    # through one read-only interface, not expanded in every system prompt.
    if available_tools is None or "research_workspace" in available_tools:
        from src.graph.scene_skills import scene_catalog_prompt
        prompt += "\n\n" + scene_catalog_prompt(available_tools)
    if available_tools is None or "analyze_research_data" in available_tools:
        prompt += ("\n\n<statistics_guidance>统计前按需读取statistics技能。优先复用analyze_research_data与可信方法；"
                   "数值必须来自真实计算，保留前提、效应量、适用的多重比较校正与全精度结果引用；"
                   "不推断未知配对、不为了绘图自动删异常值或补值。</statistics_guidance>")
    if available_tools is None or {"create_research_figure", "run_research_figure_script"}.intersection(available_tools):
        from src.graph.scene_skills import figure_scene_guidance
        prompt += "\n\n" + figure_scene_guidance(figure_phase,
            skill_reader=available_tools is None or "research_workspace" in available_tools)
    if available_tools is None or "research_workspace" in available_tools:
        prompt += ("\n\n<research_workspace_guidance>服务器提供隔离科研工作区。预设工具与内置脚本长期维护，"
                   "任务脚本默认闲置7天暂存；以实际工作区返回状态为准。优先调用已有工具，再参数化复用已有脚本，"
                   "必要时用research_workspace读取并patch派生版本，只有缺口确实存在才从头写。"
                   "脚本执行返回script_ref/run_id；用引用继续工作，不从摘要重造源码或数值。"
                   "需要恢复进展时用research_workspace(operation='task_state')读取宿主保留的任务状态和操作回执；没有覆盖快照不能推断任务已完成，不重跑已完成操作。"
                   "轮后摘要仅作导航；需要精确原始对话时用operation='context_source'和摘要中的message_id/source_id分页读取。"
                   "输入、参数、单位和输出必须明确绑定；中间表与脚本默认内部保存，最终成果统一入资料库由界面导出。"
                   "研究意图明确时直接执行；变量角色、实验单位或关键处理会改变含义且不清楚时用request_user_input确认，"
                   "不要为了颜色、常规布局反复询问。多指标按研究目的组合子图或多图，不因数值列多就硬画在同一轴。"
                   "既有工具可以直接完成任务时，不必先生成脚本；文件下载不触发模型或重跑研究。"
                   "</research_workspace_guidance>")
    if available_tools is None or "read_tool_result" in available_tools:
        prompt += ("\n\n<historical_result_access>历史摘要不是本轮重新执行，也不替代原始数据。"
                   "需要旧工具细节时，以保留的tool_call_id调用read_tool_result，按字段或分页读取；"
                   "文件和分析结果用其原有授权引用。结果标为截断、过期或不可用时须如实说明，"
                   "不得从摘要重建原始数值或虚称全文仍永久存在。</historical_result_access>")
    if available_tools is None or "request_user_input" in available_tools:
        prompt += ("\n\n<user_input_guidance>缺少会影响正确执行的用户决定时，使用通用工具request_user_input。"
                   "已被用户请求且宿主来源范围允许的操作直接执行，不反问是否开始或是否授权相同范围；"
                   "正文给出的引文可以直接核验，不要求先上传文件或存入资料库。"
                   "一次问一个必要问题，提供1至3个简明候选并允许自定义或取消；一般研究目的选项refs可为空。"
                   "字段选项必须引用工具实际返回的dataset/source schema，不得猜科学语义。"
                   "只能通过原生结构化tool-call提问，禁止在正文写JSON卡片或DSML模拟调用。"
                   "提问必须单独一批，不得与依赖答案的写操作一起提交。提问落库后本段立即结束，"
                   "用户答复后由host创建关联的新执行段。仅规划未完成步骤，保留已验证结果；"
                   "不得自动重放被中断的图写操作。脚本失败按真实诊断在宿主和脚本剩余预算内修复，不能换任务重置预算。"
                   "</user_input_guidance>")
    prompt += ("\n\n<delivery_policy>科研问答、综述、论文解读、实验设计、文献证据整理都是场景，"
               "由检索、读取、核验、计算、组织回答等能力按需要组合；默认给出有来源、可复制的回答。"
               "报告、清单、方案、证据等词本身不意味着要文件；不要默认附HTML、Excel或Word。"
               "只有用户明确要求文件、下载、导出或指定文件格式时才生成相应文件；绘图请求交付真实图件。"
               "内部数据与脚本继续暂存，不自动作为附件。文件交付统一用deliver_research_artifact或现有实质工具的发布动作，"
               "底层校验入库后由资料库预览下载，下载不调用LLM。</delivery_policy>")
    # Optional query-specific instructions follow the stable capability blocks.
    return prompt + word_guidance


def verify_figure_prompt_loading() -> dict:
    """Exercise the production assembler, not merely the files on disk."""
    import hashlib
    from src.prompts.nature_figure import guidance, status
    prompt = assemble_system_prompt(mode="expert", query="",
                                    available_tools={"run_research_figure_script"})
    if "<nature_figure_guidance scope='initial_generation_only'>" not in prompt or guidance() not in prompt:
        raise RuntimeError("nature-figure 未进入实际系统提示词。")
    return status() | {"prompt_loaded": True,
                       "load_policy": "figure_capability_initial_core_unless_explicit_edit",
                       "scope": "initial_generation_only",
                       "assembled_prompt_chars": len(prompt),
                       "assembled_prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="按角色固定拼接 system prompt 并写入 builds/")
    ap.add_argument("--list", action="store_true", help="只打印各角色拼接长度，不写盘")
    args = ap.parse_args()

    # 测试桩：允许关闭写盘（--list 模拟）
    if args.list:
        prompts = {}
        for role, filenames in ROLE_SOURCE_FILES.items():
            prompts[role] = _concat_source_files(filenames)
            print(f"{role:10s} {len(prompts[role]):6d} chars  "
                  f"({len(filenames)} files)")
        for role, prompt in prompts.items():
            heads = [ln for ln in prompt.splitlines() if ln.startswith("#")]
            print(f"--- {role} headings ---")
            for h in heads:
                print("  " + h)
    else:
        built = build_fixed_prompts()
        for role, prompt in built.items():
            target = PROMPT_DIR / "builds" / BUILD_FILE_NAMES[role]
            print(f"built: {target} ({len(prompt)} chars)")
