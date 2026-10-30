# -*- coding: utf-8 -*-
"""Prove that the palette overlay changed COLOURS ONLY.

Normalises every colour literal in both SVGs and diffs them: if the original and
the new render are geometrically/textually identical, the diff is empty.
"""
import re, difflib
from pathlib import Path

R = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent")
ORIG = R / "paper/系统篇_v7/figures_main/fig1_system/flow_system_master.svg"
NEW = R / "CitrusQA_HA_Figures/Fig1_system_original/out/fig1_system_original.svg"
OUT = R / "CitrusQA_HA_Figures/Fig1_system_original/_parity_check.txt"

HEX = re.compile(r"#[0-9A-Fa-f]{3,6}")
RGB = re.compile(r"rgb\(\s*[\d.]+\s*,\s*[\d.]+\s*,\s*[\d.]+\s*\)")


def norm(text):
    t = RGB.sub("#C", text)
    t = HEX.sub("#C", t)
    # matplotlib writes a fresh timestamp and random clip-path ids on every run
    t = re.sub(r"<dc:date>[^<]*</dc:date>", "<dc:date/>", t)
    t = re.sub(r'\bid="[^"]*"', 'id="X"', t)
    t = re.sub(r'url\(#[^)]*\)', "url(#X)", t)
    t = re.sub(r'xlink:href="#[^"]*"', 'xlink:href="#X"', t)
    return t


a = norm(ORIG.read_text(encoding="utf-8"))
b = norm(NEW.read_text(encoding="utf-8"))
la, lb = a.splitlines(), b.splitlines()

lines = [f"original: {ORIG}", f"new     : {NEW}",
         f"lines   : {len(la)} vs {len(lb)}", ""]
same = a == b
lines.append(f"COLOUR-NORMALISED IDENTICAL: {same}")
if not same:
    diff = [d for d in difflib.unified_diff(la, lb, "original", "new", n=0, lineterm="")]
    lines += ["", "first 40 diff lines:"] + diff[:40]
    # also compare the raw geometry tokens (coordinates) count
    def coords(t):
        return re.findall(r"(?:M|L|C|Q)\s*[-0-9.]+[ ,][-0-9.]+", t)
    ca, cb = coords(a), coords(b)
    lines.append(f"geometry tokens: {len(ca)} vs {len(cb)} "
                 f"({'same count' if len(ca) == len(cb) else 'DIFFERENT'})")

# report which colours each file actually uses
origc = sorted(set(m.upper() for m in HEX.findall(ORIG.read_text(encoding='utf-8'))))
newc = sorted(set(m.upper() for m in HEX.findall(NEW.read_text(encoding='utf-8'))))
lines += ["", f"original colours ({len(origc)}): {origc}",
          "", f"new colours ({len(newc)}): {newc}"]

OUT.write_text("\n".join(lines), encoding="utf-8")
print("\n".join(lines))
