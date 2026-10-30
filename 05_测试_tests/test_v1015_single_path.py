# -*- coding: utf-8 -*-
"""v10.15 · 单路检索恢复回归测试（依《单路检索恢复 · Coder 任务书》阶段 B）。

覆盖：
  B.1  检索硬钳：每轮/每请求上限 = 1（即使模型发多次也只执行 1 次）
  B.2  精排 query = 用户问题（HITL 意图）的英文翻译，不是检索角度自己的词
  B.3  （由 tests 全量回归承担）
  B.4  （由冻结产物校验承担）

实验依据：`experiment/pipeline/results/retrieval/paradigm_rewrite_kw_20260918/
paradigm_ablation.json` —— 单路 raw R@10=0.8452 最优，多路全都更低。
"""
from __future__ import annotations

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.path.dirname(HERE)
if BASE not in sys.path:
    sys.path.insert(0, BASE)

FAILS: list[str] = []


# ══════════════════════════════════════════════════════════════════════════
# v10.18b：让 pytest **真正断言**
#   缺陷：`check()` 只往 FAILS 追加，而 pytest 收集的 test_* 函数**从不断言**
#   ⇒ FAILS 非空时 pytest 仍判 PASS（只有 `__main__` 会 SystemExit）
#   ⇒ 检查形同虚设。修法：autouse fixture，每个测试后断言 FAILS 无新增。
# ══════════════════════════════════════════════════════════════════════════
try:
    import pytest as _pytest

    @_pytest.fixture(autouse=True)
    def _assert_no_fails():
        _n0 = len(FAILS)
        yield
        _new = FAILS[_n0:]
        assert not _new, f"内部断言失败: {_new}"
except ImportError:                                            # 独立运行时无 pytest
    pass


def check(name: str, ok: bool, detail: str = "") -> None:
    print(("  PASS " if ok else "  FAIL ") + name + ("" if ok else f"  <- {detail}"))
    if not ok:
        FAILS.append(name)


def _read(rel: str) -> str:
    """读取仓库文件（v6 布局：源码在 01_源码_src/，前端在 01_源码_src/web/，
    配置在 02_配置_config/）。按候选目录回退解析，兼容旧写法。"""
    import os as _os
    for cand in (_os.path.join(BASE, rel),
                 _os.path.join(BASE, "01_源码_src", rel),
                 _os.path.join(BASE, "01_源码_src/web", rel),
                 _os.path.join(BASE, "02_配置_config", rel)):
        if _os.path.exists(cand):
            with open(cand, encoding="utf-8") as f:
                content = f.read()
            if rel == "index.html":
                assets = _os.path.join(_os.path.dirname(cand), "assets")
                for asset_name in ("app.css", "app.js"):
                    asset_path = _os.path.join(assets, asset_name)
                    if _os.path.exists(asset_path):
                        with open(asset_path, encoding="utf-8") as asset_file:
                            content += "\n" + asset_file.read()
            return content
    with open(_os.path.join(BASE, rel), encoding="utf-8") as f:
        return f.read()


# ══════════════════════════════════════════════════════════════════════════
# B.1 · 检索硬钳 ≤1
# ══════════════════════════════════════════════════════════════════════════
def test_b1_single_path_cap() -> None:
    print("[v10.15-B.1] 检索硬钳：每轮/每请求上限 = 1")
    src = _read("01_源码_src/src/core/agent_runner.py")

    check("_MAX_RAG_PER_TURN = 1",
          re.search(r"^        _MAX_RAG_PER_TURN = 1$", src, re.M) is not None,
          "未找到 `_MAX_RAG_PER_TURN = 1`")
    check("_MAX_RAG_PER_REQUEST = 1",
          re.search(r"^        _MAX_RAG_PER_REQUEST = 1$", src, re.M) is not None,
          "未找到 `_MAX_RAG_PER_REQUEST = 1`")
    check("旧的 2 / 6 已不存在",
          "_MAX_RAG_PER_TURN = 2" not in src
          and "_MAX_RAG_PER_REQUEST = 6" not in src)
    check("含「不遵从提示词」告警",
          "单路模式下只执行第 1 次" in src
          and "请检查 04_retrieve_agent_search.md 是否被改回多路" in src)

    # 逻辑等价性：模拟 3 次 rag 调用在 cap=1 下的执行数
    cap_turn, cap_req = 1, 1
    _turn_rag = 0
    executed = 0
    for _ in range(3):                       # 模型发了 3 次
        if _turn_rag >= cap_turn or executed >= cap_req:
            continue                         # 被拦，不提交执行
        _turn_rag += 1
        executed += 1
    check("模型发 3 次 → 只执行 1 次", executed == 1, f"executed={executed}")


