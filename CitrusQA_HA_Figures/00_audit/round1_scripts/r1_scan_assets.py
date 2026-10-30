# -*- coding: utf-8 -*-
"""Round-1 asset scan (READ-ONLY on sources).

Scans authority zones, classifies into the 11 asset categories from the task
book, emits asset_inventory.csv + source file hashes (integrity baseline so we
can prove afterwards that no source file changed).

Categories (11):
 1 wxrev_paper      WxRev 论文
 2 draft_paper      CitrusQA 初稿
 3 plotting_code    历史绘图代码
 4 raw_data         原始 CSV/JSON/JSONL/XLSX
 5 exp_config       实验配置
 6 eval_records     评测记录（逐题评分/判官）
 7 retrieval_logs   检索命中日志
 8 citation_logs    引用核验日志
 9 freeze_manifest  知识库冻结清单
 10 parse_clean_logs 文献解析与清洗日志
 11 screenshots     用户问答案例截图
 12 runtime_truth   运行时真相 / 权威文档
Everything else found but not matching: listed as `other` (kept for trace).
"""
import sys, io, os, csv, hashlib, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
from pathlib import Path
from datetime import datetime

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
OUT = ROOT / "CitrusQA_HA_Figures" / "00_audit"
OUT.mkdir(parents=True, exist_ok=True)

ZONES = {
    "paper": ROOT / "paper",
    "plot_ws": ROOT / "experiment" / "2026927-python绘图latest",
    "data_archive": ROOT / "data_archive",
    "delivery_data": ROOT / "07_交付与文档_delivery_docs" / "04_数据_data",
    "delivery_docs": ROOT / "07_交付与文档_delivery_docs" / "06_文档_docs",
    "src": ROOT / "01_源码_src",
    "config": ROOT / "02_配置_config",
    "index_html": ROOT / "06_网页_index",
}

CAT_RULES = [
    (re.compile(r"wxRev|R\d+王自强|修订版", re.I), "wxrev_paper"),
    (re.compile(r"初稿", re.I), "draft_paper"),
    (re.compile(r"(fig|figure|绘图|plot|deck|table[s_]?)", re.I), None),  # defer
    (re.compile(r"freeze_manifest|冻结", re.I), "freeze_manifest"),
    (re.compile(r"(RUNTIME_TRUTH|口径|FILE_GUIDELINES|FIGURE_GUIDELINES|GLOSSARY|EXPERIMENTS|RAW_DATA)", re.I), "runtime_truth"),
    (re.compile(r"(citation|cite[_(]|audit|核验|resolvab)", re.I), None),
]

