# -*- coding: utf-8 -*-
"""Second-pass patch: remove the last non-palette colour literals that reach the
rendered figure (diamond label ink + default-edge fallbacks)."""
from pathlib import Path

D = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\Fig1_system_original")
edits = {
    "mpl_style.py": [('color="#1F1F1F", fontweight="bold", zorder=zorder + 1)',
                      'color="#26343B", fontweight="bold", zorder=zorder + 1)'),
                     ('fc="#F0F0F0", tc="#666666"', 'fc="#FFFFFF", tc="#52616A"')],
    "flow_system_master.py": [('e = ec if ec else "#333333"', 'e = ec if ec else "#52616A"')],
    "ppt_twin.py": [('ps.rgb(ec if ec else "#333333")', 'ps.rgb(ec if ec else "#52616A")')],
}
for name, subs in edits.items():
    p = D / name
    t = p.read_text(encoding="utf-8")
    for a, b in subs:
        if a in t:
            t = t.replace(a, b)
            print(f"{name}: patched {a[:46]!r}")
        else:
            print(f"{name}: NOT FOUND {a[:46]!r}")
    p.write_text(t, encoding="utf-8")
