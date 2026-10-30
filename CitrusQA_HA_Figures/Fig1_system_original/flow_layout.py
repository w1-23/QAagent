# -*- coding: utf-8 -*-
"""Shared layout data for the two mechanism master figures (task book v13).

One coordinate table drives BOTH renderers:
  * matplotlib vector version (SVG + PDF + 600 dpi TIFF + PNG)
  * editable PPT twin (same arrangement, every object draggable)

Figure 1 is a RECTANGULAR layout authored in centimetres (total width 17.0 cm)
and converted to axes fractions by _x()/_y()/_w()/_h().

v13 changes (this task book):
  * audit gate re-ordered to a clean top-down loop:
        [Stage A] -> [Stage B] -> [critical?]
        critical? YES -> down -> [repair] -> [re-validate] -> loop back to A
        critical? NO  -> down (right channel) -> out of the gate
        both outputs leave through the gate bottom and flow down to [4]
    the old right-hand "no" detour that crossed the main chain is gone;
  * every "exec-owner" annotation deleted - ownership is now shown by colour
    (S1 Supervisor / S3 Local RAG / S7 Web Agent / S6 Assembly) and stated once
    in an external PPT text box;
  * "(heavy path)" / "(light path)" removed from the two agent titles;
  * all block gaps tightened to 0.5 cm (audit gate internals 0.3 cm).

Colours follow S1-S8. No dashed lines anywhere.
"""

C_MAIN = "#7b95c6"
C_RET = "#49c2d9"
C_GEN = "#67a583"
C_AUD = "#f47254"
RED = "#c85e62"
GRAY = "#F0F0F0"
GRAY_EDGE = "#BFBFBF"
GRAY_T = "#666666"

# ── palette (S1-S8) ──────────────────────────────────────────────────────
S1 = "#49c2d9"
S2 = "#a1d8e8"
S3 = "#a2c986"
S4 = "#d0e2c0"
S5 = "#fded95"
S6 = "#ffc1a6"
S7 = "#f59c7c"
S8 = "#f47254"

ARROW = "#333333"
LW_ARROW = 0.5
LW_RED = 0.8

# ── figure 1 canvas width (height is derived from the stack, see below) ──
W_CM = 17.0

# ── spacing (task book v13 s5) ───────────────────────────────────────────
BLOCK_GAP = 0.50     # between stacked main blocks
COL_GAP = 0.50       # between side-by-side blocks in one layer
AUDIT_GAP = 0.30     # between audit-gate internals
PAD_T = 0.30         # title offset below box top
PAD_TB = 0.20        # title -> body gap
PAD_B = 0.30         # bottom padding

LH10 = 10 * 1.2 / 72 * 2.54     # 0.4233 cm
LH8 = 8 * 1.2 / 72 * 2.54       # 0.3387 cm


def box_h(n_body_lines, title_pt=10):
    """Exact content height so vertical centring lands the title 0.30 cm below
    the box top, the body 0.20 cm below the title and 0.30 cm at the bottom."""
    lh_t = title_pt * 1.2 / 72 * 2.54
    return PAD_T + lh_t + PAD_TB + n_body_lines * LH8 + PAD_B


# ── layer geometry (cm, measured from the TOP edge) ──────────────────────
H0 = box_h(1)                       # 1.562  [0] User input
H1 = box_h(2)                       # 1.901  [1] Supervisor orchestrator
H4 = box_h(1)                       # 1.562  [4] Supervisor final
H5 = box_h(2)                       # 1.901  [5a] / [5b]

TOP0 = 0.00
TOP1 = TOP0 + H0 + BLOCK_GAP
TOP2 = TOP1 + H1 + BLOCK_GAP
H2 = 11.51                          # layer 2 (driven by the audit gate)
TOP4 = TOP2 + H2 + BLOCK_GAP
TOP5 = TOP4 + H4 + BLOCK_GAP
H_CM = TOP5 + H5                    # 20.44 cm

W_IN = W_CM / 2.54
H_IN = H_CM / 2.54

