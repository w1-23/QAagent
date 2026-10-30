# -*- coding: utf-8 -*-
"""Quick SVG facts: canvas size in mm, raster count, text-node count."""
import re
from pathlib import Path

ROOT = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
for f in (ROOT / "Fig3/fig3_retrieval.svg", ROOT / "Fig4/fig4_quality.svg"):
    if not f.exists():
        print(f.name, "MISSING")
        continue
    t = f.read_text(encoding="utf-8")
    w = re.search(r'width="([\d.]+)pt"', t)
    h = re.search(r'height="([\d.]+)pt"', t)
    print(f"{f.name}: w={float(w.group(1))*25.4/72:.1f}mm h={float(h.group(1))*25.4/72:.1f}mm "
          f"images={len(re.findall('<image', t))} texts={len(re.findall('<text', t))}")