def classify(p: Path, zone: str):
    name = p.name
    s = str(p).lower()
    ext = p.suffix.lower()
    # papers
    if zone == "paper" and ext == ".docx":
        if "wxRev" in name or "修订" in name:
            return "wxrev_paper", 1
        if "初稿" in name or "deepseek" in name.lower() or "glm" in name.lower():
            return "draft_paper", 2
        return "draft_paper", 0
    # freeze manifest
    if "freeze_manifest" in name or ("冻结" in str(p) and ext in (".json", ".md")):
        return "freeze_manifest", 0
    # runtime truth & authority docs
    if name in ("RUNTIME_TRUTH.md", "EXPERIMENTS.md", "RAW_DATA.md", "DATA_INVENTORY.md",
                "GLOSSARY.md", "CONFIG_REFERENCE.md", "IMPLEMENTATION_DETAILS.md",
                "FIGURE_GUIDELINES.md", "REPRODUCE.md", "KNOW_ISSUES.md", "KNOWN_ISSUES.md",
                "CODE_DETAILS.md", "AGENT_FRAMEWORK.md", "CHANGELOG.md", "AGENTS.md",
                "AGENT_CHANGES.md", "口径权威链_AUTHORITY_CHAIN.md", "README.md",
                "REPAINT_SUMMARY.md", "INDEX.md", "全局索引.md", "REPORTS_INDEX.md"):
        return "runtime_truth", 0
    # plotting code
    if zone in ("plot_ws", "paper") and ext == ".py" and re.search(r"(fig|deck|table|plot|chart|corpus|flow)", name, re.I):
        return "plotting_code", 0
    # screenshots
    if ext in (".png", ".jpg", ".jpeg") and zone == "paper":
        return "screenshots", 0
    if "media" in s and ext in (".png", ".jpg", ".svg"):
        return "screenshots", 0
    # config
    if ext in (".yaml", ".yml", ".toml", ".ini") or "config" in name.lower():
        return "exp_config", 0
    # jsonl eval records (per-question)
    if ext == ".jsonl" and re.search(r"(answer|score|judge|eval|per_|detail)", name, re.I):
        return "eval_records", 0
    if re.search(r"(86question|84|pool|real\d|agreement|mdd|five_metric|per_dim|web_arms|webpipe)", name, re.I) and ext in (".json", ".csv"):
        return "eval_records", 0
    # retrieval logs
    if "retrieval" in s:
        return "retrieval_logs", 0
    # citation logs
    if re.search(r"(citation|cite|audit|核验|resolvab|cited)", name, re.I):
        return "citation_logs", 0
    # parse/clean logs
    if re.search(r"(clean|ingest|parse|mineru|block|chunk|corpus|构建|切块|清洗)", name, re.I):
        return "parse_clean_logs", 0
    # raw data
    if ext in (".csv", ".xlsx", ".xls"):
        return "raw_data", 0
    if ext == ".json":
        return "raw_data", 0
    if ext == ".jsonl":
        return "raw_data", 0
    # code / other
    if ext == ".py":
        return "plotting_code" if zone == "plot_ws" else "exp_code", 0
    if ext in (".pptx", ".md", ".pdf"):
        return "report_doc", 0
    return "other", -1


def sha256(path: Path, block=1 << 20):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(block), b""):
            h.update(chunk)
    return h.hexdigest()

SKIP_DIRS = ("__pycache__", "node_modules", ".git", ".pytest_cache", "render_", ".tmp")

rows = []
hashes = []
count_limit_per_zone = 40000
for zone, zroot in ZONES.items():
    if not zroot.exists():
        print(f"!! zone missing: {zone} {zroot}")
        continue
    n = 0
    for dirpath, dirnames, filenames in os.walk(zroot):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.stat()
            except OSError:
                continue
            if st.st_size > 60_000_000:  # skip >60MB blobs but record name
                cat, conf = "other", 0
                size = st.st_size
            else:
                cat, conf = classify(p, zone)
                size = st.st_size
            rel = str(p.relative_to(ROOT))
            rows.append({
                "zone": zone, "rel_path": str(p.relative_to(zroot)),
                "full_path": str(p), "category": cat, "size_bytes": size,
                "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "pic_redun_role": conf,
            })
            n += 1
            if n > count_limit_per_zone:
                break
    print(f"zone {zone}: {n} files")

# hash evidence file: only critical authority sources (papers, key data, code) to keep runtime sane
for r in rows:
    p = Path(r["full_path"])
    CRIT_pats = re.compile(r"(wxRev|RUNTIME_TRUTH|five_metrics|mdd\.json|agreement\.json|per_dimension|web_arms_summary|webpipe_thresholds|pools144_answers_final|real132_answers_final|freeze_manifest|flow_layout\.py|口径权威链|REPAINT_SUMMARY)", re.I)
    if CRIT_pats.search(p.name):
        try:
            h = sha256(p)
        except OSError:
            h = "READ_FAIL"
        hashes.append({"rel_path": str(p.relative_to(ROOT)), "sha256": h})

# write outputs
inv = OUT / "asset_inventory.csv"
with inv.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else ["zone"])
    w.writeheader()
    w.writerows(rows)

hs = OUT / "source_hashes_baseline.csv"
with hs.open("w", newline="", encoding="utf-8-sig") as f:
    w = csv.DictWriter(f, fieldnames=["rel_path", "sha256"])
    w.writeheader()
    w.writerows(hashes)

from collections import Counter
cat_count = Counter(r["category"] for r in rows)
print("total files:", len(rows))
print("categories:", dict(cat_count))
print("hashed files:", len(hashes))
print("asset_inventory.csv / source_hashes_baseline.csv written to", OUT)