# ── horizontal split (totals 17.0 cm, gaps 0.5 cm) ───────────────────────
A2_X, A2_W = 0.00, 5.00             # [2a] Local RAG Agent
A3_X, A3_W = 5.50, 6.00             # [3] Assembly & audit
B2_X, B2_W = 12.00, 5.00            # [2b] Web Agent
S5A_X, S5A_W = 0.00, 8.25           # [5a] Section 1
S5B_X, S5B_W = 8.75, 8.25           # [5b] Section 2

# ── inner sub-boxes of [2a] / [2b] (5 rows, AUDIT_GAP apart) ─────────────
SUB_X_OFF, SUB_W = 0.25, 4.50
SUB_H = box_h(2, title_pt=8)                                         # 1.817
SUB_TOP0 = TOP2 + PAD_T + LH10 + PAD_TB                              # 5.383
SUB_STEP = SUB_H + AUDIT_GAP                                        # 2.117

# ── audit gate inside [3] ────────────────────────────────────────────────
GATE_X, GATE_W = 5.75, 5.50
GATE_TOP = TOP2 + PAD_T + LH10 + PAD_TB + LH8 + PAD_TB               # 5.922
GI_X, GI_W = 6.00, 5.00
GI_H = box_h(1, title_pt=8)                                          # 1.517
GA_TOP = GATE_TOP + 0.25                                             # 6.172
GB_TOP = GA_TOP + GI_H + AUDIT_GAP                                   # 7.989
DIA_W, DIA_H = 3.20, 1.15
DIA_X = 6.90
DIA_TOP = GB_TOP + GI_H + AUDIT_GAP                                  # 9.806
REP_TOP = DIA_TOP + DIA_H + AUDIT_GAP                                # 11.256
REV_TOP = REP_TOP + GI_H + AUDIT_GAP                                 # 13.073
GATE_H = (0.25 + GI_H + AUDIT_GAP + GI_H + AUDIT_GAP + DIA_H +
          AUDIT_GAP + GI_H + AUDIT_GAP + GI_H + AUDIT_GAP + 0.30 + 0.25)

OUT_Y = REV_TOP + GI_H + AUDIT_GAP + 0.15                            # 14.284
GATE_BOT = GATE_TOP + GATE_H                                         # 14.684
LOOP_X = GATE_X + 0.13                                               # 5.88
NO_X = GATE_X + GATE_W - 0.13                                        # 11.12


def _x(x_cm):
    return x_cm / W_CM


def _y(top_cm, h_cm):
    return (H_CM - top_cm - h_cm) / H_CM


def _w(w_cm):
    return w_cm / W_CM


def _h(h_cm):
    return h_cm / H_CM


def N(nid, x_cm, top_cm, w_cm, h_cm, title, sub=None, fc=None,
      shape="round", ts=10, ss=8):
    return (nid, _x(x_cm), _y(top_cm, h_cm), _w(w_cm), _h(h_cm),
            title, sub, fc, ARROW, shape, ts, ss)


def T(x_cm, top_cm, w_cm, h_cm, text, size=7, color=GRAY_T, align="center",
      bold=False):
    return (_x(x_cm), _y(top_cm, h_cm), _w(w_cm), _h(h_cm), text, size,
            color, align, 0, bold)


def E(pts_cm, color=ARROW, lw=LW_ARROW, arrow=True):
    return ([(_x(px), (H_CM - py) / H_CM) for px, py in pts_cm],
            color, lw, "-", arrow)


