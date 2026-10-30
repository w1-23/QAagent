# -*- coding: utf-8 -*-
"""增强包自检：①默认关闭时**完全惰性**；②打开时各模块功能生效；③不碰检索链路。

用法：python -m pytest agent/tests/test_enhance_pack.py -q
     或直接 python agent/tests/test_enhance_pack.py
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
# v6 布局：源码根是 <repo>/01_源码_src（其下才是 src/ 包）。
# HERE.parents[0] = 05_测试_tests/，HERE.parents[1] = 仓库根。
REPO = HERE.parents[1]
AGENT = REPO / "01_源码_src"
sys.path.insert(0, str(AGENT))

PASS, FAIL = [], []


def chk(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"   {detail}" if detail else ""))


@contextlib.contextmanager
def _env(**kv):
    """临时改环境变量并重载开关，**退出时原样还原**。

    必须还原：本文件与其它测试同会话运行时，残留的 CITRUS_* 会污染后面的测试
    （实测把 test_v86_features 的两项搞挂——已修）。
    """
    saved = {k: v for k, v in os.environ.items() if k.startswith("CITRUS_")}
    for k in saved:
        del os.environ[k]
    os.environ.update({k: str(v) for k, v in kv.items()})
    try:
        from src.enhance import flags as F
        yield F._Flags().reload()
    finally:
        for k in [k for k in os.environ if k.startswith("CITRUS_")]:
            del os.environ[k]
        os.environ.update(saved)


def test_default_off_is_inert():
    print("\n[EP-1] 默认关闭：所有开关为关、且 any_on()=False")
    with _env() as f:
        chk("support_check 默认关", f.support_check is False)
        chk("recheck 默认关", f.recheck is False)
        chk("web_fulltext 默认关", f.web_fulltext is False)
        chk("entity_norm 默认关", f.entity_norm is False)
        chk("dedup_granularity 默认 doi（与实验一致）", f.dedup_granularity == "doi")
        chk("evidence_level 默认关", f.evidence_level is False)
        chk("any_on() = False（主流程不会进入任何增强分支）", f.any_on() is False)


def test_switches_take_effect():
    print("\n[EP-2] 打开开关：读得到、且联动正确")
    with _env(CITRUS_ENABLE_SUPPORT_CHECK="1", CITRUS_SUPPORT_THRESHOLD="0.5",
              CITRUS_ENABLE_RECHECK="1", CITRUS_DEDUP_GRANULARITY="chunk") as f:
        chk("support_check 打开", f.support_check is True)
        chk("阈值读到 0.5", abs(float(f.support_threshold) - 0.5) < 1e-9)
        chk("recheck 随 support_check 生效", f.recheck is True)
        chk("evidence_level 随 support_check 生效", f.evidence_level is True)
        chk("dedup_granularity=chunk", f.dedup_granularity == "chunk")
        chk("any_on() = True", f.any_on() is True)
    with _env(CITRUS_ENABLE_RECHECK="1") as f2:            # 只开回检、不开校验
        chk("校验关闭时回检被强制关闭（无校验则无从回检）", f2.recheck is False)
    chk("环境变量已还原（不污染同会话其它测试）",
        not any(k.startswith("CITRUS_") for k in os.environ))


def test_split_claims():
    print("\n[EP-3] 论断切分：表格单元格 + 带引用句子，无引用的句子不算")
    from src.enhance.support_check import split_claims
    ans = ("SDE5 通过 PUB21 降解 MYC2[1]。这是一句没有引用的话。\n"
           "| 效应子 | 靶标 | 手段 |\n|---|---|---|\n"
           "| SDE2470 (CLIBASIA_02470) | CsVOZ2 | 互作+降解[2] |\n"
           "| 无引用单元格 | x | y |\n")
    cs = split_claims(ans)
    kinds = [c.kind for c in cs]
    chk("切出 2 条论断（1 句 + 1 单元格）", len(cs) == 2, f"实际 {len(cs)}：{kinds}")
    chk("含表格单元格", "cell" in kinds)
    chk("无引用的句子被排除", all("没有引用" not in c.text for c in cs))
    chk("无引用的单元格被排除", all("无引用单元格" not in c.text for c in cs))
    chk("引用编号解析正确", cs[0].ref_ids == ["1"] and cs[1].ref_ids == ["2"])


def test_number_gate():
    print("\n[EP-4] 数字硬门：把 A 论文的数字挂到 B 条目上 → 拦得住")
    from src.enhance.support_check import number_gate
    ok1, m1 = number_gate("清除率达到 55.32%[1]", "methanolic extract showed 55.32% at 300 μg/mL")
    chk("数字在原文里 → 通过", ok1 and not m1, f"missing={m1}")
    ok2, m2 = number_gate("清除率达到 88.88%[1]", "methanolic extract showed 55.32% at 300 μg/mL")
    chk("数字不在原文里 → 拦下", (not ok2) and "88.88%" in m2, f"missing={m2}")
    ok3, _ = number_gate("该基因参与免疫调控[1]", "any text")
    chk("论断无数字 → 不拦（交给相关性判据）", ok3)


def test_support_check_and_levels():
    print("\n[EP-5] 支持度校验 + 证据等级自动判定（注入 scorer，不加载模型）")
    from src.enhance.support_check import run_support_check
    answer = ("SDE19 在 ER 干扰 COPII 囊泡转运[1]。"
              "清除率达到 88.88%[1]。")
    refs = [{"ref_id": "1",
             "text_preview": "SDE19 interacts with sweet orange Sec12 (a Sar1 GEF) and "
                             "interferes with COPII vesicle trafficking; clearance was "
                             "55.32% at 300 μg/mL, confirmed by Y2H and BiFC."}]
    rep = run_support_check(answer, refs, threshold=0.5,
                            scorer=lambda c, e: 0.9 if "SDE19" in c else 0.2)
    chk("切出 2 条论断", rep["n_claims"] == 2, str(rep["n_claims"]))
    chk("1 条不支持（数字挂错的那条）", rep["n_unsupported"] == 1,
        f"unsupported={[u['text'][:24] for u in rep['unsupported']]}")
    chk("不支持的正是数字那条",
        "88.88" in (rep["unsupported"][0]["text"] if rep["unsupported"] else ""))
    chk("等级由代码判定而非模型自报",
        rep["level_counts"].get("直接实验", 0) >= 1, str(rep["level_counts"]))
    chk("不支持项等级为「原文未定级」",
        rep["unsupported"][0]["level"] == "原文未定级")


def test_recheck_loop():
    print("\n[EP-6] 回检闭环：重绑成功 / 回检失败则降级为「原文未记载」")
    from src.enhance.recheck import recheck_unsupported, apply_downgrade
    unsup = [{"text": "清除率达到 88.88%[1]", "ref_ids": ["1"], "locator": "line:0"},
             {"text": "某效应子抑制韧皮部转运[2]", "ref_ids": ["2"], "locator": "line:1"}]

    def fake_retrieve(q, k):
        if "88.88" in q:
            return [{"ref_id": "9", "title": "correct paper",
                     "text": "the clearance reached 88.88% under the assay"}]
        return []

    rep = recheck_unsupported(unsup, retrieve_fn=fake_retrieve, threshold=0.5,
                              scorer=lambda c, e: 0.95)
    chk("1 条重绑成功", rep["n_rebound"] == 1, str(rep["rebound"][:1]))
    chk("重绑拿到新编号", rep["rebound"] and rep["rebound"][0]["new_ref_id"] == "9")
    chk("1 条降级", rep["n_downgraded"] == 1)
    chk("降级同步释放编号（供 remap 更新）", rep["freed_ref_ids"] == ["2"],
        str(rep["freed_ref_ids"]))
    out = apply_downgrade("某效应子抑制韧皮部转运[2]", rep["downgraded"])
    chk("降级标注「（原文未记载）」", "（原文未记载）" in out, out[:40])


def test_entity_norm():
    print("\n[EP-7] 柑橘实体归一：CLIBASIA_02470 / SDE2470 / SDE5 → 同一对象")
    from src.enhance import entity_norm as E
    a = E.normalize("CLIBASIA_02470")
    b = E.normalize("SDE2470")
    c = E.normalize("SDE5")
    chk("三种写法归一到同一 ID", a == b == c == ["CLas.SDE2470"], f"{a} {b} {c}")
    ex = E.expand_query("SDE5 的宿主靶标是什么")
    _added = {str(t).upper() for t in ex["added_terms"]}
    chk("查询扩展补出同对象别名",
        "CLIBASIA_02470" in _added and "SDE2470" in _added,
        str(ex["added_terms"][:4]))
    chk("表格行主键跨写法一致",
        E.object_key("SDE5 (CLIBASIA_02470)") == E.object_key("SDE2470"))
    rels = E.extract_relations("SDE2470 与 CsVOZ2 互作，经 BRUTUS E3 降解，Y2H 验证")
    chk("抽出四元组（效应子/靶标/作用/手段）",
        rels and rels[0]["effector"] == "CLas.SDE2470"
        and rels[0]["host"] == "Cs.VOZ2" and rels[0]["action"] in ("互作", "降解"),
        str(rels[:1])[:120])
    conf = E.detect_conflicts([
        {"text": "SDE2470 抑制 CsVOZ2 通路", "source": "rag", "ref_ids": ["1"]},
        {"text": "SDE2470 促进 CsVOZ2 通路", "source": "web", "ref_ids": ["W1"]}])
    chk("同对象对矛盾自动识别（不靠提示词）",
        conf and conf[0]["kind"] == "action_conflict", str(conf[:1])[:120])
    st = E.stats()
    chk("映射表已加载", st["n_objects"] > 10 and st["n_aliases"] > 20, str(st))


def test_chunk_dedup():
    print("\n[EP-8] 块级去重 + 位置归属交代")
    from src.enhance.dedup_chunk import dedup_with_positions
    items = [
        {"paper_id": "p1", "chunk_index": 1, "doi": "10.1/x", "text": "chunk one"},
        {"paper_id": "p1", "chunk_index": 2, "doi": "10.1/x", "text": "chunk two"},
        {"paper_id": "p2", "chunk_index": 1, "doi": "10.2/y", "text": "other"},
    ]
    d_doi, _ = dedup_with_positions(items, "doi")
    d_chunk, notes = dedup_with_positions(items, "chunk")
    chk("doi 粒度：同 DOI 合并为 1 条（v9.6.3 口径）", len(d_doi) == 2, str(len(d_doi)))
    chk("chunk 粒度：同论文两块各自成条", len(d_chunk) == 3, str(len(d_chunk)))
    chk("位置归属有交代", notes and "rule" in notes[0], str(notes[0])[:80])
    rich = [{"paper_id": "p9", "chunk_index": 1, "text": ""},
            {"paper_id": "p9", "chunk_index": 1, "text": "更丰富"}]
    merged, n2 = dedup_with_positions(rich, "chunk")
    chk("碰撞时保留首次位置、内容取更丰富者",
        len(merged) == 1 and merged[0]["text"] == "更丰富"
        and n2[-1]["replaced"] is True)


def test_web_fulltext():
    print("\n[EP-9] 联网原文块管线（注入 fetch，不联网）")
    from src.enhance.web_fulltext import build_web_blocks, chunk_text, to_cited_ref
    html = ("<html><head><style>x{}</style></head><body><article>"
            "<p>" + "SDE19 与甜橙 Sec12 互作，干扰 COPII 囊泡转运。" * 60 + "</p>"
            "<p>验证手段：Y2H、BiFC。</p></article></body></html>")
    blocks, stat = build_web_blocks(
        [{"ref_id": "W1", "url": "https://example.org/a", "title": "T1",
          "snippet": "搜索片段"}],
        fetch_fn=lambda u: __import__("src.enhance.web_fulltext", fromlist=["x"])
        ._strip_html(html))
    chk("抓到正文 → 切成多块", stat["n_fetched"] == 1 and stat["n_blocks"] >= 2,
        str(stat))
    chk("块条目与本地证据同构（有 text / chunk_index）",
        all(b.get("text") and b.get("chunk_index") is not None for b in blocks))
    cr = to_cited_ref(blocks[0])
    chk("侧栏 text_preview 取块正文（不再是搜索片段）",
        cr["text_preview"].startswith("SDE19"), cr["text_preview"][:30])
    blocks2, stat2 = build_web_blocks(
        [{"ref_id": "W2", "url": "https://example.org/b", "title": "T2",
          "snippet": "片段兜底"}], fetch_fn=lambda u: None)
    chk("抓取失败 fail-open 退回片段、不丢条目",
        stat2["n_fallback"] == 1 and blocks2[0]["text"] == "片段兜底")
    chk("切块函数对长文产出多块", len(chunk_text("a" * 5000)) >= 4)


def test_meta_refresh_follow():
    print("\n[EP-9b] 跳转页跟随：doi.org 落地页（HTTP 200 但正文 0）→ 取出真实目标")
    from src.enhance.web_fulltext import _follow_meta_refresh
    # 真实页面形态：content 用双引号包裹、内部是单引号包住的 url（收尾是 '"/>）
    page = ('<html><head><meta HTTP-EQUIV="REFRESH" content="2; '
            "url='/retrieve/articleSelectPrefsPerm?Redirect=https%3A%2F%2F"
            "www.sciencedirect.com%2Fscience%2Farticle%2Fpii%2FS0092"
            "867413002225%3Fvia%253Dihub&amp;key=abc'\"/></head></html>")
    got = _follow_meta_refresh(page, "https://doi.org/10.1016/j.cell.2013.02.033")
    chk("从 meta refresh 取出并解码目标 URL",
        got == "https://www.sciencedirect.com/science/article/pii/"
               "S0092867413002225?via=ihub", str(got))
    chk("普通页面不误跟", _follow_meta_refresh(
        "<html><body>normal page</body></html>", "https://x/") is None)
    chk("相对跳转按 base 补全",
        (_follow_meta_refresh('<meta http-equiv="refresh" content="0; url=/a/b">',
                              "https://x.com/p") or "") == "https://x.com/a/b")


def test_no_retrieval_coupling():
    print("\n[EP-10] 零耦合：增强包不 import 任何检索实现（实验依赖不受影响）")
    import re
    src = AGENT / "src" / "enhance"
    bad = []
    for p in src.glob("*.py"):
        t = p.read_text(encoding="utf-8")
        for m in re.finditer(r"^\s*(?:from|import)\s+([\w\.]+)", t, re.M):
            mod = m.group(1)
            if mod.startswith(("src.retrieval", "src.engine.embedder",
                               "src.tools.search", "src.config")):
                bad.append(f"{p.name}:{mod}")
    chk("未 import retrieval/embedder/search/config",
        not bad, str(bad))
    # support_check 允许惰性 import reranker（只读、且已在 try 内）；确认是函数内 import
    t = (src / "support_check.py").read_text(encoding="utf-8")
    chk("reranker 是函数内惰性 import（模块顶层不加载模型）",
        "def _reranker_score" in t and t.index("from src.engine.reranker")
        > t.index("def _reranker_score"))


def test_full_text_enrichment():
    print("\n[EP-12] 全文补入：修 300 字预览导致的实体对截断（v10.1）")
    from src.enhance.pipeline import enrich_with_full_text
    # 构造：实体对出现在第 400 字符之后（模拟真实语料，效应子首现于 372/975 字符）
    filler = "Plants utilise a two-tiered innate immune system. " * 9   # ≈430 字符
    # 两块"真实语料式"的相反结论：一块抑制、一块促进（同一对象对）
    full_a = filler + "SDE2470 suppresses CsMYC2 in citrus, confirmed by Y2H."
    full_b = filler + "SDE2470 promotes CsMYC2 and enhances CLas survival."
    refs = [{"ref_id": 1, "type": "main", "source": "rag",
             "doi": "10.1/x", "title": "T1", "text_preview": full_a[:300]},
            {"ref_id": 2, "type": "main", "source": "rag",
             "doi": "10.2/y", "title": "T2", "text_preview": full_b[:300]}]
    main_results = [{"doi": "10.1/x", "title": "T1", "text": full_a},
                  {"doi": "10.2/y", "title": "T2", "text": full_b}]

    chk("预览确实截断（前 300 字里没有实体对）",
        all("SDE2470" not in r["text_preview"] for r in refs))
    enriched, st = enrich_with_full_text(refs, main_results)
    chk("两条都补入了 text_full", st.get("enriched") == 2, str(st))
    chk("text_full 含完整实体对",
        all("SDE2470" in e["text_full"] for e in enriched))
    chk("按 (doi,title) 匹配而非下标",
        enrich_with_full_text([{"ref_id": 9, "doi": "10.1/x", "title": "T1",
                                "text_preview": "x"}], main_results)[0][0]
        .get("text_full") is not None)
    chk("未提供 main_results 时原样返回",
        enrich_with_full_text(refs, None)[0] == refs)

    # 关键对照：只有预览 vs 补了全文，冲突检出结果不同
    import importlib
    import os
    saved = {k: v for k, v in os.environ.items() if k.startswith("CITRUS_")}
    try:
        for k in list(os.environ):
            if k.startswith("CITRUS_"):
                del os.environ[k]
        os.environ["CITRUS_ENABLE_ENTITY_NORM"] = "1"
        import src.enhance.flags as F
        import src.enhance.pipeline as P
        importlib.reload(F)
        importlib.reload(P)
        a = P.run_postprocess("答案", refs, main_results=None)          # 只有预览
        b = P.run_postprocess("答案", refs, main_results=main_results)    # 补了全文
        ea, eb = (a.get("entity_norm") or {}), (b.get("entity_norm") or {})
        n_a = len(ea.get("conflicts") or [])
        n_b = len(eb.get("conflicts") or [])
        chk("**补全文后检出冲突，只有预览时检不出**", n_b >= 1 and n_a == 0,
            f"预览冲突={n_a} 全文冲突={n_b}")
        chk("两条对齐到同一规范主键", eb.get("n_aligned", 0) >= 2,
            f"n_aligned={eb.get('n_aligned')}")
        chk("冲突对象含 CLas.SDE2470",
            bool(eb.get("conflicts"))
            and "SDE2470" in str((eb["conflicts"][0] or {}).get("object")),
            str((eb.get("conflicts") or [{}])[0].get("object")))
    finally:
        for k in list(os.environ):
            if k.startswith("CITRUS_"):
                del os.environ[k]
        os.environ.update(saved)
        import src.enhance.flags as F
        import src.enhance.pipeline as P
        importlib.reload(F)
        importlib.reload(P)


def test_entity_norm_default_off():
    print("\n[EP-13] B.2：entity_norm 默认必须为 False")
    with _env() as f:
        chk("entity_norm 默认 False", f.entity_norm is False)
        chk("query_expansion 默认 False", f.entity_query_expansion is False)
        chk("any_on() 为 False（主流程不进入增强分支）", f.any_on() is False)
    # 用干净环境重载后再验 run_postprocess（否则会用到上面测试留下的单例）
    import importlib
    import os
    saved = {k: v for k, v in os.environ.items() if k.startswith("CITRUS_")}
    try:
        for k in list(os.environ):
            if k.startswith("CITRUS_"):
                del os.environ[k]
        import src.enhance.flags as F
        import src.enhance.pipeline as P
        importlib.reload(F)
        importlib.reload(P)
        r = P.run_postprocess("答案", [{"ref_id": 1, "text_preview": "x"}],
                              main_results=[{"text": "y"}])
        chk("默认关闭时返回 {'enabled': False}", r == {"enabled": False},
            str(r)[:120])
    finally:
        os.environ.update(saved)
        import src.enhance.flags as F
        import src.enhance.pipeline as P
        importlib.reload(F)
        importlib.reload(P)


def test_experiment_deps_untouched():
    print("\n[EP-11] 本包未触碰实验依赖文件（按**提交**核对，非工作区状态）")
    files = ["agent/src/config.py", "agent/config.yaml",
             "agent/src/retrieval/bm25.py", "agent/src/retrieval/multi_retriever.py",
             "agent/src/engine/reranker.py", "agent/src/engine/embedder.py",
             "agent/src/engine/hardware.py", "agent/src/tools/search.py"]
    repo = str(AGENT.parent)
    try:
        # 判据必须是"**本包**没改"，不是"没人改"——同一工作区还有论文 Coder 在改
        # search.py（HyDE/翻译链），拿工作区干净度当判据会误报（实测踩到）。
        # 正确做法：凡**触碰过 agent/src/enhance/** 的提交，都不得同时触碰这 8 个文件。
        log = subprocess.run(["git", "-C", repo, "log", "--format=%H", "--",
                              "agent/src/enhance"],
                             capture_output=True, text=True, timeout=30)
        commits = [c for c in (log.stdout or "").split() if c]
        chk("找得到本包的提交", bool(commits), f"{len(commits)} 个")
        bad: list[str] = []
        for c in commits:
            sh = subprocess.run(["git", "-C", repo, "show", "--name-only",
                                 "--format=", c],
                                capture_output=True, text=True, timeout=30)
            touched = {l.strip() for l in (sh.stdout or "").splitlines() if l.strip()}
            overlap = touched & set(files)
            if overlap:
                bad.append(f"{c[:8]}:{sorted(overlap)}")
        chk("触碰 enhance 的提交均未触碰 8 个实验依赖文件", not bad, str(bad))
        # 附带信息：工作区里这些文件的改动来自其他 Coder（不判失败，仅提示）
        st = subprocess.run(["git", "-C", repo, "status", "--porcelain", "--"] + files,
                            capture_output=True, text=True, timeout=30)
        dirty = [l.strip() for l in (st.stdout or "").splitlines() if l.strip()]
        if dirty:
            print(f"  （提示）工作区另有他人改动，非本包所为：{dirty}")
    except Exception as e:                                     # noqa: BLE001
        chk("git 可用", False, repr(e))


def main() -> int:
    for fn in (test_default_off_is_inert, test_switches_take_effect, test_split_claims,
               test_number_gate, test_support_check_and_levels, test_recheck_loop,
               test_entity_norm, test_chunk_dedup, test_web_fulltext, test_meta_refresh_follow, test_full_text_enrichment, test_entity_norm_default_off,
               test_no_retrieval_coupling, test_experiment_deps_untouched):
        try:
            fn()
        except Exception as e:                                 # noqa: BLE001
            import traceback
            FAIL.append(fn.__name__)
            print(f"  FAIL  {fn.__name__} 抛异常: {e}")
            traceback.print_exc()
    print(f"\n[enhance pack] {len(PASS)} passed / {len(FAIL)} failed")
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
