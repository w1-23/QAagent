# -*- coding: utf-8 -*-
"""Round-1 evidence grep for module_status + duplicate-file hash compare."""
import sys, io, re, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
SRC = ROOT / "01_源码_src"
SOURCES = ROOT / "07_交付与文档_delivery_docs" / "03_图件_figures" / "英文_figures_en" / "_sources"
PLOTC = ROOT / "experiment" / "2026927-python绘图latest" / "05_系统层对比五配置_system_layer" / "02_结果数据_results"

def sep(t): print("\n" + "=" * 96 + "\n" + t)

def find(pattern, roots=(SRC,), exts=(".py",), cap=4):
    rx = re.compile(pattern, re.I | re.S)
    out = []
    for root in roots:
        for p in root.rglob("*"):
            if p.suffix not in exts or "__pycache__" in str(p) or p.stat().st_size > 3_000_000:
                continue
            try:
                t = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            m = rx.search(t)
            if m:
                s = t[max(0, m.start() - 80):m.end() + 120].replace("\n", " ")
                out.append((str(p.relative_to(ROOT)), s.strip()[:200]))
                if len(out) >= cap:
                    return out
    return out

checks = {
    "faithful_translation (忠实翻译/不改写)": r"(忠实翻译|faithful translat|不改写|no rewrit)",
    "query_rewrite_module": r"(rewrite_query|rewrite\.py|query_rewrite|英文改写|改写为英文)",
    "source_tiering T1/T2/T3": r"(T1.{0,30}T2.{0,30}T3|source_tier|来源分级|tier_?ing)",
    "multi_source_search": r"(multi.{0,3}source|多源|原生联网|WEB_SEARCH_API_STYLE|web_search)",
    "stage_a_code_gate": r"(Stage A|stage_a|代码硬门|code.{0,4}gate|硬校验)",
    "stage_b_model_review": r"(Stage B|stage_b|量表回审|model review|rubric)",
    "audit_gate_severity": r"(severity.{0,20}critical|critical.{0,20}severity)",
    "minimal_repair_once": r"(最小返修|至多一次|repair.{0,12}(once|max)|max_repair)",
    "revalidate_loop": r"(re-?validat|回环|回到 Stage|loop back)",
    "two_section_separator": r"(联网小节|web_output_separator|两段|Section 2|section_?2)",
    "out_of_pool_drop": r"(out[-_ ]?of[-_ ]?pool|越界|dropped.{0,20}ref|池外)",
    "web_namespace_Wn": r"(\[Wn\]|WEB_CITE_SLOT|W\{i|W1\.\.Wm)",
    "intent_ambiguity_threshold": r"(AMBIGUITY_THRESHOLD|歧义分|needs_clarification)",
    "citation_existence_gate": r"(cite_exist|引用存在性|citeCover|CiteExist)",
}
for k, pat in checks.items():
    hits = find(pat)
    print(f"\n[{k}]")
    if not hits:
        print("   NOT FOUND in 01_源码_src")
    for rel, snip in hits:
        print(f"   {rel}: {snip[:180]}")

# duplicate stat files: hash compare
sep("hash compare: SOURCES vs plot-ws copies")
for name in ("five_metrics.csv", "per_dimension_means.csv", "mdd.json", "agreement.json",
             "web_arms_summary.json", "webpipe_thresholds.json", "exp1_summary.json"):
    a = SOURCES / name
    b = PLOTC / name if name not in ("web_arms_summary.json", "webpipe_thresholds.json") else None
    if b is None:
        b2 = ROOT / "experiment" / "2026927-python绘图latest" / "09_联网五组对照_web_arms" / "02_结果数据_results" / name
        b = b2 if b2.exists() else None
    if a.exists() and b and b.exists():
        ha = hashlib.sha256(a.read_bytes()).hexdigest()[:16]
        hb = hashlib.sha256(b.read_bytes()).hexdigest()[:16]
        print(f"   {name}: SOURCES={ha} vs plot-ws={hb} {'SAME' if ha == hb else 'DIFF'}")
    else:
        print(f"   {name}: SOURCES={a.exists()} plot-ws={bool(b and b.exists())}")