# ───────────────────────── Figure 1: system master ─────────────────────────
LAYOUT1 = dict(
    W=W_IN, H=H_IN,
    nodes=[
        # ── Layer 0 ─────────────────────────────────────────────────────
        N("n0", 0.0, TOP0, 17.0, H0, "User input (CN)",
          "Intent confirmation (HITL) \u00b7 EN rewrite", S2),

        # ── Layer 1 ─────────────────────────────────────────────────────
        N("n1", 0.0, TOP1, 17.0, H1, "Supervisor orchestrator",
          "local_goal \u2225 web_goal \u00b7 budgets clamped in code\n"
          "asyncio.gather (concurrent)", S1),

        # ── Layer 2a: Local RAG Agent ───────────────────────────────────
        N("n2a", A2_X, TOP2, A2_W, H2, "Local RAG Agent", None, S3, "container"),
        N("a1", SUB_X_OFF, SUB_TOP0, SUB_W, SUB_H, "Corpus layer",
          "4,906 documents / 126,494 chunks\nfrozen manifest, DOI-keyed", S4, ts=8),
        N("a2", SUB_X_OFF, SUB_TOP0 + SUB_STEP, SUB_W, SUB_H, "Retrieval",
          "Dense e5-large / Sparse BM25+\nRRF k = 60, window W = 20", S4, ts=8),
        N("a3", SUB_X_OFF, SUB_TOP0 + 2 * SUB_STEP, SUB_W, SUB_H, "Rerank",
          "bge-reranker-v2-m3, top-K = 10\nT = max(0.25, s_top x 0.60)", S4, ts=8),
        N("a4", SUB_X_OFF, SUB_TOP0 + 3 * SUB_STEP, SUB_W, SUB_H, "Generation",
          "answers with [n], verbatim\nno [Wn] / URL; CN or EN", S4, ts=8),
        N("a5", SUB_X_OFF, SUB_TOP0 + 4 * SUB_STEP, SUB_W, SUB_H, "Orchestration",
          "budgets & caps; early stop\nevidence >= 6, gain < 0.25", S4, ts=8),

        # ── Layer 2b: Web Agent ─────────────────────────────────────────
        N("n2b", B2_X, TOP2, B2_W, H2, "Web Agent", None, S7, "container"),
        N("b1", B2_X + SUB_X_OFF, SUB_TOP0, SUB_W, SUB_H, "Round 1 search",
          "DeepSeek native, 90 s cap\nfail-open on timeout", S6, ts=8),
        N("b2", B2_X + SUB_X_OFF, SUB_TOP0 + SUB_STEP, SUB_W, SUB_H, "self-judge",
          "sufficient -> stop\notherwise retry once", S6, ts=8),
        N("b3", B2_X + SUB_X_OFF, SUB_TOP0 + 2 * SUB_STEP, SUB_W, SUB_H,
          "Round 2 retry",
          "<= 2 rounds hard cap\nsame cleaner and tierer", S6, ts=8),
        N("b4", B2_X + SUB_X_OFF, SUB_TOP0 + 3 * SUB_STEP, SUB_W, SUB_H,
          "cleaning",
          "drop duplicates, strip markup\nkeep snippets + URL + date", S6, ts=8),
        N("b5", B2_X + SUB_X_OFF, SUB_TOP0 + 4 * SUB_STEP, SUB_W, SUB_H,
          "source tiering",
          "T1 official, T2 media\nT3 self-media, cannot support", S6, ts=8),

        # ── Layer 2c: Assembly & audit (nested gate) ────────────────────
        N("n3", A3_X, TOP2, A3_W, H2, "Assembly & audit", None, S6, "container"),
        N("gate", GATE_X, GATE_TOP, GATE_W, GATE_H, "", None, None, "container"),
        N("ga", GI_X, GA_TOP, GI_W, GI_H, "Stage A", "code hard gate", S5, ts=8),
        N("gb", GI_X, GB_TOP, GI_W, GI_H, "Stage B", "model review", S5, ts=8),
        N("dia", DIA_X, DIA_TOP, DIA_W, DIA_H, "critical?", None, S8, "diamond",
          ts=8),
        N("rep", GI_X, REP_TOP, GI_W, GI_H, "one minimal repair",
          "one pass, count capped", S7, ts=8),
        N("rev", GI_X, REV_TOP, GI_W, GI_H, "re-validate",
          "back to Stage A", S5, ts=8),

        # ── Layer 3 ─────────────────────────────────────────────────────
        N("n4", 0.0, TOP4, 17.0, H4, "Supervisor final",
          "audit passed? \u00b7 final decision: pass / partial", S1),

        # ── Layer 4 ─────────────────────────────────────────────────────
        N("n5a", S5A_X, TOP5, S5A_W, H5, "Section 1 - local",
          "[n] citations \u00b7 verbatim\nno [Wn] / URL", S3),
        N("n5b", S5B_X, TOP5, S5B_W, H5, "Section 2 - web",
          "URL + tier (T1/T2/T3)\nnever pooled with local", S4),
    ],
    edges=[
        # ── main chain ──────────────────────────────────────────────────
        E([(8.5, TOP0 + H0), (8.5, TOP1)]),                       # [0] -> [1]
        E([(6.2, TOP1 + H1), (2.5, TOP2)]),                       # [1] -> [2a]
        E([(10.8, TOP1 + H1), (14.5, TOP2)]),                     # [1] -> [2b]
        E([(A2_X + A2_W, TOP2 + H2 / 2), (A3_X, TOP2 + H2 / 2)]),  # [2a] -> [3]
        E([(B2_X, TOP2 + H2 / 2), (A3_X + A3_W, TOP2 + H2 / 2)]),  # [2b] -> [3]
        # [4] -> [5a] / [5b]: symmetric fan-out about x = 8.5 (figure centre).
        # starts at 8.5 -/+ 2.30 (same spread as the [1] fan-out), ends on the
        # centre line of each output block -> both arrows have |dx| = 2.075.
        E([(6.20, TOP4 + H4), (S5A_X + S5A_W / 2, TOP5)]),        # [4] -> [5a]
        E([(10.80, TOP4 + H4), (S5B_X + S5B_W / 2, TOP5)]),       # [4] -> [5b]

        # ── audit gate: clean top-down loop, ALL inside the gate ────────
        E([(8.5, GA_TOP + GI_H), (8.5, GB_TOP)]),                 # A -> B
        E([(8.5, GB_TOP + GI_H), (8.5, DIA_TOP)]),                # B -> critical?
        E([(8.5, DIA_TOP + DIA_H), (8.5, REP_TOP)]),              # yes -> repair
        E([(8.5, REP_TOP + GI_H), (8.5, REV_TOP)]),               # repair -> re-val
        # re-validate -> loop back to Stage A, up the gate's LEFT channel
        E([(GI_X, REV_TOP + GI_H / 2), (LOOP_X, REV_TOP + GI_H / 2),
           (LOOP_X, GA_TOP + GI_H / 2), (GI_X, GA_TOP + GI_H / 2)]),
        # critical? NO -> down the gate's RIGHT channel (no right-hand detour)
        E([(DIA_X + DIA_W, DIA_TOP + DIA_H / 2), (NO_X, DIA_TOP + DIA_H / 2),
           (NO_X, OUT_Y), (8.5, OUT_Y)]),
        # re-validate passed -> down to the same merge point
        E([(8.5, REV_TOP + GI_H), (8.5, OUT_Y)]),
        # single output: gate bottom -> through [3] bottom -> [4]
        E([(8.5, OUT_Y), (8.5, TOP4)]),
    ],
    texts=[
        # assembly note inside [3]
        T(A3_X + 0.25, TOP2 + PAD_T + LH10 + PAD_TB, A3_W - 0.5, 0.40,
          "Assembly: renumber, drop dead markers", 7, GRAY_T, "left"),
        # gate branch labels
        T(8.62, DIA_TOP + DIA_H + 0.02, 1.2, 0.30, "yes", 7, GRAY_T, "left"),
        T(DIA_X + DIA_W + 0.06, DIA_TOP - 0.34, 1.2, 0.30, "no", 7, GRAY_T,
          "left"),
        # loop caption
        T(GI_X + 0.10, REV_TOP + GI_H + 0.02, 3.4, 0.30,
          "loop back to Stage A", 7, GRAY_T, "left"),
        # output caption
        T(8.62, OUT_Y + 0.04, 4.0, 0.30, "output to [4]", 7, GRAY_T, "left"),
    ],
    caption=("CitrusQA multi-agent end-to-end architecture: user input, Supervisor orchestrator, parallel "
             "Local RAG and Web Agent chains, Assembly with a nested audit gate, Supervisor final, "
             "two-section output."),
    source="Content basis: manuscript sections 3.1-3.4.",
    note=("Colour semantics: S1 = Supervisor, S3 = Local RAG Agent, S7 = Web Agent, S6 = Assembly. "
          "The two agents run concurrently and differ in visible information, decision authority "
           
          "(native web search). The audit gate loops top-down: Stage A -> Stage B -> critical?; "
          "'yes' enters one minimal repair then re-validate and loops back to Stage A, 'no' leaves "
          "through the gate's right channel. Both outputs exit the gate bottom and flow down to [4]. "
          "Total width 17.0 cm; block gaps 0.5 cm, audit-gate internals 0.3 cm. "
          "Arrows #333333 0.5 pt solid. No dashed lines."),
    legend=("Colour semantics: S1 = Supervisor  \u00b7  S3 = Local RAG Agent  \u00b7  "
            "S7 = Web Agent  \u00b7  S6 = Assembly"),
)

