# -*- coding: utf-8 -*-
"""Set up the ORIGINAL Fig.1 (pre-Round-1) pipeline with the PI palette applied.

Base (untouched, read-only):
  paper/系统篇_v7/figures_main/fig1_system/{flow_system_master.py, flow_layout.py, mpl_style.py}
  experiment/2026927-python绘图latest/00_shared/{flow_layout.py, ppt_style.py, ppt_twin.py}

This script COPIES those files into Fig1_system_original/ and applies a
colour-only overlay:
  * node fills/edges remapped to the PI semantic palette (Supervisor / Local RAG /
    Web / Audit),
  * title text #1F1F1F -> #26343B, body #666666 -> #52616A, arrows -> #52616A,
  * NO geometry, text or layout edits (verified afterwards by a colour-normalised
    SVG diff against the original render).
"""
import re, shutil
from pathlib import Path

R = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
FIGDIR = R / "paper/系统篇_v7/figures_main/fig1_system"
SHARED = R / "experiment/2026927-python绘图latest/00_shared"
DST = R / "CitrusQA_HA_Figures/Fig1_system_original"
DST.mkdir(parents=True, exist_ok=True)
(DST / "out").mkdir(exist_ok=True)

for src, name in ((FIGDIR / "flow_system_master.py", "flow_system_master.py"),
                  (FIGDIR / "flow_layout.py", "flow_layout.py"),
                  (FIGDIR / "mpl_style.py", "mpl_style.py"),
                  (SHARED / "ppt_style.py", "ppt_style.py"),
                  (SHARED / "ppt_twin.py", "ppt_twin.py")):
    shutil.copy2(src, DST / name)
    print("copied", name, "<<", src.relative_to(R))

# ── 1) flow_layout.py : append a palette overlay for LAYOUT1 (colours only) ──
p = DST / "flow_layout.py"
t = p.read_text(encoding="utf-8")
t += '''

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
'''
p.write_text(t, encoding="utf-8")
print("flow_layout.py: palette overlay appended")

# ── 2) flow_system_master.py : local paths + PI text colours ──
p = DST / "flow_system_master.py"
t = p.read_text(encoding="utf-8")
t = t.replace('sys.path.insert(0, os.path.join(HERE, "..", "..", "00_shared"))',
              'sys.path.insert(0, HERE)')
t = t.replace('OUTDIR = os.path.join(HERE, "..", "..", "40_mechanisms")',
              'OUTDIR = os.path.join(HERE, "out")')
t = t.replace('OUT = "flow_system_master"', 'OUT = "fig1_system_original"')
t = t.replace('color="#1F1F1F"', 'color="#26343B"')
t = t.replace('color="#666666"', 'color="#52616A"')
p.write_text(t, encoding="utf-8")
print("flow_system_master.py: paths + text colours patched")

# ── 3) mpl_style.py : only the defaults this figure uses ──
p = DST / "mpl_style.py"
t = p.read_text(encoding="utf-8")
t = re.sub(r'RULE, MUTED = "#333333", "#7A7A7A"', 'RULE, MUTED = "#52616A", "#B9C2C7"', t)
t = t.replace('tc="#1F1F1F", sc="#666666"', 'tc="#26343B", sc="#52616A"')
t = t.replace('diamond(ax, cx, cy, w, h, text, fc="#fded95", ec="#C9A83A"',
              'diamond(ax, cx, cy, w, h, text, fc="#FAF0D8", ec="#BE9346"')
t = t.replace('color="#4A4A4A")', 'color="#52616A")')
t = t.replace('color="#4A4A4A")', 'color="#52616A")')
t = t.replace('color="#666666"', 'color="#52616A"')
p.write_text(t, encoding="utf-8")
print("mpl_style.py: text/rule/diamond colours patched")

# ── 4) ppt_style.py : ink colours ──
p = DST / "ppt_style.py"
t = p.read_text(encoding="utf-8")
t = t.replace('INK = "#1F1F1F"', 'INK = "#26343B"')
t = t.replace('INK_SOFT = "#4A4A4A"', 'INK_SOFT = "#52616A"')
t = t.replace('INK_MUTED = "#7A7A7A"', 'INK_MUTED = "#B9C2C7"')
t = re.sub(r'^RULE = "#[0-9A-Fa-f]{6}"', 'RULE = "#52616A"', t, flags=re.M)
p.write_text(t, encoding="utf-8")
print("ppt_style.py: ink colours patched")

# ── 5) ppt_twin.py : ink colour + single-slide deck for LAYOUT1 ──
p = DST / "ppt_twin.py"
t = p.read_text(encoding="utf-8")
t = t.replace('GRAY_T = "#666666"', 'GRAY_T = "#52616A"')
t = t.replace('color="#666666"', 'color="#52616A"')
marker = "def main():"
head = t[:t.index(marker)]
new_main = '''def main():
    """Editable PPT twin of the ORIGINAL Fig.1 layout (LAYOUT1 only), PI palette."""
    prs = ps.new_deck()
    W_CM = L.LAYOUT1["W"] * 2.54
    H_CM = L.LAYOUT1["H"] * 2.54 + 1.30          # room for the legend box
    prs.slide_width = ps.Cm(W_CM)
    prs.slide_height = ps.Cm(H_CM)
    render_layout(prs, L.LAYOUT1)
    legend = L.LAYOUT1.get("legend", "")
    if legend:
        ps.add_text(prs.slides[0], 0.30, H_CM - 1.05, W_CM - 0.60, 0.80,
                    legend, size=7, color="#52616A", align="left")
    out = os.path.join(HERE, "out", "fig1_system_original.pptx")
    prs.save(out)
    print("saved", out)


if __name__ == "__main__":
    main()
'''
p.write_text(head + new_main, encoding="utf-8")
print("ppt_twin.py: single-slide LAYOUT1 deck + local output")
print("\nwork dir:", DST)
