# -*- coding: utf-8 -*-
"""v8.16.2 回答质量修复回归：回执重排（截尾防护）/ cap 扩容 / 数字专名保真规则 /
草稿业务日志 / load 分段计时插桩。

全部离线、无模型、无网络（回执为纯函数，容量以实测数据编码为回归护栏）。
覆盖：
  VF-25  回执重排 + 容量护栏（rich 场景 47.6K 必须 ≤ cap 且 [Wn]/导引前置）
  VF-26  cap 60000 + 源码接线（draft_done/draft_skipped 业务日志、load_stages 插桩）
  VF-27  decision_guide 数字与专名保真规则落地
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import settings

passed, failed = [], []


def check(name, cond, detail=""):
    if cond:
        passed.append(name)
    else:
        failed.append(name)
        if os.environ.get("PYTEST_CURRENT_TEST"):
            raise AssertionError(name + (f" {detail}" if detail else ""))
    print(f"  {'PASS' if cond else 'FAIL'}  {name}  {detail}")


def _arts(n_docs, chunk_len, n_web_sum=0, n_web=0):
    """构造符合生产形态的回执输入：chunk ≤2000（v8.4.6 语料约束），综述 ≤4000，
    联网条目 500 字摘要——与 v8.16.2 实测脚本同一数据形态。"""
    main = [{"title": f"Paper {i}", "doi": f"10.{i}/x", "year": 2024,
             "text": "x" * chunk_len} for i in range(n_docs)]
    ws = ["w" * 4000 for _ in range(n_web_sum)]
    web = [{"title": f"W{i}", "url": f"https://x/{i}", "abstract": "a" * 500}
           for i in range(n_web)]
    return {"main_results": main, "web_results": web, "web_summaries": ws}


# ── VF-25 回执重排 + 容量护栏（v8.16.2 Phase 1 核心）──────────────
def test_v8162_report_reorder():
    """v8.16.2 的**意图**（引用导引必须留在截断线上方、主证据不得被吞）保留；
    联网段的落点已按 v10.6 分离架构改变（联网内容不再进模型侧回执）。
    """
    print("[VF-25] build_evidence_report 重排 + 容量护栏（v10.6：联网段改走分离小节）")
    from src.core.agent_runner import build_evidence_report

    rich = _arts(15, 2000, 3, 8)
    rep = build_evidence_report(rich, "2026 citrus policy", 8)

    # v8.16.2 实测：rich 场景 47,587 chars，旧 cap 40000 截掉 7.6K。
    # v10.6：联网正文不再注入 → 回执显著变短，但**导引前置**这条不变量必须保持。
    i_guide = rep.find("引用编号")
    i_first = rep.find("[1][RAG]")
    check("引用导引前置（在本地主证据之前）", 0 < i_guide < i_first,
          f"{i_guide}/{i_first}")
    # v10.14（阶段 2 · 撤销隐藏入口③）：Supervisor **应当**看到联网原文（用于提炼
    # 联网小节），但**引用池隔离保留** ⇒ 仍不得出现 [W1] 编号。
    check("模型侧回执**含**联网段（阶段2 撤销隐藏后）", "## 网络综述" in rep)
    # v10.13-B：`[Wn]` 恢复 ⇒ 回执含 [W1] 编号
    check("模型侧回执**含** [W1] 编号（供识别引用）", "[W1]" in rep)
    # v10.14（阶段2 撤销隐藏入口③）：见 test_supervisor_final.py 同项说明
    check("模型侧回执含联网原文（阶段2 撤销隐藏后）",
          "## 网络综述" in rep or "联网检索结果" in rep or "联网取回" in rep)
    check("零截断（≤ cap）", len(rep) <= settings.TOOL_RESULT_CAPS["retrieve-agent"],
          f"{len(rep)} > {settings.TOOL_RESULT_CAPS['retrieve-agent']}")
    # 主证据完整（重排不改内容总量）
    check("主证据 15 篇齐全（[15] 在、[16] 不在）",
          "[15][RAG]" in rep and "[16][RAG]" not in rep)

    # 空结果分支不受影响
    empty = build_evidence_report({"main_results": [], "web_results": [],
                                   "web_summaries": []}, "q", 0)
    check("空结果分支保留", "未检索到相关文献。" in empty and "网络综述" not in empty)

    # 回退档（消融/回滚）：旧的注入顺序必须可恢复
    import os
    _old = os.environ.get("CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL")
    try:
        os.environ["CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL"] = "0"
        from src.core.web_output_separator import SEPARATION
        SEPARATION.refresh()
        rep_old = build_evidence_report(rich, "2026 citrus policy", 8)
        i_g2 = rep_old.find("引用编号请使用下列")
        i_s2 = rep_old.find("## 网络综述")
        i_w2 = rep_old.find("[W1]")
        i_f2 = rep_old.find("[1][RAG]")
        check("回退档恢复旧顺序（导引<综述<[W1]<主证据）",
              0 < i_g2 < i_s2 < i_w2 < i_f2, f"{i_g2}/{i_s2}/{i_w2}/{i_f2}")
        check("回退档 rich 场景仍零截断（护栏意义）",
              len(rep_old) > 40000
              and len(rep_old) <= settings.TOOL_RESULT_CAPS["retrieve-agent"])
        check("回退档 worst 残余截断不吞导引",
              "引用编号请使用下列" in build_evidence_report(
                  _arts(15, 3000, 3, 8), "q", 8))
    finally:
        if _old is None:
            os.environ.pop("CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL", None)
        else:
            os.environ["CITRUS_SEPARATE_HIDE_WEB_FROM_MODEL"] = _old
        SEPARATION.refresh()


# ── VF-26 cap + 源码接线 ──────────────────────────────────────────
def test_v8162_cap_and_wiring():
    print("[VF-26] cap 60000 + 接线断言")
    cap = settings.TOOL_RESULT_CAPS.get("retrieve-agent")
    check("retrieve-agent cap == 60000", cap == 60000, str(cap))

    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(src, '01_源码_src', 'src', "tools", "deepseek_web.py"),
              encoding="utf-8") as f:
        dw = f.read()
    check("草稿业务日志已删（v8.17.15 草稿全链删除）",
          'blog("draft_done"' not in dw and 'blog("draft_skipped"' not in dw
          and "def _draft_blog" not in dw)
    check("联网工具保留（deepseek_web_search）", "def deepseek_web_search" in dw)

    with open(os.path.join(src, '01_源码_src', 'src', "core", "context_manager.py"),
              encoding="utf-8") as f:
        cm = f.read()
    check("load 有 load_stages 分段插桩", 'diag("load_stages"' in cm)
    check("load 计时变量完备", "_t_load" in cm and "_t_hints0" in cm)


# ── VF-27 数字与专名保真规则 ─────────────────────────────────────
def test_v8162_prompt_rule():
    print("[VF-27] 全局数据保真含数字与专名保真")
    src = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(src, '01_源码_src', 'src', "prompts", "source", "02_global_data_fidelity_citation.md"),
              encoding="utf-8") as f:
        txt = f.read()
    check("含数字与专名保真规则（v8.16.2）",
          "数字与专名保真" in txt
          and "禁止把具体数字概括为" in txt
          and "保留原始数值" in txt)


# ── 汇总 ──────────────────────────────────────────────────────────
def _summary():
    print(f"\n[VF-16.2] PASS {len(passed)} / FAIL {len(failed)}"
          f"  ({len(passed) + len(failed)} total)")
    for f in failed:
        print(f"  [FAIL] {f}")
    return len(failed) == 0


if __name__ == "__main__":
    import types as _types
    _funcs = [v for v in list(globals().values())
              if isinstance(v, _types.FunctionType)
              and v.__name__.startswith("test_")]
    for _f in sorted(_funcs, key=lambda x: x.__name__):
        try:
            _f()
        except Exception as _e:
            if os.environ.get("PYTEST_CURRENT_TEST"):
                raise
            print(f"  {_e}")
    ok = _summary()
    sys.exit(0 if ok else 1)