# ───────────────────── Figure 2: corpus pipeline ─────────────────────
# v11: canvas height cut to 50% of the previous value (3.4 -> 1.7 in) so the
# four stage blocks match their content height instead of leaving blank space.
LAYOUT2 = dict(
    W=11.0, H=1.7,
    nodes=[
        ("s1", 0.015, 0.09, 0.23, 0.84, "(1) Sources",
         "Web of Science / Scopus / PubMed\n"
         "Journal whitelist (T1 only)\n"
         "Metadata normalisation: DOI dedup,\n"
         "Title 100%, Year 90.7%\n"
         "Scale: frozen corpus",
         S1, ARROW, "round", 10, 8),
        ("s2", 0.275, 0.09, 0.23, 0.84, "(2) Cleaning & chunking",
         "Encoding unified\n"
         "Structural field extraction\n"
         "Chunking: Markdown-heading split,\n"
         "recursive character re-split\n"
         "Scale: 4,906 documents",
         S2, ARROW, "round", 10, 8),
        ("s3", 0.535, 0.09, 0.23, 0.84, "(3) Indexing",
         "Dense: e5-large 1024-dim,\n"
         "LanceDB IVF_HNSW, cosine\n"
         "Sparse: BM25+ (offline build,\n"
         "no standalone fallback)\n"
         "Fusion: RRF (k = 60)\n"
         "Scale: 126,494 chunks",
         S2, ARROW, "round", 10, 8),
        ("s4", 0.795, 0.09, 0.19, 0.84, "(4) Serving",
         "Retrieval (FastAPI + SSE)\n"
         "Local and web channels served in parallel\n"
         "Corpus fingerprint hot update\n"
         "Batch-level isolation: hot reload\n"
         "at startup, no runtime detach",
         S3, ARROW, "round", 10, 8),
    ],
    edges=[
        ([(0.245, 0.51), (0.275, 0.51)], ARROW, LW_ARROW, "-", True),
        ([(0.505, 0.51), (0.535, 0.51)], ARROW, LW_ARROW, "-", True),
        ([(0.765, 0.51), (0.795, 0.51)], ARROW, LW_ARROW, "-", True),
    ],
    texts=[],
    stats=("4,906 documents  /  126,494 chunks  /  "
           "no Chinese-dominant chunks (Chinese sources ingested as English translations)"),
    caption="Corpus construction and retrieval pipeline: sources, cleaning & chunking, indexing, serving.",
    source="Content basis: manuscript section 4.",
    note=("Statistics live only in the bottom gray strip (independent text box, not part of the figure). "
          "Title 10 pt bold, body 8 pt. Arrows #333333 0.5 pt solid; no dashed lines. "
          "Full corpus tables are Tables 11-13."),
)


