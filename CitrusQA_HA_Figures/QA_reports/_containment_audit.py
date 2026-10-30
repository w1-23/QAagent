# -*- coding: utf-8 -*-
"""Container-containment audit: every <text> must sit inside its enclosing box.

For each text node we pick the smallest <rect>/panel that horizontally contains
the text anchor, then check the glyph box (cap height above the baseline,
descender below) against that container's top/bottom edges. Reports violations —
this catches the class of defect that width-only fit() checks miss
(text hanging outside its card/badge/chip/panel).
"""
import sys, json
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass
from pathlib import Path
from lxml import etree

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
TARGETS = {"Fig1": ROOT / "Fig1/fig1_system.svg", "Fig2": ROOT / "Fig2/fig2_corpus.svg"}
CAP, DESC = 0.72, 0.22          # fraction of font-size above/below the baseline


def audit(svg: Path, pad=0.6):
    root = etree.parse(str(svg)).getroot()
    rects = []
    for e in root.iter():
        if etree.QName(e).localname == "rect":
            x, y = float(e.get("x", 0)), float(e.get("y", 0))
            w, h = float(e.get("width", 0)), float(e.get("height", 0))
            if w > 2 and h > 2:
                rects.append((x, y, w, h))
    bad, checked = [], 0
    for e in root.iter():
        if etree.QName(e).localname != "text":
            continue
        s = "".join(e.itertext()).strip()
        if not s:
            continue
        x, y = float(e.get("x", 0)), float(e.get("y", 0))
        style = e.get("style", "")
        m = None
        if style:
            import re
            m = re.search(r"font-size:\s*([\d.]+)px", style)
        size = float(m.group(1)) if m else float(e.get("font-size", 8))
        # anchor: matplotlib writes x at the alignment point; our own SVG writer
        # writes x at the left edge unless text-anchor is middle/end
        anchor = e.get("text-anchor") or (
            "middle" if "text-anchor: middle" in style else
            ("end" if "text-anchor: end" in style else "start"))
        est_w = len(s) * size * 0.55
        x0 = x if anchor == "start" else (x - est_w / 2 if anchor == "middle" else x - est_w)
        cands = [r for r in rects if r[0] - pad <= x0 <= r[0] + r[2] + pad]
        # keep only containers whose vertical span brackets the glyph box
        top = y - CAP * size
        bot = y + DESC * size
        near = [r for r in cands if r[1] - 2 <= y <= r[1] + r[3] + 3]
        if not near:
            continue
        box = min(near, key=lambda r: r[2] * r[3])       # smallest enclosing container
        checked += 1
        dx, dy, dw, dh = box
        over_top = (dy + pad) - top
        over_bot = bot - (dy + dh - pad)
        # a real violation = glyph actually outside the container edge (pad is only
        # breathing room, so subtract it before comparing)
        real_top = (dy - top)
        real_bot = bot - (dy + dh)
        if real_top > 0.01 or real_bot > 0.01:
            bad.append({"text": s[:46], "size": round(size, 1), "y": round(y, 1),
                        "box": [round(dy, 1), round(dy + dh, 1)],
                        "glyph": [round(top, 1), round(bot, 1)],
                        "over_top": round(real_top, 2), "over_bottom": round(real_bot, 2)})
    return {"svg": str(svg), "checked": checked, "violations": len(bad), "items": bad}


out = {k: audit(v) for k, v in TARGETS.items()}
print(json.dumps(out, ensure_ascii=False, indent=1))
(ROOT / "QA_reports" / "_containment_audit.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print("\nFig1 violations:", out["Fig1"]["violations"], " Fig2:", out["Fig2"]["violations"])
