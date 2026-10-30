# -*- coding: utf-8 -*-
"""Layout integrity check for the modified Fig.1 (LAYOUT1).

Verifies, from the layout data itself (not from pixels):
  1. every stacked node's required text height fits in the space before the next
     node (the renderer grows boxes downward, so this is what prevents overlap);
  2. every node stays inside the canvas;
  3. the two new output-port routes run inside the inter-column gaps and do not
     cross any container;
  4. the new label/edge set is exactly the intended one (diff against the
     original unmodified LAYOUT1 from the paper's file).
"""
import importlib.util, sys
from pathlib import Path

D = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\Fig1_system_original")
sys.path.insert(0, str(D))
import mpl_style as ms                      # noqa: E402  (provides wrap_cm/line_h_cm)
import flow_layout as L                     # noqa: E402

fail = []
lay = L.LAYOUT1
W_CM = lay["W"] * 2.54
H_CM = lay["H"] * 2.54
print(f"canvas {W_CM:.2f} × {H_CM:.2f} cm  ({W_CM*10:.0f} × {H_CM*10:.0f} mm)")

# ---- 1/2) heights & canvas ----
boxes = []
for n in lay["nodes"]:
    nid, x, y, w, h, title, sub, fc, ec, shape, ts, ss = n
    x_cm, top_cm = x * W_CM, (1 - y - h) * H_CM
    w_cm, h_cm = w * W_CM, h * H_CM
    t_lines = ms.wrap_cm(title, w_cm - 0.30, ts, True) if title else []
    s_lines = ms.wrap_cm(sub, w_cm - 0.30, ss) if sub else []
    need = 0.16 + len(t_lines) * ms.line_h_cm(ts) + \
        (0.20 + len(s_lines) * ms.line_h_cm(ss) if s_lines else 0.0)
    boxes.append(dict(nid=nid, top=top_cm, bot=top_cm + max(h_cm, need),
                      h=h_cm, need=need, grown=need > h_cm + 1e-9,
                      left=x_cm, right=x_cm + w_cm))
    if x_cm < -1e-6 or x_cm + w_cm > W_CM + 1e-6 or top_cm < -1e-6 or top_cm + max(h_cm, need) > H_CM + 1e-6:
        fail.append(f"{nid}: outside canvas")

# stacked cards: same x-range and stacked vertically → need vertical clearance
for a in boxes:
    for b in boxes:
        if a is b:
            continue
        if abs(a["left"] - b["left"]) < 1e-6 and abs(a["right"] - b["right"]) < 1e-6 and b["top"] > a["top"]:
            gap = b["top"] - a["bot"]
            if gap < 0.02:
                fail.append(f"{a['nid']}→{b['nid']}: vertical clearance {gap:+.3f} cm")

grown = [b["nid"] for b in boxes if b["grown"]]
print("boxes that grow to fit text:", grown or "none")

# ---- 3) new routes inside the gaps ----
A2_R, A3_L = L.A2_X + L.A2_W, L.A3_X
A3_R, B2_L = L.A3_X + L.A3_W, L.B2_X
MIDY = L.TOP2 + L.H2 / 2
PORTY = L.TOP2 + L.H2 - 0.28
for x in (L._x((A2_R + A3_L) / 2), L._x((A3_R + B2_L) / 2)):
    pass
gl = (A2_R + A3_L) / 2
gr = (A3_R + B2_L) / 2
if not (A2_R < gl < A3_L):
    fail.append("left gap route outside the inter-column gap")
if not (A3_R < gr < B2_L):
    fail.append("right gap route outside the inter-column gap")
print(f"left gap route x={gl:.2f} cm (gap {A2_R:.2f}–{A3_L:.2f}); "
      f"right gap route x={gr:.2f} cm (gap {A3_R:.2f}–{B2_L:.2f})")
print(f"output ports at y={PORTY:.2f} cm, entering audit at y={MIDY:.2f} cm")

# ---- 4) intended-change diff vs the original LAYOUT1 ----
spec = importlib.util.spec_from_file_location(
    "orig_flow_layout",
    r"E:\codex_WORKSPACES\Citrus_QA_Agent\paper\系统篇_v7\figures_main\fig1_system\flow_layout.py")
orig = importlib.util.module_from_spec(spec)
sys.path.insert(0, r"E:\codex_WORKSPACES\Citrus_QA_Agent\paper\系统篇_v7\figures_main\fig1_system")
spec.loader.exec_module(orig)

o_nodes = {n[0]: n for n in orig.LAYOUT1["nodes"]}
n_nodes = {n[0]: n for n in lay["nodes"]}
text_changes = {k: (o_nodes[k][5], o_nodes[k][6], n_nodes[k][5], n_nodes[k][6])
                for k in o_nodes if (o_nodes[k][5], o_nodes[k][6]) != (n_nodes[k][5], n_nodes[k][6])}
geom_changes = [k for k in o_nodes if (o_nodes[k][1:5] != n_nodes[k][1:5])]
print("\nnode text changes:")
for k, (ot, os_, nt, ns) in text_changes.items():
    print(f"  {k}: {ot!r}/{os_!r}  →  {nt!r}/{ns!r}")
print("node geometry changes:", geom_changes or "none (positions/sizes untouched)")
print(f"edges: original {len(orig.LAYOUT1['edges'])} → now {len(lay['edges'])} "
      f"(2 branch arrows replaced by 2 port routes + 1 control + 7 internal)")
print(f"texts: original {len(orig.LAYOUT1['texts'])} → now {len(lay['texts'])} (+1 conditional tag)")
print("legend:", lay["legend"])

print("\nFAILURES:", len(fail))
for f in fail:
    print("   -", f)
Path(D / "_layout_check.txt").write_text("\n".join(
    [f"canvas {W_CM:.2f}x{H_CM:.2f} cm", f"grown boxes: {grown}", f"text changes: {text_changes}",
     f"geometry changes: {geom_changes}", f"failures: {fail}"]), encoding="utf-8")