# ═══════════════ PI palette overlay (2026-10-16) — COLOURS ONLY ═══════════════
# Semantics of the original layout (see LAYOUT1["legend"]):
#   S1 = Supervisor · S3 = Local RAG Agent · S7 = Web Agent · S6 = Assembly & audit
# Mapping to the PI standard palette: large areas keep the light tint, the
# primary colour carries title bands and box borders; arrows are the secondary
# ink. Geometry, text and wrapping are untouched.
SUP, SUP_L = "#287D99", "#E4F2F8"       # Supervisor / orchestration
RAG, RAG_L = "#4F8868", "#E7F1E5"       # Local RAG / local literature
WEB, WEB_L = "#C87558", "#FBEAE1"       # Web / external evidence
AUD, AUD_L = "#BE9346", "#FAF0D8"       # Assembly & audit
MET, MET_L = "#7772A8", "#F0EDFA"       # metadata (unused in LAYOUT1)
INK, INK2, BORDER = "#26343B", "#52616A", "#D4DCE0"

_WHITE = "#FFFFFF"
_NODE_COLOURS = {
    "n0": (_WHITE, SUP),                       # user input
    "n1": (SUP_L, SUP), "n4": (SUP_L, SUP),    # supervisor bands
    "n2a": (RAG_L, RAG),                       # local-rag container
    "a1": (_WHITE, RAG), "a2": (_WHITE, RAG), "a3": (_WHITE, RAG),
    "a4": (_WHITE, RAG), "a5": (_WHITE, RAG),  # local-rag sub-cards
    "n2b": (WEB_L, WEB),                       # web container
    "b1": (_WHITE, WEB), "b2": (_WHITE, WEB), "b3": (_WHITE, WEB),
    "b4": (_WHITE, WEB), "b5": (_WHITE, WEB),  # web sub-cards
    "n3": (AUD_L, AUD),                        # assembly & audit container
    "ga": (_WHITE, AUD), "gb": (_WHITE, AUD), "rev": (_WHITE, AUD),
    "dia": (AUD_L, AUD), "rep": (AUD_L, AUD),  # gate internals
    "gate": (None, BORDER),                    # nested gate frame
    "n5a": (RAG_L, RAG), "n5b": (WEB_L, WEB),  # two-section output
}


