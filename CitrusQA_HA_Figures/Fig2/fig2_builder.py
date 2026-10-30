# -*- coding: utf-8 -*-
"""Fig.2 — Corpus construction and quality control (Round 2).

(a) four-stage pipeline (matches wxRev §2.2 exactly: 来源准入/清洗/元数据质检/切块与索引)
(b) composition bands: datasets → source collections → resource classes.

All counts are the paper-frozen values (PI ruling 2026-10-14).
True chunking parameter = target 800 (PI ruling); observed median 941 chars
lives in the manuscript, kept OUT of the figure. Deprecated 187,441口径 absent.
"""
import sys, io, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "01_design_system"))
import design as D

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
OUT = ROOT / "Fig2"
W_MM, H_MM = 174.0, 112.0
C = D.C

CHECKS = []


def fit(tag, lines, size, box_w, pad=5.0):
    def wl(s):
        w = 0.0
        for ch in s:
            if '\u4e00' <= ch <= '\u9fff':
                w += 1.02
            elif ch.isdigit():
                w += 0.55
            else:
                w += 0.52
        return w * size
    mx = max(wl(s) for s in lines)
    ok = mx <= box_w - 2 * pad
    CHECKS.append({"tag": tag, "max_w": round(mx, 1), "box_w": round(box_w, 1), "fit": bool(ok)})
    assert ok, f"OVERFLOW {tag}: {mx:.1f} > {box_w-2*pad:.1f}"


# ── composition data (paper §3.2 / v7 fig2(b) source-collection split) ──
DATASETS = [
    ("Main corpus", 112_890, C["sup"]),
    ("Translated literature", 13_137, C["web"]),
    ("Special resources", 467, C["audit"]),
]
SOURCES = [
    ("Parsed literature", 62_803, C["sup"]),
    ("Supplementary literature", 33_790, C["rag"]),
    ("UCR germplasm resources", 9_801, C["audit"]),
    ("Additional literature", 6_496, C["meta"]),
    ("CN→EN translated corpus", 13_137, C["web"]),
    ("Experimental methods", 250, C["audit"]),
    ("Citrus monograph", 217, C["meta"]),
]
CLASSES = [
    ("Research literature", 103_089, C["sup"]),
    ("Variety records", 10_018, C["audit"]),
    ("Translated corpus", 13_137, C["web"]),
    ("Methods & protocols", 250, C["meta"]),
]
# sources → datasets
SRC2DS = {0: 0, 1: 0, 2: 0, 3: 0, 4: 1, 5: 2, 6: 2}
# sources → resource classes
SRC2CL = {0: 0, 1: 0, 2: 1, 3: 0, 4: 2, 5: 3, 6: 1}

TOTAL = 126_494
SCALE = 170.0 / TOTAL          # pt per chunk
MIN_H = 3.0                    # readability clamp (<0.2% bands)
SW, GH = 30.0, 6.0             # node width, per-item gap
NODE_X = {"ds": 104.0, "src": 252.0, "cl": 358.0}
LBL_L_W, LBL_R_X = 92.0, NODE_X["cl"] + SW + 6


