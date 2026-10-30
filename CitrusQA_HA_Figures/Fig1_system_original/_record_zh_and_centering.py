# -*- coding: utf-8 -*-
"""Keep the README change table truthful after the ZH + centring revision."""
from pathlib import Path

p = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures"
         r"\Fig1_system_original\README.md")
t = p.read_text(encoding="utf-8")

old_row6_start = "| 6 | **`User input (CN)` 用词**"
lines = t.splitlines()
out, replaced = [], False
for ln in lines:
    if ln.startswith(old_row6_start):
        out.append("| 6 | **`User input (CN)` 用词**：CN 是 ISO-3166 国家代码，不是语言代码"
                   "（中文的 ISO 639-1 是 `zh`） | 表头改为 **`User input (ZH)`**（PI 指定用语言码）；"
                   "正文 `no [Wn] / URL; CN or EN` 同步改为 **`... ; ZH or EN`**（2 字符等长、零重排）。"
                   "全图已无 `CN` |")
        out.append("| 7 | **位置注记未与一级标题对齐**：\"Assembly: renumber, drop dead markers\" "
                   "原为左对齐（`T(A3_X+0.25, …, A3_W-0.5, align=left)`），与其上居中的 "
                   "\"Assembly & audit\" 不齐 | 改为 **同一锚点居中**（`x=A3_X, w=A3_W, align=center` → "
                   "锚点 8.5 cm，SVG 实测与标题同为 `x=251.74pt` + `text-anchor: middle`），"
                   "**纵向参数（y、h）一字未动** |")
        replaced = True
    else:
        out.append(ln)
p.write_text("\n".join(out) + "\n", encoding="utf-8")
print("README rows updated:", replaced)
