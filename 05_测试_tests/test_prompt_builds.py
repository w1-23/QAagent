# -*- coding: utf-8 -*-
"""v9.0/v9.1 固定 system prompt 架构回归测试 + v10.6 分离输出架构验收。

验证 "source/ 源文件 + 启动时固定拼接 + 进程级缓存" 的核心不变量：
  1. 角色 → 源文件映射完整且章节顺序符合设计；
  2. 每次请求复用同一字符串：format_hint / query 不影响内容，进程内缓存稳定；
  3. builds/ 落盘文件与运行时 getter 输出一致（KV cache 前缀字节级复用前提）；
  4. 全部源文件存在且非空。

**v10.6（2026-09）架构变更后的修订**：本文件原先断言 v10.0 之前的旧结构
（`source/` 21 份、含 7 份 `09~15_format_*.md`、Supervisor 14 个章节、标题
"Supervisor 决策与证据融合"、"证据来源仲裁与引用规则"）。这些断言自 v10.0
合并格式模板起即已失效，长期挂在失败清单里；本轮架构改为"本地与联网分离输出"，
故一并改为**从映射表推导期望值**的结构断言（标题改名不再让测试变脆），
并新增 PB-07：把"无融合/仲裁/合并指令、改为分离语义"这条验收标准机器化。

全部离线，无模型、无网络。
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.prompts import loader

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(name + (f" {detail}" if detail else ""))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")


def _first_h1(text: str) -> str:
    for ln in text.splitlines():
        if ln.startswith("# ") and not ln.startswith("## "):
            return ln
    return ""


def _source_dir() -> Path:
    return Path(loader.PROMPT_DIR) / "source"


def test_pb01_source_files_complete():
    print("[PB-01] source/ 源文件齐全且非空（期望集合由映射表推导）")
    source_dir = _source_dir()
    files = {p.name for p in source_dir.glob("*.md")}
    # 期望集合 = 映射表里出现过的全部源文件（唯一维护入口），不再硬编码旧清单
    expected = {fn for fns in loader.ROLE_SOURCE_FILES.values() for fn in fns}
    check("映射表引用的源文件全部存在", expected <= files,
          f"missing={sorted(expected - files)}")
    check("source/ 无游离文件（未被任何角色引用）", files == expected,
          f"orphan={sorted(files - expected)}")
    check("源文件数 = 映射表去重数", len(files) == len(expected),
          f"{len(files)} vs {len(expected)}")
    for name in sorted(expected):
        check(f"源文件非空: {name}", (source_dir / name).stat().st_size > 50)


def test_pb02_role_mapping_orders():
    print("[PB-02] 角色→源文件映射与章节顺序（由映射表推导）")
    prompts = loader.ensure_fixed_prompts(reload=True)

    for role, filenames in loader.ROLE_SOURCE_FILES.items():
        heads = [ln for ln in prompts[role].splitlines()
                 if ln.startswith("# ") and not ln.startswith("## ")]
        # 每个源文件贡献且仅贡献一个一级标题，且顺序与映射表一致
        want = [_first_h1((_source_dir() / fn).read_text(encoding="utf-8"))
                for fn in filenames]
        check(f"{role}: 一级标题数 = 源文件数({len(filenames)})",
              len(heads) == len(filenames), f"{len(heads)}: {heads}")
        check(f"{role}: 章节顺序与映射表一致", heads == want,
              f"got={heads} want={want}")

    check("Supervisor 含分离输出章节",
          any("分离输出" in h for h in
              [ln for ln in prompts["supervisor"].splitlines() if ln.startswith("# ")]))
    check("Retrieve 含检索执行 Agent 章节",
          "# 检索执行 Agent（Retrieve-Agent）" in prompts["retrieve"])
    check("Write 含 Plan/Execute 章节",
          "# 综述写作规划器（Plan 阶段）" in prompts["write"]
          and "# 综述单章撰写（Execute 阶段）" in prompts["write"])


def test_pb03_fixed_and_cached():
    print("[PB-03] 固定字符串 + 进程级缓存（KV cache 复用前提）")
    from src.prompts.loader import assemble_system_prompt, build_dynamic_blocks

    s1 = assemble_system_prompt(mode="expert", format_hint="fact", query="Q1")
    s2 = assemble_system_prompt(mode="expert", format_hint="review", query="Q2")
    check("不同 format_hint/query → 同一字符串", s1 == s2)
    check("Supervisor 提示词含输出规范章节", "输出规范" in s1)
    check("Supervisor 提示词含引用规则章节", "引用规则" in s1)
    check("无动态格式块（恒空串）", build_dynamic_blocks(format_hint="fact", query="Q") == "")

    # 进程级缓存：同一角色两次取引同一缓存对象
    a = loader.get_system_prompt("supervisor")
    b = loader.get_system_prompt("supervisor")
    check("supervisor 缓存对象稳定", a is b)

    # reload 重建后字节级一致（确定性拼接）
    rebuilt = loader.ensure_fixed_prompts(reload=True)
    check("reload 后字节级一致", rebuilt["supervisor"] == s1
          and rebuilt["retrieve"] == loader.get_system_prompt("retrieve")
          and rebuilt["write"] == loader.get_system_prompt("write"))


def test_pb07_separation_architecture():
    """v10.6 验收标准：无"融合/仲裁/合并"指令，改为分离语义。

    ⚠️ 不能直接断言 `"融合" not in prompt` —— `20_terminology_domain.md` 里有
    「无融合生殖（珠心胚/合子胚）」，那是**农学术语**（apomixis），不是架构指令；
    同理 `07_review_chapter_writer.md` 的「全文合并」指**章节合并**。
    故此处只断言**具体的指令句式**，不按词根扫。
    """
    print("[PB-07] v10.6 分离输出架构：旧仲裁指令已清除、分离语义已就位")
    prompts = loader.ensure_fixed_prompts(reload=True)
    sv = prompts["supervisor"]

    # ⚠️ 下列三项**刻意不在** forbidden 里：它们被新规则当作"被否定的旧规则"引用
    #   （"不存在'时效优先联网'或'学术优先本地'的规则"、"不由 Supervisor 仲裁"），
    #   按短词扫会误报。改在下面单独断言"只以否定形式出现"。
    forbidden = [
        "证据来源仲裁与引用规则",   # 旧章节标题
        "证据融合与仲裁规则",       # 旧章节标题
        "时效信息优先联网",         # 旧跨源优先级（指令句）
        "学术参数/历史数据优先本地",  # 旧跨源优先级（指令句）
        "冲突并列展示",             # 旧冲突处置指令
        "禁止捏造折中值",           # 旧冲突处置指令
        "联网默认可信",             # 旧规则 5（最危险：默认信任联网）
        "两路证据冲突时按仲裁规则消解",
        "按仲裁规则收敛",
        "来自联网的事实必须挂",
        "来自联网的事实挂 [Wn]",
    ]
    for phrase in forbidden:
        check(f"Supervisor 已无旧指令「{phrase}」", phrase not in sv)

    # 被否定的旧规则：只允许以"否定形式"出现（存在 = 正在被否掉，属正确写法）
    check("旧跨源优先级仅以否定形式出现",
          '不存在"时效优先联网"或"学术优先本地"的规则' in sv)
    check("Supervisor 不再由它自己仲裁（仅以否定形式出现）",
          "不由 Supervisor 仲裁" in sv)

    required = [
        "两段输出规范",
        "不合并",
        "不做跨源仲裁",
        "禁止引用 `[Wn]`",
        "由用户自行判断",
    ]
    for phrase in required:
        check(f"Supervisor 含分离语义「{phrase}」", phrase in sv)

    # 联网检索员：只取回、不合并
    web = prompts["web"]
    check("Web-Agent 明确不合并本地证据", "把联网内容与本地证据混写" in web)
    # 分离语义不得污染与联网无关的角色
    check("Lite 不含分离输出章节", "两段输出规范" not in prompts["lite"])
    check("Write 不含分离输出章节", "两段输出规范" not in prompts["write"])

    # 三个曾在正文里要求挂 [Wn] 的位置都必须改掉
    for role in ("supervisor", "retrieve", "lite", "web"):
        body = prompts[role]
        check(f"{role}: 不再要求正文挂 [Wn]",
              "来自联网搜索的信息，标注 [Wn] 编号" not in body)


def test_pb04_builds_on_disk():
    print("[PB-04] builds/ 落盘文件与 getter 一致")
    builds_dir = Path(loader.PROMPT_DIR) / "builds"
    prompts = loader.ensure_fixed_prompts()
    for role, fname in loader.BUILD_FILE_NAMES.items():
        target = builds_dir / fname
        check(f"builds/{fname} 存在", target.exists())
        on_disk = target.read_text(encoding="utf-8").strip()
        check(f"builds/{fname} 与缓存一致", on_disk == prompts[role].strip(),
              f"disk={len(on_disk)} vs cache={len(prompts[role])}")


def test_pb05_agent_role_fixed_prompts():
    print("[PB-05] 子 Agent 固定提示词内容正确")
    from src.prompts.loader import assemble_agent_prompt

    ra = assemble_agent_prompt("retrieve-agent")
    wa = assemble_agent_prompt("write-agent")
    aa = assemble_agent_prompt("analyze-agent")
    check("retrieve 含检索执行 Agent 章节", "# 检索执行 Agent（Retrieve-Agent）" in ra)
    check("retrieve 不重复 Supervisor 格式模板", "输出规范" not in ra)
    check("write 含写作规范章节", "# 学术写作通用规范（Write-Agent）" in wa)
    check("retrieve/write 均含全局角色", "你是柑橘科研助手" in ra and "你是柑橘科研助手" in wa)
    check("write/retrieve 固定不随 skills 变化",
          assemble_agent_prompt("write-agent", skills=["x"]) == wa)
    check("analyze 保留位含结果归因", "结果归因" in aa)


def test_pb06_lite_prompt():
    print("[PB-06] Lite 固定提示词（01+02+19+20）")
    lite = loader.get_system_prompt("lite")
    check("lite 含轻量模式规则", "轻量模式" in lite)
    check("lite 不含 Supervisor 分离输出章节", "两段输出规范" not in lite)
    check("lite 固定", lite == loader.get_system_prompt("lite"))


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
    print(f"\nprompt-builds tests: {len(passed)} passed, {len(failed)} failed")
    if failed:
        print("FAILED:", failed)
        sys.exit(1)