def _apply_pi_palette(lay):
    nodes = []
    for n in lay["nodes"]:
        nid = n[0]
        fc, ec = _NODE_COLOURS.get(nid, (n[7], n[8]))
        nodes.append((n[0], n[1], n[2], n[3], n[4], n[5], n[6], fc, ec,
                      n[9], n[10], n[11]))
    edges = [(pts, INK2, lw, ls, arrow)
             for (pts, _c, lw, ls, arrow) in lay["edges"]]
    texts = [(x, y, w, h, s, size, INK2, align, rot, bold)
             for (x, y, w, h, s, size, _c, align, rot, bold) in lay["texts"]]
    return dict(lay, nodes=nodes, edges=edges, texts=texts)


LAYOUT1 = _apply_pi_palette(LAYOUT1)


# ═══════════ PI-ordered flow fixes (2026-10-16) — arrows & four labels ═══════════
# Implementation basis:
#   * Local RAG per-iteration order  : multi_retriever (retrieve → rerank) → generation
#   * agent loop cap / early stop    : config.MAX_REACT_STEPS=5,
#                                      agent_runner.py: prev_unique ≥ 6 and new_ratio < 0.25
#   * web rounds (conditional, cap 2): web_agent_loop.py, web_agent.max_rounds = 2
#   * web evidence layer order       : web_evidence_pipeline.LAYER_ORDER
#                                      = (fetch, tiers, content, meta, embed, adjudicate, rerank)
_OUT_PORT_Y = TOP2 + H2 - 0.28            # container output port, just above the bottom
_MID_Y = TOP2 + H2 / 2                    # audit container vertical centre
_GAP_L = (A2_X + A2_W + A3_X) / 2         # centre of the local→audit gap (5.25)
_GAP_R = (A3_X + A3_W + B2_X) / 2         # centre of the audit→web gap (11.75)
_WEB_CX = B2_X + SUB_X_OFF + SUB_W / 2    # web sub-card centre line (14.50)
_LOC_CX = A2_X + SUB_X_OFF + SUB_W / 2    # local sub-card centre line (2.50)


def _card(i):
    """(top, bottom) of layer-2 sub-card i, cm from the figure top."""
    top = SUB_TOP0 + i * SUB_STEP
    return top, top + SUB_H


