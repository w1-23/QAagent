# -*- coding: utf-8 -*-
"""Fig.1 — CitrusQA: system design and evaluation overview (Round 2).

Single source of truth = LAYOUT data + builder below; drives BOTH the SVG
master and the editable PPTX twin (fig1_pptx.py imports this module).

Text-width budget is enforced by fit() (latin .52em, CJK 1.02em) so the
figure survives the §10.4 overflow QA without manual reflow.

Every number/label is verified in:
  wxRev-1014 §2.1/§2.5/§3.2 · RUNTIME_TRUTH.md ·
  _sources/judge_scores_{kimi,glm}.jsonl · _sources/web_arms_summary.json
No mockup-only values are used (see 00_audit/reference_layout_verification.md).
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
OUT = ROOT / "Fig1"
W_MM, H_MM = 174.0, 135.0        # 118 → 135: columns 2/3 (Generation / Tiering cards) run to
                                 # y≈238, i.e. past the old badge strip at 222 — the badge row
                                 # and the (b)/(c) panels are moved down so nothing is overprinted.
                                 # Column geometry, text and card sizes are unchanged.

C = D.C
X0, GAP = 6.0, 8.0
CW1 = 84.0                       # column 1 (user input) wider for CJK
CW = (493.2 - 12 - CW1 - 4 * GAP) / 4   # = 88.8
Y0, CH = 14.0, 202.0
BADGE_Y, BADGE_H = 250.0, 26.0   # 222/20 → 250/26: clears col-2/3 bottoms and fits both sub-lines
ROW2_Y, ROW2_H = 292.0, 86.0     # 246/84 → 292/86 (see H_MM note)
BW = (493.2 - 12 - 8) / 2        # ≈ 236.6

COLS = {"c1": X0,
        "c2": X0 + CW1 + GAP,
        "c3": X0 + CW1 + GAP + CW + GAP,
        "c4": X0 + CW1 + GAP + 2 * (CW + GAP),
        "c5": X0 + CW1 + GAP + 3 * (CW + GAP)}

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
    CHECKS.append({"tag": tag, "max_w": round(mx, 1), "box_w": round(box_w, 1),
                   "fit": bool(ok)})
    assert ok, f"OVERFLOW {tag}: {mx:.1f} > {box_w - 2*pad:.1f}"


# ═══════════════════════ canvas content ════════════════════════════════
def build():
    cv = D.Canvas(W_MM, H_MM, "Fig1")
    LEAD = 8.7   # vertical lead for 6.8pt text

    cv.text(X0, 9.5, "(a) System design and workflow", 9, "bold")

    def header(colx, colw, icon, title, sub, color, bg, tag):
        # Colour rule (PI palette spec): large areas keep the light tint; the PRIMARY
        # colour is reserved for title bars, key borders and necessary connectors.
        cv.rrect(colx, Y0, colw, 26, color, color, 0.7, tag=tag)      # solid title bar
        cv.icon(icon, colx + 11, Y0 + 13, 9.5, "#FFFFFF")
        cv.text(colx + 20, Y0 + 12.2, title, 8.0, "bold", "#FFFFFF")
        cv.text(colx + 20, Y0 + 21.0, sub, 6.8, color=bg)             # light tint sub-line
        fit(tag + ".title", [title], 8.0, colw - 20)
        fit(tag + ".sub", [sub], 6.8, colw - 20)

    def sub_card(colx, colw, y0, icon, title, lines, color, fill, tag):
        h = 16 + len(lines) * LEAD
        cv.rrect(colx + 3, y0, colw - 6, h, fill, color, 0.6, tag=tag)
        if icon:
            cv.icon(icon, colx + 12, y0 + 8.8, 7.5, color)
            tx = colx + 19
        else:
            tx = colx + 10
        cv.text(tx, y0 + 11.2, title, 7.8, "bold", C["text"])
        fit(tag + ".title", [title], 7.8, colw - 6 - (tx - colx))
        yy = y0 + 20.0
        for b in lines:
            cv.text(colx + 12, yy, b, 6.8, color=C["text"])
            yy += LEAD
        fit(tag, lines, 6.8, colw - 12)
        return y0 + h

    def down(xc, y1, y2=7.0):
        cv.arrow(xc, y1 + 2, xc, y1 + y2, C["text2"], 0.8)

    # ── column 1 : user question & language alignment ───────────────────
    x, w = COLS["c1"], CW1
    cv.rrect(x, Y0, w, 26, C["sup"], C["sup"], 0.7, tag="c1.head")
    cv.icon("circle-user-round", x + 11, Y0 + 13, 9.5, "#FFFFFF")
    cv.text(x + 20, Y0 + 12.2, "User question", 7.6, "bold", "#FFFFFF")
    cv.text(x + 20, Y0 + 21.0, "asked in CN", 6.8, color=C["sup_bg"])
    fit("c1.head", ["User question"], 7.6, w - 20)
    fit("c1.head.sub", ["asked in CN"], 6.8, w - 20)

    # question bubble (illustrative, matches deployment)
    qlines = ["柑橘黄龙病有哪些", "有效防治药剂？"]
    cv.rrect(x + 5, Y0 + 32, w - 10, 34, "#FFFFFF", C["border"], 0.6)
    cv.block(x + 9, Y0 + 43.5, qlines, 6.8, color=C["text"], tag="c1.q")
    cv.text(x + w - 8, Y0 + 36.5, "example", 6.0, color=C["text2"], anchor="end")
    fit("c1.question", qlines, 6.8, w - 14, pad=4)

    y = sub_card(x, w, Y0 + 72, None, "Intent confirm",
                 ["detect ambiguity", "ask to clarify", "1–3 candidates"],
                 C["sup"], C["sup_bg"], "c1.intent")
    down(x + w / 2, y)
    sub_card(x, w, y + 10, None, "Query rewrite",
             ["faithful CN → EN", "no rewriting", "intent-anchored"],
             C["meta"], C["meta_bg"], "c1.rewrite")

    # ── column 2 : Local RAG agent ──────────────────────────────────────
    x, w = COLS["c2"], CW
    header(x, w, "book-open-text", "Local RAG", "authoritative", C["rag"], C["rag_bg"], "c2.head")
    y = sub_card(x, w, Y0 + 32, "database", "Corpus layer",
                 ["4,906 documents", "126,494 chunks", "EN · DOI-keyed"],
                 C["rag"], C["rag_bg"], "c2.corpus")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "search", "Retrieval",
                 ["dense e5-large", "+ sparse BM25Plus", "RRF k=60 fusion,", "bounded cutoff"],
                 C["rag"], C["rag_bg"], "c2.retrieval")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "list-ordered", "Rerank",
                 ["bge-reranker", "(top-K = 10)"],
                 C["rag"], C["rag_bg"], "c2.rerank")
    down(x + w / 2, y)
    sub_card(x, w, y + 8, "pen-line", "Generation",
             ["answers with [n]", "no [Wn] · EN/CN", "budget & early stop"],
             C["rag"], C["rag_bg"], "c2.gen")

    # ── column 3 : Web agent ────────────────────────────────────────────
    x, w = COLS["c3"], CW
    header(x, w, "globe", "Web agent", "timely info", C["web"], C["web_bg"], "c3.head")
    y = sub_card(x, w, Y0 + 32, "globe", "Server search",
                 ["snippets + URL", "+ access date"],
                 C["web"], C["web_bg"], "c3.search")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "scale", "Self-judge",
                 ["sufficient → stop", "otherwise retry", "(≤ 2 rounds)"],
                 C["web"], C["web_bg"], "c3.judge")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "brush", "Cleaning",
                 ["remove duplicates", "keep snippet", "+ URL + date"],
                 C["web"], C["web_bg"], "c3.clean")
    down(x + w / 2, y)
    sub_card(x, w, y + 8, "layers", "Tiering",
             ["T1 · T2 · T3", "(annotation,", "not a filter)"],
             C["web"], C["web_bg"], "c3.tier")

    # ── column 4 : Assembly & audit  (per source code: audit.py / expert_graph.py)
    #    Verified: check_citation_ids is one of Stage A's three checks
    #    (citation existence / numeric caliber / recency co-occurrence); there is
    #    NO separate "citation audit" stage and NO re-validation loop back to
    #    Stage A in the code — the documented policy is
    #    "critical -> 1 次修复循环；minor -> 标注 + ledger，不重生成".
    x, w = COLS["c4"], CW
    header(x, w, "shield-check", "Assembly/audit", "code-verified", C["audit"], C["audit_bg"], "c4.head")
    y = sub_card(x, w, Y0 + 32, "code-xml", "Stage A",
                 ["IDs within pool", "caliber (numbers)", "recency co-occur."],
                 C["audit"], C["audit_bg"], "c4.stageA")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "clipboard-check", "Stage B",
                 ["fast-model rubric"],
                 C["audit"], C["audit_bg"], "c4.stageB")
    down(x + w / 2, y)
    y = sub_card(x, w, y + 8, "refresh-cw", "repair (once)",
                 ["critical only", "minor: flag only"],
                 C["audit"], C["audit_bg"], "c4.repair")
    down(x + w / 2, y)
    sub_card(x, w, y + 8, "file-check-2", "Outcome",
             ["pass / partial", "refs all in pool"],
             C["audit"], C["audit_bg"], "c4.outcome")

    # ── column 5 : two-section output ───────────────────────────────────
    x, w = COLS["c5"], CW
    header(x, w, "message-square-text", "Two sections", "user-language", C["meta"], C["meta_bg"], "c5.head")
    cv.rrect(x + 3, Y0 + 34, w - 6, 60, C["rag_bg"], C["rag"], 0.7, tag="c5.s1")
    cv.icon("file-check-2", x + 12, Y0 + 43.5, 7.5, C["rag"])
    cv.text(x + 19, Y0 + 46.5, "Section 1", 8.0, "bold", C["rag"])
    cv.text(x + w - 8, Y0 + 46.5, "(verified)", 6.8, color=C["rag"], anchor="end")
    s1 = ["only from local", "authoritative lit.", "numbered citations", "each DOI-clickable"]
    cv.block(x + 11, Y0 + 59, s1, 6.8, color=C["text"], tag="c5.s1b")
    fit("c5.s1", s1, 6.8, w - 6, pad=8)
    cv.rrect(x + 3, Y0 + 100, w - 6, 60, C["web_bg"], C["web"], 0.7, tag="c5.s2")
    cv.icon("globe-lock", x + 12, Y0 + 109.5, 7.5, C["web"])
    cv.text(x + 19, Y0 + 112.5, "Section 2", 8.0, "bold", C["web"])
    cv.text(x + w - 8, Y0 + 112.5, "(unverified)", 6.8, color=C["web"], anchor="end")
    s2 = ["timely information", "from web; marked", "flagged unverified", "URL + access dates"]
    cv.block(x + 11, Y0 + 125, s2, 6.8, color=C["text"], tag="c5.s2b")
    fit("c5.s2", s2, 6.8, w - 6, pad=8)

    # ── badge strip ─────────────────────────────────────────────────────
    badges = [
        ("shield", "Source isolation", ["citation pools &", "namespaces separate"]),
        ("code-xml", "Code verification", ["every in-text citation", "resolvable"]),
        ("message-square-text", "Chinese-friendly", ["CN question → EN search", "answer in own language"]),
        ("clock", "Timely but bounded", ["web information", "clearly marked"]),
    ]
    bw = (493.2 - 12 - 3 * 8) / 4
    xi = X0
    for icon, title, sub2 in badges:
        cv.rrect(xi, BADGE_Y, bw, BADGE_H, "#FFFFFF", C["border"], 0.7, r=6, tag="badge")
        cv.icon(icon, xi + 12, BADGE_Y + BADGE_H / 2, 8.5, C["sup"])
        cv.text(xi + 21, BADGE_Y + 9, title, 7.6, "bold", C["text"])
        cv.block(xi + 21, BADGE_Y + 16.2, sub2, 6.2, color=C["text2"], tag="badge.sub")
        fit("badge." + title, sub2, 6.2, bw - 23)
        xi += bw + 8

    # ── (b) knowledge-base overview ─────────────────────────────────────
    cv.text(X0, ROW2_Y - 3.5, "(b) Knowledge-base overview", 8.6, "bold")
    px, pw = X0, BW
    cv.rrect(px, ROW2_Y, pw, ROW2_H, "#FFFFFF", C["border"], 0.8, r=6, tag="b.panel")
    cv.icon("book-open-text", px + 12, ROW2_Y + 12, 9, C["rag"])
    cv.text(px + 23, ROW2_Y + 12.5, "Authoritative literature", 8, "bold", C["text"])
    cv.text(px + 23, ROW2_Y + 22, "4,906 documents · 126,494 chunks", 7.6, color=C["text"], tag="b.big")
    chips = [
        ("bug", ["Research", "literature"], "103,089", C["rag"]),
        ("sprout", ["Variety", "records"], "10,018", C["audit"]),
        ("languages", ["Translated", "corpus"], "13,137", C["web"]),
        ("clipboard-list", ["Methods &", "protocols"], "250", C["meta"]),
    ]
    cw_ = (pw - 16 - 3 * 6) / 4
    xi = px + 8
    for icon, label, num, color in chips:
        cv.rrect(xi, ROW2_Y + 28, cw_, 34, "#FFFFFF", color, 0.7, r=5, tag="chip")
        cv.icon(icon, xi + 9, ROW2_Y + 37.5, 7.5, color)
        cv.text(xi + 18, ROW2_Y + 40.5, num, 8.4, "bold", C["text"])
        cv.block(xi + 8, ROW2_Y + 51.0, label, 6.2, color=C["text2"], tag="chip.sub")
        fit("chip." + label[0], label, 6.2, cw_ - 8, pad=3)
        xi += cw_ + 6
    note2 = ["no Chinese-dominant chunks; CN",
             "sources enter as faithful English"]
    cv.block(px + 8, ROW2_Y + 70.0, note2, 6.2, color=C["text2"], tag="b.note")
    fit("b.note", note2, 6.2, pw - 16, pad=4)

    # ── (c) evaluation overview ─────────────────────────────────────────
    qx = px + pw + 8
    qw = 493.2 - 6 - qx
    cv.text(qx, ROW2_Y - 3.5, "(c) Evaluation overview", 8.6, "bold")
    cv.rrect(qx, ROW2_Y, qw, ROW2_H, "#FFFFFF", C["border"], 0.8, r=6, tag="c.panel")
    facts = [
        ("chart-column", ["84-question comparison:",
                          "Tracks A–E × two judges"]),
        ("workflow", ["144-question 3 pools:",
                      "facts · decoys · stale,", "48 questions each"]),
        ("circle-user-round", ["132 real user queries", "four-day window"]),
        ("scale", ["judges kimi-k2.6 and", "glm-5.3 — per-judge,", "with MDD boundaries"]),
        ("clock", ["48-question web test:", "3.1% no-web → 24.0%",
                   "under unfiltered mixing"]),
        ("file-check-2", ["1,649/1,649 citations", "resolvable;", "0 out-of-pool"]),
    ]
    cellw = (qw - 16) / 2
    for i, (icon, lines) in enumerate(facts):
        r, cc = divmod(i, 2)
        cx = qx + 8 + cc * cellw
        cy = ROW2_Y + 10 + r * 24.5
        cv.icon(icon, cx + 5, cy + 3.4, 7.5, C["sup"])
        cv.block(cx + 16, cy + 6.0, lines, 6.8, color=C["text"], tag="fact." + str(i))
        ll = lines + [icon]
        fit("fact." + str(i), lines, 6.8, cellw - 16, pad=2)
    return cv


def caption_draft():
    return """# Fig.1 caption draft (HA style; final wording for review)