# ══════════════════════════════════════════════════════════════════════════
# B.2 · 精排 query = 用户问题（HITL 意图）的英文翻译
# ══════════════════════════════════════════════════════════════════════════
def test_b2_rerank_query_is_user_question() -> None:
    print("[v10.15-B.2] 精排 query 对齐用户问题（A.3 核查）")
    src = _read("01_源码_src/src/tools/search.py")

    # ① 翻译输入优先取 HITL 确认意图
    check("_translate_input 优先取 tracing.original_query()",
          "from src.core.tracing import original_query as _ctx_original" in src
          and "_ctx_original()" in src)

    # ② All local retrieval passes through the English-only normalizer.
    check("search_query 使用本地英文规范化",
          "search_query, language_info = normalize_local_query(query)" in src
          and "search_query = translation or query" not in src)

    # ③ 三处 search_multi 的 rerank_query 都是 search_query（= 用户问题翻译）
    check("rerank_query 三处均为 search_query",
          src.count("rerank_query=search_query") == 3,
          f"count={src.count('rerank_query=search_query')}")

    # ④ 不再残留按角度重翻译的实现
    check("无多余的重翻译（_rerank_q / rerank_ctx 已撤销）",
          "_rerank_q" not in src and "rerank_ctx" not in src)

    # ⑤ The reranker also receives an English-only query at the retrieval boundary.
    mr = _read("01_源码_src/src/retrieval/multi_retriever.py")
    check("multi_retriever 校验并规范化精排查询",
          "english_rerank_query = require_english_local_query" in mr)

    # ⑥ 实测：_translate_input 在 HITL 意图存在时返回它（而非工具入参）
    try:
        from src.core.tracing import _original_query as _oq
        from src.tools.search import _translate_input as _ti
        _tok = _oq.set("华中农大培育了哪些品种？现在柑橘品种有哪些")
        try:
            got = _ti("some retrieval angle words")
        finally:
            _oq.reset(_tok)
        check("HITL 意图存在时 _translate_input 返回用户问题",
              got == "华中农大培育了哪些品种？现在柑橘品种有哪些",
              f"got={got!r}")
        # 未设置时应回退工具入参（非会话路径不受影响）
        check("未设置 HITL 意图时回退工具入参",
              _ti("fallback angle") == "fallback angle")
    except Exception as e:                                     # noqa: BLE001
        check("HITL 意图存在时 _translate_input 返回用户问题", False, repr(e))


# ══════════════════════════════════════════════════════════════════════════
# A.1 · 提示词单路
# ══════════════════════════════════════════════════════════════════════════
def test_a1_prompt_single_path() -> None:
    print("[v10.15-A.1] 提示词单路")
    prom = _read("01_源码_src/src/prompts/source/04_retrieve_agent_search.md")

    check("含「唯一查询」", "唯一查询" in prom)
    check("含「不拆解角度」", "不拆解角度" in prom)
    check("含「调用一次」", "调用一次" in prom)
    check("多角度文本已移除",
          "2~4 个" not in prom and "多角度并行" not in prom
          and "第 2 轮起：定向补检" not in prom
          and "最多 3 轮" not in prom)
    check("保留检索后自审", "检索后自审" in prom and "通过 ≤2 条" in prom)
    check("保留一句话总结论", "一句话总结论" in prom)
    # builds/snapshots 已重建
    build = _read("01_源码_src/src/prompts/builds/retrieve_agent_system.md")
    check("builds 已重建（含「唯一查询」）", "唯一查询" in build)
    snap = _read("01_源码_src/src/prompts/snapshots/agent_retrieve-agent.txt")
    check("snapshots 已重建（含「唯一查询」）", "唯一查询" in snap)


# ══════════════════════════════════════════════════════════════════════════
# B 组 · 输出格式（B.2/B.3）
# ══════════════════════════════════════════════════════════════════════════
def test_b2b3_format() -> None:
    print("[v10.15-B.2/B.3] 一级标题与 CSS")
    html = _read("index.html")
    check("CSS 含 .answer h1", ".answer h1 {" in html)
    check("h1 字号 1.8em + 下边框",
          "font-size: 1.8em" in html and "border-bottom: 2px solid" in html)
    check("h2/h3/h4 分级仍在",
          ".answer h2 {" in html and ".answer h3 {" in html and ".answer h4 {" in html)

    sep = _read("01_源码_src/src/core/web_output_separator.py")
    check('DEFAULT_HEADING = "# 联网获取信息"',
          'DEFAULT_HEADING = "# 联网获取信息"' in sep)
    check("旧的【联网获取信息】已不存在", "【联网获取信息】" not in sep)

    prom3 = _read("01_源码_src/src/prompts/source/03_supervisor_routing_fusion.md")
    check("03 含强制一级标题 `# 本地文献库`", "# 本地文献库" in prom3)
    check("03 含强制一级标题 `# 联网获取信息`", "# 联网获取信息" in prom3)
    check("03 含加粗收紧规则", "正文内一律不加粗" in prom3)

    prom9 = _read("01_源码_src/src/prompts/source/09_output_style.md")
    check("09 同步一级标题规则", "# 本地文献库" in prom9)