_FLOW_TITLES = {
    # PI review 2026-10-16: "CN" is an ISO-3166 country code, not a language code
    # (ISO 639-1 for Chinese is "zh") → the band title carries the language code ZH.
    "n0": "User input (ZH)",
}
# PI review 2026-10-16: these notes must be horizontally centred on their container
# (same anchor as the centred container title); vertical placement is untouched.
_FLOW_CENTER = ("Assembly: renumber, drop dead markers",)
_FLOW_TEXT = {
    # PI font ruling: Arial has no U+2225 (PARALLEL TO) → use the ASCII "||" so the
    # figure stays single-font (matplotlib would otherwise fall back to DejaVu Sans).
    "n1": ("Supervisor orchestrator",
           "local_goal || web_goal \u00b7 budgets clamped in code\nasyncio.gather (concurrent)"),
    # PI plan 2026-10-16: unify "translation, never rewriting" — the figure must not
    # say "EN rewrite" (the code translates faithfully and stores the intent anchor)
    "n0": ("User input (ZH)",
           "Intent confirmation (HITL) · faithful EN translation"),
    "a4": ("Generation", "answers with [n], verbatim\nno [Wn] / URL; ZH or EN"),
    "a5": ("Orchestration", "budgets & caps; early stop\nevidence ≥ 6 and new < 25%"),
    # LAYER_ORDER: deterministic tier tagging (L1) precedes content cleaning (L2)
    "b4": ("tier tagging", "T1 official, T2 media\nT3 self-media (labels only)"),
    "b5": ("cleaning & metadata", "drop duplicates, strip markup\nkeep snippet + URL + date"),
    "n5b": ("Section 2 - web", "URL + tier (T1/T2/T3)\nnot in the local citation pool"),
}
_FLOW_EDGES_CM = [
    # 1) Local RAG output port → audit  (leaves the container after its last card)
    [(A2_X + A2_W, _OUT_PORT_Y), (_GAP_L, _OUT_PORT_Y), (_GAP_L, _MID_Y), (A3_X, _MID_Y)],
    # 2) Web Agent output port → audit  (leaves after the whole web pipeline)
    [(B2_X, _OUT_PORT_Y), (_GAP_R, _OUT_PORT_Y), (_GAP_R, _MID_Y), (A3_X + A3_W, _MID_Y)],
    # 3) Supervisor orchestrator also drives Assembly & audit (straight centre line)
    [(8.5, TOP1 + H1), (8.5, TOP2)],
    # 4) Local RAG internal (per-iteration): retrieval → rerank → generation
    [(_LOC_CX, _card(1)[1] + 0.03), (_LOC_CX, _card(2)[0] - 0.03)],
    [(_LOC_CX, _card(2)[1] + 0.03), (_LOC_CX, _card(3)[0] - 0.03)],
    # 4b) code-level agent loop: Orchestration → Retrieval (left inner channel)
    [(SUB_X_OFF, _card(4)[0] + SUB_H / 2), (A2_X + 0.12, _card(4)[0] + SUB_H / 2),
     (A2_X + 0.12, _card(1)[0] + SUB_H / 2), (SUB_X_OFF, _card(1)[0] + SUB_H / 2)],
    # 5) Web internal: search → self-judge → (Round 2, conditional) → tiering → cleaning
    [(_WEB_CX, _card(0)[1] + 0.03), (_WEB_CX, _card(1)[0] - 0.03)],
    [(_WEB_CX, _card(1)[1] + 0.03), (_WEB_CX, _card(2)[0] - 0.03)],
    [(_WEB_CX, _card(2)[1] + 0.03), (_WEB_CX, _card(3)[0] - 0.03)],
    [(_WEB_CX, _card(3)[1] + 0.03), (_WEB_CX, _card(4)[0] - 0.03)],
]
_DROPPED_STARTS_CM = [(A2_X + A2_W, _MID_Y), (B2_X, _MID_Y)]      # old branch arrows


def _frac(pts_cm):
    return [(_x(px), (H_CM - py) / H_CM) for px, py in pts_cm]


def _same_f(p, q):
    return abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9


