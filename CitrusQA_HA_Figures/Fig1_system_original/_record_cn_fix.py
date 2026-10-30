# -*- coding: utf-8 -*-
"""Record the PI review item (CN → Chinese / ZH) in the Fig.1 README change table."""
from pathlib import Path

p = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures"
         r"\Fig1_system_original\README.md")
t = p.read_text(encoding="utf-8")
anchor = "| 5 | 字体统一 Arial"
row = ("| 6 | **`User input (CN)` 用词**：CN 是 ISO-3166 国家代码，不是语言代码"
       "（中文的 ISO 639-1 是 `zh`） | 表头改为 **`User input (Chinese)`**；"
       "正文 `no [Wn] / URL; CN or EN` 同步改为 **`... ; ZH or EN`**"
       "（2 字符等长、零重排）。全图已无 `CN`。若更想统一为代码风格，"
       "表头可一键改为 `User input (ZH)` |")
assert anchor in t, "anchor row not found"
lines = t.splitlines()
out = []
for ln in lines:
    out.append(ln)
    if anchor in ln and row not in t:
        out.append(row)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("added:", row[:70], "...")