def build():
    cv = D.Canvas(W_MM, H_MM, "Fig2")
    LEAD = 8.7

    # ── (a) pipeline ────────────────────────────────────────────────────
    cv.text(6, 9.5, "(a) Corpus construction pipeline", 9, "bold")
    STAGES = [
        ("workflow", "1 · Source admission", C["sup"], C["sup_bg"],
         ["whitelisted journals", "variety & rootstock", "records (UCR entry list,",
          "citrus monograph)", "authorized private data"]),
        ("eraser", "2 · Extraction", C["rag"], C["rag_bg"],
         ["PDF extraction (text)", "encoding normalization", "layout / noise removal",
          "content + normalized-text", "dedup (SHA-256)"]),
        ("clipboard-list", "3 · Metadata QA", C["meta"], C["meta_bg"],
         ["title / year / DOI rules", "format & range checks", "manual exception review"]),
        ("database", "4 · Chunks & index", C["audit"], C["audit_bg"],
         ["heading-based segmentation", "recursive chunks (target", "≈ 800 chars)",
          "dense e5-large · LanceDB",
          "sparse BM25Plus", "frozen snapshot+manifest"]),
    ]
    sw_ = (493.2 - 12 - 3 * 8) / 4
    y0 = 16.0
    xs = []
    xi = 6.0
    for icon, title, color, bg, lines in STAGES:
        fit("(a)." + title + ".t", [title], 7.8, sw_ - 18)
        fit("(a)." + title, lines, 6.8, sw_ - 12)
        h = 16 + len(lines) * LEAD
        cv.rrect(xi, y0, sw_, h, bg, color, 0.7, tag=("(a)" + title))
        cv.icon(icon, xi + 10, y0 + 8.6, 7.8, color)
        cv.text(xi + 18, y0 + 11.4, title, 7.8, "bold", C["text"])
        cv.block(xi + 8, y0 + 22.0, lines, 6.8, color=C["text"], tag="a." + title)
        xs.append(xi)
        xi += sw_ + 8
    # arrows between stages
    for i in range(3):
        cv.arrow(xs[i] + sw_ + 1.2, y0 + 26, xs[i] + sw_ + 6.8, y0 + 26, C["text2"], 0.8)
    # excluded/flagged branch under stage 3
    fx = xs[2] + 8
    fy = y0 + 16 + 4 * LEAD + 10
    cv.arrow(xs[2] + sw_ / 2, fy - 8, xs[2] + sw_ / 2, fy - 2, C["web"], 0.7)
    cv.rrect(fx, fy, sw_ - 4, 15, C["web_bg"], C["web"], 0.7, r=4, tag="excl")
    cv.text(fx + 6, fy + 6.2, "excluded / flagged:", 6.8, "bold", C["web"])
    cv.text(fx + 6, fy + 14.0, "unparseable · too short", 6.8, color=C["text"])
    fit("excl", ["excluded / flagged:", "unparseable · too short"], 6.8, sw_ - 12)

    # ── (b) composition bands ───────────────────────────────────────────
    yb = 96.0
    cv.text(6, yb - 3.5, "(b) Dataset, source collection and resource class (chunks)", 9, "bold")
    top = yb + 6.0

    def stack(items, col):
        """compute node y-positions with min-height clamp; returns (ys,hs,total)"""
        ys, hs, y = [], [], top
        for _, v, _ in items:
            h = v * SCALE
            if h < MIN_H:
                h = MIN_H
            ys.append(y)
            hs.append(h)
            y += h + GH
        return ys, hs, y

    ds_y, ds_h, _ = stack(DATASETS, "ds")
    src_y, src_h, _ = stack(SOURCES, "src")
    cl_y, cl_h, _ = stack(CLASSES, "cl")

    # band poly helper (trapezoid, slight ease)
    def band(x1, t1, b1, x2, t2, b2, color):
        fill = color
        op = 0.30
        xm = (x1 + x2) / 2
        d = f"M {x1:.1f} {t1:.1f} C {xm:.1f} {t1:.1f}, {xm:.1f} {t2:.1f}, {x2:.1f} {t2:.1f} " \
            f"L {x2:.1f} {b2:.1f} C {xm:.1f} {b2:.1f}, {xm:.1f} {b1:.1f}, {x1:.1f} {b1:.1f} Z"
        cv.colors.add(fill)
        cv.el.append(f'<path d="{d}" fill="{fill}" fill-opacity="{op}" stroke="none"/>')

    # dataset → source bands
    ds_targets = {0: [0, 1, 2, 3], 1: [4], 2: [5, 6]}
    for di, targets in ds_targets.items():
        span = [ds_y[di] - 2, ds_y[di] + ds_h[di] + 2]
        yi = span[0]
        for si in targets:
            h = src_h[si] / len(targets)
            band(NODE_X["ds"] + SW, yi, yi + h,
                 NODE_X["src"] - 2, src_y[si], src_y[si] + src_h[si], SOURCES[si][2])
            yi += h
    # source → class bands
    for si, ci in SRC2CL.items():
        band(NODE_X["src"] + SW, src_y[si], src_y[si] + src_h[si],
             NODE_X["cl"] - 2, cl_y[ci], cl_y[ci] + cl_h[ci], CLASSES[ci][2])

    # nodes + labels
    def wrap(s, budget):
        words = s.split()
        lines, cur = [], ""
        for wd in words:
            t = (cur + " " + wd).strip()
            if len(t) <= budget:
                cur = t
            else:
                if cur:
                    lines.append(cur)
                cur = wd
        lines.append(cur)
        return lines

    def col_labels(items, ys, hs, xnode, side, title, title_x):
        cv.text(title_x, top + 8.6, title, 7.8, "bold", C["text"])
        for (name, v, color), y, h in zip(items, ys, hs):
            cv.rrect(xnode, y, SW, h, color, None, 0, r=2, tag="node")
            if side == "left":
                budget = 20
                lines = wrap(name, budget)
                base = max(y + h / 2 - (len(lines) + 1) * 4.2, top + 4)
                yy = base
                for ln in lines:
                    cv.text(8, yy, ln, 6.8, color=C["text"], tag="lbl")
                    yy += 8.7
                cv.text(8, yy + 0.4, f"{v:,}", 7.2, "bold", color=C["text"], tag="lbl")
                labs = lines + [f"{v:,}"]
            else:
                lines = wrap(name, 24)
                if len(name) > 16:
                    lines = lines + [f"{v:,}"]      # long names: value on own line
                else:
                    lines[-1] = f"{lines[-1]}  {v:,}"
                base = y + h / 2 - (len(lines) - 1) * 4.4 + 2.6
                yy = base
                for ln in lines:
                    cv.text(LBL_R_X, yy, ln, 6.8, color=C["text"], tag="lbl")
                    yy += 8.8
                labs = lines
            fit("node." + name, labs, 6.8,
                (489.2 - LBL_R_X if side == "right" else LBL_L_W - 10))

    col_labels(DATASETS, ds_y, ds_h, NODE_X["ds"], "left", "Dataset", 6)
    col_labels(SOURCES, src_y, src_h, NODE_X["src"], "right", "Source collection", NODE_X["src"] + SW + 2)
    col_labels(CLASSES, cl_y, cl_h, NODE_X["cl"], "right", "Resource class", LBL_R_X)

    # footnote
    fn = ["band width ∝ chunk count; every column reconciles to 126,494 chunks (4,906 documents).",
          "bands below 0.2% are drawn at minimum width for readability."]
    fit("(b).footnote", fn, 6.2, 493.2 - 12, pad=4)
    cv.block(8, 96.0 + 6 + 196 + 6, fn, 6.2, color=C["text2"], tag="b.fn")
    return cv