def _apply_flow_fixes(lay):
    # (a) label corrections, geometry untouched
    nodes = []
    for n in lay["nodes"]:
        title, sub = _FLOW_TEXT.get(n[0], (n[5], n[6]))
        title = _FLOW_TITLES.get(n[0], title)
        nodes.append((n[0], n[1], n[2], n[3], n[4], title, sub, n[7], n[8], n[9], n[10], n[11]))

    # (b) drop the two old mid-height branch arrows
    dropped = [_frac([p])[0] for p in _DROPPED_STARTS_CM]
    edges = [e for e in lay["edges"]
             if not (len(e[0]) == 2 and any(_same_f(e[0][0], d) for d in dropped))]
    assert len(lay["edges"]) - len(edges) == 2, "expected to replace exactly 2 branch arrows"

    # (c) add the corrected routes
    for pts_cm in _FLOW_EDGES_CM:
        edges.append((_frac(pts_cm), ARROW, LW_ARROW, "-", True))

    # (d) label of the gate output; centre the container note on its title anchor;
    #     add the "if insufficient" tag on the conditional round
    texts = []
    for (x, y, w, h, txt, size, c, al, rot, b) in lay["texts"]:
        if txt == "output to [4]":
            texts.append((x, y, w, h, "to Supervisor final", size, c, al, rot, b))
        elif txt in _FLOW_CENTER:
            # same anchor as the centred container title (x + w/2 = A3_X + A3_W/2),
            # vertical placement (y, h) untouched
            texts.append((_x(A3_X), y, _w(A3_W), h, txt, size, c, "center", rot, b))
        else:
            texts.append((x, y, w, h, txt, size, c, al, rot, b))
    texts.append(T(_WEB_CX + 0.58, _card(1)[1] - 0.02, 2.6, 0.30,
                   "if insufficient", 6.5, GRAY_T, "left"))
    return dict(lay, nodes=nodes, edges=edges, texts=texts)


LAYOUT1 = _apply_flow_fixes(LAYOUT1)
LAYOUT1["legend"] = "Colour semantics: Supervisor · Local RAG Agent · Web Agent · Assembly & audit"

# ═══════════════ PI font/size ruling (2026-10-16) — fonts & sizes ONLY ═══════════════
# Arial everywhere, three levels only:
#   10 pt  — level-1 module titles (bands + the three agent containers)
#    9 pt  — level-2 box titles (sub-cards, audit-gate boxes, the decision diamond)
#    8 pt  — module descriptions AND every auxiliary/arrow label
# Nothing else changes: no geometry, no text content, no colours.
_FONT_SCALE_BANDS = {"n0", "n1", "n2a", "n2b", "n3", "n4", "n5a", "n5b"}
_FONT_LEVEL1, _FONT_LEVEL2, _FONT_BODY = 10, 9, 8


def _apply_font_scale(lay):
    nodes = []
    for n in lay["nodes"]:
        ts = _FONT_LEVEL1 if n[0] in _FONT_SCALE_BANDS else _FONT_LEVEL2
        nodes.append((n[0], n[1], n[2], n[3], n[4], n[5], n[6], n[7], n[8], n[9],
                      ts, _FONT_BODY))
    texts = [(x, y, w, h, txt, _FONT_BODY, c, al, rot, b)
             for (x, y, w, h, txt, size, c, al, rot, b) in lay["texts"]]
    return dict(lay, nodes=nodes, texts=texts)


LAYOUT1 = _apply_font_scale(LAYOUT1)

# Two auxiliary labels were widened past their container / across an arrow by the
# size bump (7→8 pt). Keep the same text, same size band, same vertical position;
# only the box anchor changes so the text stays inside its container / clear of the
# audit output line at x = 8.5 cm.
_TEXT_FIT = {
    "loop back to Stage A": lambda: (GI_X, REV_TOP + GI_H + 0.02, 2.42, 0.30, "right"),
    "if insufficient": lambda: (_WEB_CX - 0.30, _card(1)[1] - 0.02, 2.70, 0.30, "right"),
}


def _apply_text_fit(lay):
    texts = []
    for (x, y, w, h, txt, size, c, al, rot, b) in lay["texts"]:
        if txt in _TEXT_FIT:
            nx, ny, nw, nh, nal = _TEXT_FIT[txt]()
            texts.append((_x(nx), _y(ny, nh), _w(nw), _h(nh), txt, size, c, nal, rot, b))
        else:
            texts.append((x, y, w, h, txt, size, c, al, rot, b))
    return dict(lay, texts=texts)


LAYOUT1 = _apply_text_fit(LAYOUT1)
