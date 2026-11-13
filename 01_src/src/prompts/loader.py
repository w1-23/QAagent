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
) -> str:
    """Keep the QA prefix fixed; load Word rules only for a document request."""
    prompt = get_system_prompt("supervisor")
    from src.graph.export_contract import deliverable_contract
    needs_word = any("docx" in r.get("formats", []) for r in deliverable_contract(query or ""))
    if needs_word and (available_tools is None or "word_document" in available_tools):
        rules = _read_prompt_cached("skills/word-export/SKILL.md")
        if not rules:
            raise RuntimeError("Word 写作规范文件缺失，不能声明已加载该能力。")
        prompt += "\n\n<word_export_guidance>\n" + rules + (
            "\n工具映射：使用 word_document(operation=...)，不要执行 Python。"
            "create 的 outline 每项为 {section_id,title}；create 也须有唯一 batch_id。"
            "每节写 heading 和正文后提交 section_end，整篇最后提交 document_end。"
            "finalize 传 expected_version、expected_last_block_id 和 .docx 文件名。"
            "inspect 可分页恢复块状态。草稿动作不产生下载文件；仅 finalize 成功才交付。"
            "本版本仅支持本工具草稿按块修改；不支持任意上传 DOCX 的保真编辑或模板导入，须如实说明。"
            "\n</word_export_guidance>")
    # 统计与绘图规范按"工具是否可用"加载，而不是按关键词。
    # 关键词门控会重演"短问句丢检索工具"的老问题：deliverable_contract 分不清
    # "要一张图"和"要一份报告"，因此这里只看环境事实。
    if available_tools is None or "analyze_research_data" in available_tools:
        rules = _read_prompt_cached("skills/statistics/SKILL.md")
        if not rules:
            raise RuntimeError("统计规范文件缺失，不能声明已加载该能力。")
        prompt += "\n\n<statistics_guidance>\n" + rules + (
            "\n工具映射：analyze_research_data(file_index=..., sheet_name=..., "
            "header_row=..., group_by_json=..., metrics_json=..., test=..., "
            "paired_by_json=..., alpha=..., output_format=...)。"
            "数值一律由工具计算，不得心算；显著性必须同时给出效应量与多重比较校正后的 p 值。"
            "\n</statistics_guidance>")
    if available_tools is None or "create_research_figure" in available_tools:
        rules = _read_prompt_cached("skills/figure-export/SKILL.md")
        if not rules:
            raise RuntimeError("图形导出规范文件缺失，不能声明已加载该能力。")
        prompt += "\n\n<figure_export_guidance>\n" + rules + (
            "\n工具映射：优先 create_research_figure(spec_json=...)，"
            "spec 只描述图，不含可执行代码；"
            "只有用户明确要求自定义 matplotlib 绘图逻辑时才用 run_research_figure_script(script=...)，"
            "该工具不可见时如实说明并改用声明式路径。"
            "图内微调在 /figures 工作台完成，不在聊天里改图。"
            "\n</figure_export_guidance>")
    return prompt


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