def caption_draft():
    return """# Fig.2 caption draft (HA style; final wording for review)

**(EN)** **Fig.2** Construction and quality control of the CitrusQA knowledge base.

**(a)** Corpus construction proceeds in four stages. (1) Source admission accepts whitelisted
journal sources (the authoritative-literature backbone spanning pathology & disease resistance,
physiology & metabolism, genetics & breeding, cultivation & management and post-harvest processing),
variety & rootstock records (including UCR germplasm entries and a curated citrus monograph) and
authorized private materials (experimental methods and reports). (2) Cleaning normalises encodings,
removes layout noise (references, page furniture, standalone URLs, production notices) and
de-duplicates documents at two levels — a content hash of the raw file and a SHA-256 hash of the
normalised text, keeping one representative copy of identical normalised documents. (3) Metadata QA
normalises title, year and DOI (title 100%, year 90.7%); files that fail parsing or are too short
are excluded or flagged (branch shown). (4) Chunking and indexing split documents by Markdown
headings and then re-split recursively (target ≈ 800 characters; observed median 941 characters);
each chunk inherits its document DOI as the provenance key used at evidence assembly. Devoted dense
vectors (multilingual e5-large, 1,024-d, LanceDB IVF_HNSW) and a sparse BM25Plus inverted index are
built offline, and the frozen snapshot is version-controlled by a hash list and document manifest.

**(b)** Composition of the frozen corpus, drawn as flow bands whose widths are proportional to
chunk counts (minimum-width clamp at 0.2% for readability). Datasets: main corpus 112,890,
translated literature 13,137, special resources 467 — totalling 126,494 chunks from 4,906
documents. Source collections: parsed literature 62,803, supplementary literature 33,790, UCR
germplasm resources 9,801, additional literature 6,496 (main corpus); CN→EN translated corpus
13,137; experimental methods 250 and citrus monograph 217 (special resources). Resource classes:
research literature 103,089 (parsed + supplementary + additional), variety records 10,018 (UCR
germplasm + monograph), translated corpus 13,137, methods & protocols 250. Every column reconciles
to 126,494 chunks; Chinese sources enter only as faithful English translations, so the corpus
contains no Chinese-dominant chunks, and Chinese questions reach the corpus only through the
faithful CN→EN query rewriting of Fig.1.
"""


