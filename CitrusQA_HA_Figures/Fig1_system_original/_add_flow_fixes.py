# -*- coding: utf-8 -*-
"""Append the PI-ordered FLOW fixes + Arial font to the working copies.

Coordinate conventions of flow_layout.py (must be respected):
  * N()/T() store fractions via _x/_y   (y = (H_CM - top - h)/H_CM, bottom-left origin)
  * E()  stores fractions via _x and (H_CM - y_cm)/H_CM  (a line position, top-down)

All changes are arrow routing / four labels / font family — no geometry moves.
Justification for each change is recorded in Fig1_system_original/README.md.
"""
import re
from pathlib import Path

D = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\Fig1_system_original")

# ─────────────────────────────────────────────────────────────── flow_layout.py
P = D / "flow_layout.py"
t = P.read_text(encoding="utf-8")
assert "π" not in t or True
BLOCK = '''

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
           "local_goal || web_goal \\u00b7 budgets clamped in code\\nasyncio.gather (concurrent)"),
    # PI plan 2026-10-16: unify "translation, never rewriting" — the figure must not
    # say "EN rewrite" (the code translates faithfully and stores the intent anchor)
    "n0": ("User input (ZH)",
           "Intent confirmation (HITL) · faithful EN translation"),
    "a4": ("Generation", "answers with [n], verbatim\\nno [Wn] / URL; ZH or EN"),
    "a5": ("Orchestration", "budgets & caps; early stop\\nevidence ≥ 6 and new < 25%"),
    # LAYER_ORDER: deterministic tier tagging (L1) precedes content cleaning (L2)
    "b4": ("tier tagging", "T1 official, T2 media\\nT3 self-media (labels only)"),
    "b5": ("cleaning & metadata", "drop duplicates, strip markup\\nkeep snippet + URL + date"),
    "n5b": ("Section 2 - web", "URL + tier (T1/T2/T3)\\nnot in the local citation pool"),
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
'''
P.write_text(t + BLOCK, encoding="utf-8")
print("flow_layout.py: palette + flow-fix overlays in place")

# ─────────────────────────────────────────────────────────── Arial everywhere
p = D / "mpl_style.py"
t = p.read_text(encoding="utf-8")
t = t.replace('"font.family": ["Times New Roman", "SimSun"]',
              '"font.family": ["Arial"]')
t = re.sub(r'"font\.sans-serif": \[[^\]]*\]', '"font.sans-serif": ["Arial"]', t)
p.write_text(t, encoding="utf-8")
print("mpl_style.py: font.family → Arial (single family, no fallback)")

p = D / "ppt_style.py"
t = p.read_text(encoding="utf-8")
t = t.replace('FONT_LATIN = "Times New Roman"', 'FONT_LATIN = "Arial"')
# PI ruling: latin runs must use Arial. The original routed EVERY run to the CJK
# face, which rendered English in SimSun inside the PPT twin.
old_style = ('    f.name = FONT_LATIN if cjk is False else FONT_CJK\n'
             '    if cjk is not False:\n'
             '        set_cjk(f, FONT_CJK)')
new_style = ('    f.name = FONT_LATIN\n'
             '    set_cjk(f, FONT_CJK)')
if old_style in t:
    t = t.replace(old_style, new_style)
    print("ppt_style.py: latin runs → Arial (CJK face kept for CJK glyphs)")
else:
    print("ppt_style.py: _style_run already patched")
p.write_text(t, encoding="utf-8")

# PI ruling: in the PPT twin every text box shrinks to fit instead of spilling
# (Office metrics are wider than the matplotlib wrap model).
p = D / "ppt_twin.py"
t = p.read_text(encoding="utf-8")
anchor = "import flow_layout as L\n"
inject = anchor + '''
# ── PI font ruling: autofit so no text spills out of its box in the PPT twin ──
_ps_add_text = ps.add_text


def _autofit_add_text(*a, **kw):
    tb = _ps_add_text(*a, **kw)
    from pptx.enum.text import MSO_AUTO_SIZE
    tb.text_frame.auto_size = MSO_AUTO_SIZE.TEXT_TO_FIT_SHAPE
    return tb


ps.add_text = _autofit_add_text
'''
if "_autofit_add_text" not in t:
    t = t.replace(anchor, inject, 1)
    print("ppt_twin.py: every text box set to shrink-to-fit")
else:
    print("ppt_twin.py: already patched")
p.write_text(t, encoding="utf-8")