**(EN)** **Fig.1** CitrusQA system design and evaluation overview.

**(a)** Researchers ask questions in Chinese. An intake step detects ambiguity and, when needed,
confirms the intended interpretation among 1–3 candidate readings; the confirmed intent is then
translated faithfully into English search queries (no rewriting). The local RAG agent retrieves
evidence from a frozen English-only corpus (4,906 documents; 126,494 chunks; DOI-keyed index) with a
dense (multilingual e5-large, LanceDB) and a sparse (BM25Plus) path fused by reciprocal rank fusion
(k = 60) under a lower-bounded cutoff, re-ranks the top candidates with a cross-encoder
(bge-reranker, top-K = 10) and generates answers that carry inline [n] citations only
(web markers [Wn] never enter Section 1; budget and early-stop caps bound the loop). In parallel, a
web agent performs server-side web search (snippets with URL and access date), self-judges whether
evidence is sufficient (otherwise retries, ≤ 2 rounds), cleans and de-duplicates results, and labels
every source T1/T2/T3 (labels are annotations, not a filter). Outputs of both chains enter an
assembly-and-audit stage: Stage A is a deterministic code gate (citation renumbering, removal of
out-of-pool references), followed by Stage B, a fast-model rubric review; a citation audit checks
citation-pool membership, and answers flagged critical receive at most one minimal repair before
re-validation. The final answer is emitted in two sections in the user's language: Section 1 (local,
verified) built only on the authoritative literature with inline numbered, DOI-clickable citations,
and Section 2 (web, unverified) that carries timely information explicitly marked "unverified by
local literature" with source URLs and access dates. Bottom badges summarise the four guarantees:
source isolation, code-based verification, Chinese-friendly interaction, and bounded timeliness.