def manifest():
    return {
        "figure_id": "Fig2",
        "figure_title": "Construction and quality control of the citrus knowledge base",
        "data_sources": [
            "paper wxRev-1014 §2.2 (four stages; chunking; e5-large/LanceDB/BM25Plus; DOI inheritance)",
            "paper wxRev-1014 §3.2 (4,906 documents / 126,494 chunks; classes 103,089/10,018/13,137/250)",
            "paper/系统篇_v7/CitrusQA初稿 (2).docx §3.2 v7 (source collections 62,803/33,790/9,801/6,496/13,137/250/217 — reconciled)",
            "PI rulings 2026-10-14: chunking target 800; corpus 187,441 (11 batches) DEPRECATED, never shown",
        ],
        "source_code_refs": [
            "04_脚本_scripts/ingest.py (ingest pipeline)",
            "data_archive/01_corpus/pipeline.md (DOI normalisation, cold-start index)",
            "07_交付与文档_delivery_docs/04_数据_data/冻结_freezed/freeze_manifest.json (snapshot versioning concept)",
        ],
        "experiment_conditions": "n/a (corpus construction)",
        "number_of_samples": "126,494 chunks / 4,906 documents",
        "plot_types": "(a) structured pipeline (vector); (b) flow bands (widths ∝ counts)",
        "statistical_methods": "none (descriptive counts)",
        "output_dimensions": "174.0 × 112.0 mm",
        "output_formats": ["svg(editable)", "pptx(editable)", "pdf", "png"],
        "verification_status": "Round-2 draft; all counts match the reconciled paper values; "
                                "band <0.2% drawn clamped (noted in caption)",
    }


if __name__ == "__main__":
    cv = build()
    qa = cv.save(OUT / "fig2_corpus.svg")
    (OUT / "caption_draft_fig2.md").write_text(caption_draft(), encoding="utf-8")
    (OUT / "figure_manifest_fig2.json").write_text(
        json.dumps({**manifest(), "qa_evidence": {**qa, "fit_checks": CHECKS}},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    n_overflow = [c for c in CHECKS if not c["fit"]]
    print("fig2_corpus.svg written.")
    print(f"  texts={len(cv.texts)} nodes={sum(1 for e in cv.el if '<rect' in e and 'node' in e)}")
    print(f"  fit-checks={len(CHECKS)} overflow={len(n_overflow)}")
    print("  colors:", sorted(cv.colors))
    print("  numbers:", sorted(cv.numbers()))
