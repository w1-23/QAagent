# -*- coding: utf-8 -*-
"""Finalise the Fig.1 colour revision: standalone raster deliverable + doc updates."""
import io, json, shutil
from pathlib import Path

F = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")
FIG1 = F / "Fig1"

# 1) standalone final raster (300 dpi) next to the other formats
src = FIG1 / "preview" / "page-0001.png"
dst = FIG1 / "fig1_system.png"
shutil.copy2(src, dst)
print("final raster:", dst.name, f"{dst.stat().st_size:,} B")

# 2) manifest: dimensions + colour rule + corrections applied
mp = FIG1 / "figure_manifest_fig1.json"
m = json.loads(mp.read_text(encoding="utf-8"))
m["output_dimensions"] = "174.0 × 135.0 mm"
m["colour_rule"] = ("PI palette: large modules use the light tint (#E7F1E5 / #FBEAE1 / #E4F2F8 / "
                    "#FAF0D8 / #F0EDFA); the primary colour (#4F8868 local / #C87558 web / #287D99 "
                    "supervisor / #BE9346 audit / #7772A8 metadata) is reserved for title bars, key "
                    "borders and necessary connectors. Text #26343B / #52616A, border #D4DCE0, "
                    "background #FFFFFF.")
m.setdefault("revision_notes", []).append(
    "2026-10-15 colour revision (PI palette): column title bars changed from light fill to SOLID "
    "primary with white text and light-tint sub-line; all other colours already conformed. "
    "Three pre-existing layout defects surfaced during visual review were corrected: (i) column 2/3 "
    "bottom cards (Generation/Tiering, bottom y≈238) were overprinted by the badge strip — badge row "
    "moved 222 → 250 and panels (b)/(c) 246 → 292 by growing the canvas 118 → 135 mm (column "
    "geometry, text and card sizes unchanged); (ii) badge boxes were 20 units tall while their second "
    "sub-line sat at +24.1 — box height 20 → 26; (iii) chip sub-labels in panel (b) crossed their chip "
    "border by 0.9 units — chip height 30 → 34, panel note moved +64 → +70.")
m["qa_evidence"]["containment_audit"] = {"script": "QA_reports/_containment_audit.py",
                                         "fig1_violations": 0,
                                         "note": "every text glyph box lies inside its container box"}
mp.write_text(json.dumps(m, ensure_ascii=False, indent=1), encoding="utf-8")
print("manifest updated:", mp.name)