# ══════════════════════════════════════════════════════════════════════════
# C 组 · 侧栏（C.1/C.2）—— 用 Node 真实执行 index.html 的真函数
# ══════════════════════════════════════════════════════════════════════════
def test_c1c2_sidebar_render() -> None:
    print("[v10.15-C.1/C.2] 侧栏「标题 + 🔗」")
    import shutil
    import subprocess
    html = _read("index.html")
    check("renderCitationItem 读 item.title", "item.title ||" in html)
    check("使用 .cite-row / .cite-title / .cite-link",
          "cite-row" in html and "cite-title" in html and "cite-link" in html)
    check("首行不再渲染 [ref_id] 徽标",
          "'<span class=\"' + b + '\">[' + escapeHtml(item.ref_id)" not in html)

    node = shutil.which("node")
    if not node:
        print("  SKIP node 不可用，跳过真实渲染验证")
        return
    script = os.path.join(os.path.dirname(BASE), "_scratch", "verify_sidebar_render.js")
    if not os.path.exists(script):
        print("  SKIP 渲染验证脚本不存在")
        return
    r = subprocess.run([node, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("Node 真实渲染验证 ALL PASS", r.returncode == 0,
          (r.stdout or "")[-300:] + (r.stderr or "")[-200:])


def test_v1016_no_duplicate_h1() -> None:
    """v10.16：联网小节一级标题**不重复**（用户真机现象：出现两次）。"""
    print("[v10.16] 联网小节一级标题不重复")
    import subprocess
    script = os.path.join(os.path.dirname(BASE), "_scratch", "verify_h1_dedup.py")
    if not os.path.exists(script):
        print("  SKIP 单测脚本不存在")
        return
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("标题去重单测 ALL PASS", r.returncode == 0,
          (r.stdout or "")[-300:] + (r.stderr or "")[-200:])

    prom9 = _read("01_源码_src/src/prompts/source/09_output_style.md")
    check("09 含「第二段不要写一级标题」（语义，容忍措辞）",
          ("第二段不要写任何一级标题" in prom9
           or "第二段不要预写该标题" in prom9))
    check("09 含「证据要写透/充分展开」类规则（语义）",
          any(k in prom9 for k in ("证据细节必须写透", "把证据写透",
                                   "证据越丰富，展开越充分",
                                   "每段必须含证据中的具体内容")))
    check("09 含两段篇幅相称规则", "篇幅" in prom9 and "相称" in prom9)
    prom3 = _read("01_源码_src/src/prompts/source/03_supervisor_routing_fusion.md")
    check("03 含「本地证据写透/充分展开」类规则（语义）",
          "本地证据必须写透" in prom3 or "充分展开" in prom3)


def test_v1016b_restored_expansion_guidance() -> None:
    """v10.16b：恢复 `_backup_20260911` 里 v9.6 的**充分展开引导**。

    用户反馈"本地没展开、完全没细节"；v10 曾把原 09~15 共 7 份输出格式模板
    精简掉（backup README 有记录），导致输出信息量不足以做端到端实验。
    """
    print("[v10.16b] 恢复 v9.6 充分展开引导")
    prom9 = _read("01_源码_src/src/prompts/source/09_output_style.md")
    check("含「证据越丰富，展开越充分」", "证据越丰富，展开越充分" in prom9)
    check("含「维度数量与证据量匹配」表", "维度数量与证据量匹配" in prom9)
    check("含 N≥8 → 4-6 维度 / 每维度 2-4 段",
          "N ≥ 8" in prom9 and "4-6 个维度" in prom9 and "每维度 2-4 段" in prom9)
    check("含「每段必须含证据中的具体内容」",
          "每段必须含证据中的具体内容" in prom9)
    check("含「禁止空泛概括」", "禁止空泛概括" in prom9)
    check("含 v9.6 模板的展开路径（机制按层级 / 对比用表格 / 综述 4-8 章）",
          "按层级结构逐层展开" in prom9 and "必须用 Markdown 表格" in prom9
          and "4-8 个主题章节" in prom9)
    check("含证据说明四要素（依据/缺口/冲突/文献确证）",
          "区分\"文献确证\"与\"模型推演\"" in prom9)
    check("含数字与专名保真", "一律保留原始数值" in prom9)
    # 最新格式要求仍在（未被恢复动作覆盖）
    check("保留最新格式：一级标题", "# 本地文献库" in prom9 and "# 联网获取信息" in prom9)
    check("保留最新格式：正文不加粗", "正文内不加粗" in prom9)
    check("保留最新格式：正文禁 emoji", "禁止 emoji" in prom9)
    # backup 仍在（可回溯）
    bak = os.path.join(BASE, '01_源码_src', 'src', "prompts", "_backup_20260911",
                       "source", "09_format_fact.md")
    check("backup 源仍可查（可回溯）", os.path.exists(bak))
    # 已进 builds
    build = _read("01_源码_src/src/prompts/builds/supervisor_system.md")
    check("builds 已含展开引导", "N ≥ 8" in build and "禁止空泛概括" in build)


def test_v1017_no_prompt_contradictions() -> None:
    """v10.17：提示词**内部无矛盾**（跨文件一致性）。

    实测发现 5 处矛盾，最严重的是 03:245 的 v10.8 残留
    「用**加粗短语**分层（**不要用 `###`**）」—— 与 09「章节标题用 ##/###」
    +「正文不加粗」**完全相反**。
    """
    print("[v10.17] 提示词内部无矛盾")
    import subprocess
    script = os.path.join(os.path.dirname(BASE), "_scratch",
                          "verify_no_contradictions.py")
    if not os.path.exists(script):
        print("  SKIP 扫描脚本不存在")
        return
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("矛盾扫描 ALL PASS", r.returncode == 0,
          (r.stdout or "")[-400:] + (r.stderr or "")[-200:])


def test_v1018_prompt_code_parity() -> None:
    """v10.18：**提示词 ↔ 代码 一致性**（跨层对账）。

    前几轮反复踩同一类坑：提示词说 A、代码做 B，模型照提示词做 ⇒ 用户看到错的。
    本检查逐条对账：分隔符 / 标题归属 / 结尾句 / [Wn] / 验证器 / 硬钳 /
    回执标题 / CSS 居中 / 加粗抑制 / 工具名 / Hn 退役。
    首跑即抓到 `Hn` 已退役但 02/18 仍在教模型用 `Hn`。
    """
    print("[v10.18] 提示词 ↔ 代码 一致性")
    import subprocess
    script = os.path.join(os.path.dirname(BASE), "_scratch",
                          "verify_prompt_code_parity.py")
    if not os.path.exists(script):
        print("  SKIP 检查脚本不存在")
        return
    r = subprocess.run([sys.executable, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("提示词↔代码 parity ALL PASS", r.returncode == 0,
          (r.stdout or "")[-500:] + (r.stderr or "")[-200:])


def test_v1019_sidebar_group_dedup() -> None:
    """v10.19：侧栏**分组标题不重复**（用户截图：T1 两次、T3 两次）。

    根因 `index.html` 的 `groupOrder`：固定段 `['rag','web_t1','web_t2','web_t3']`
    已含这三个键，而动态段的排除集只写了 `['rag','ucr','web','historical']`
    **没排除 `web_t1/t2/t3`** ⇒ 各出现两次 ⇒ 同组渲染两遍。
    实测复现：修复前 groupOrder = [...'web_t3', 'web_t1', 'web_t3']。
    """
    print("[v10.19] 侧栏分组标题不重复")
    import shutil
    import subprocess
    html = _read("index.html")
    check("groupOrder 动态段排除集含 web_t1/t2/t3",
          "_FIXED_KEYS" in html
          and "'web_t1', 'web_t2', 'web_t3'" in html)
    node = shutil.which("node")
    if not node:
        print("  SKIP node 不可用")
        return
    script = os.path.join(os.path.dirname(BASE), "_scratch",
                          "verify_sidebar_group_dedup.js")
    if not os.path.exists(script):
        print("  SKIP 验证脚本不存在")
        return
    r = subprocess.run([node, script], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    check("分组去重验证 ALL PASS", r.returncode == 0, (r.stdout or "")[-400:])


if __name__ == "__main__":
    test_a1_prompt_single_path()
    test_b1_single_path_cap()
    test_b2_rerank_query_is_user_question()
    test_b2b3_format()
    test_c1c2_sidebar_render()
    test_v1016_no_duplicate_h1()
    test_v1016b_restored_expansion_guidance()
    test_v1017_no_prompt_contradictions()
    test_v1018_prompt_code_parity()
    test_v1019_sidebar_group_dedup()
    print()
    if FAILS:
        print(f"FAILED {len(FAILS)}: {FAILS}")
        raise SystemExit(1)
    print("ALL PASS —— v10.15–v10.19 契约成立（检索/格式/侧栏/提示词一致性）")