**(b)** Knowledge-base overview: the frozen snapshot contains 4,906 documents and 126,494 chunks —
research literature 103,089, variety records 10,018, translated corpus 13,137, and experimental
methods & protocols 250. No Chinese-dominant chunks exist: Chinese sources are ingested as faithful
English translations and then follow the identical cleaning, chunking and indexing pipeline.

**(c)** Evaluation overview: 84-question system-layer comparison across Tracks A–E under two
independent judges; a 144-question three-pool stress test (in-corpus facts, adversarial decoys,
stale-timeliness, 48 each); 132 real user queries from a four-day window; two judges (kimi-k2.6,
glm-5.3) report per-judge with minimum-detectable-difference boundaries; a 48-question web-input
experiment spans five treatments, where the model-side hallucination rate rises from 3.1% (no web)
to 24.0% (unfiltered mixing); 1,649/1,649 in-text citations resolve and none point outside the
citation pool.

QA note (Round 2): the "Citation audit" card and the "Re-validate → back to Stage A" arrow follow the
approved layout reference; their exact trigger semantics remain flagged for confirmation
(00_audit/module_status.csv). Panel labels are lowercase per HA; no figure title is embedded.
"""


def manifest():
    return {
        "figure_id": "Fig1",
        "figure_title": "CitrusQA system design and evaluation overview",
        "data_sources": [
            "paper/系统篇_v7/wxRev-1014_王自强修订前原样备份.docx (§2.1, §2.5, §3.2)",
            "07_交付与文档_delivery_docs/06_文档_docs/RUNTIME_TRUTH.md (RRF k=60; ≤2 web rounds; [Wn] namespace)",
            "07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources/"
            "judge_scores_{kimi,glm}.jsonl (84 questions; tracks↔configs mapped)",
            "_sources/web_arms_summary.json (five treatments; pairs n=48)",
            "_sources/data_sources.CITATION_LEVELS (1,649 markers / 0 outside)",
        ],
        "source_code_refs": [
            "01_源码_src/src/core/{intake,two_section,search_both,agent_loop,evidence,audit,source_tiers}.py",
        ],
        "experiment_conditions": "Tracks A–E; three pools ×48; 132 real; five web treatments",
        "number_of_samples": "84 | 144 (48×3) | 132 | 48-question web subset",
        "plot_types": "structured diagram (vector)",
        "statistical_methods": "none (structural overview)",
        "output_dimensions": "174.0 × 118.0 mm",
        "output_formats": ["svg(editable)", "pptx(editable)", "pdf", "png"],
        "verification_status": "Round-2 draft; two layout-parity flags pending PI ruling "
                                "(Citation audit as separate card; Re-validate arrow)",
    }


if __name__ == "__main__":
    cv = build()
    qa = cv.save(OUT / "fig1_system.svg")
    (OUT / "caption_draft_fig1.md").write_text(caption_draft(), encoding="utf-8")
    (OUT / "figure_manifest_fig1.json").write_text(
        json.dumps({**manifest(), "qa_evidence": {**qa, "fit_checks": CHECKS}},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    n_overflow = [c for c in CHECKS if not c["fit"]]
    n_small = [t for t in cv.texts if t["size"] < 6.8]
    print("fig1_system.svg written.")
    print(f"  texts={len(cv.texts)} connectors={len(cv.connectors)} icons={len(cv.icon_files)}")
    print(f"  fit-checks={len(CHECKS)} overflow={len(n_overflow)} sub-6.8pt={len(n_small)}")
    print("  colors:", sorted(cv.colors))
