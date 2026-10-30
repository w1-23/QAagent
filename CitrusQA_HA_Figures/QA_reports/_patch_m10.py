# -*- coding: utf-8 -*-
"""Patch missing_data_report M-10 (extraction tool) per PI ruling."""
import io
from pathlib import Path

p = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\00_audit\missing_data_report.md")
t = p.read_text(encoding="utf-8")
old = "| M-10 | MinerU 是否用于语料解析的**权威确认** | Fig.2(b) | 冲突 C2：源码用 PyMuPDF；若语料由仓库外工具预处理需补证据 |"
new = ("| M-10 | ~~解析工具确认~~ **已裁定（2026-10-14）**：不点名具体工具；"
       "正式图与图注统一写通用 \u201cPDF 提取\u201d | Fig.2(b) | 冲突 C2 已 RESOLVED |")
if old in t:
    t = t.replace(old, new)
    print("M-10 row patched")
else:
    print("M-10 row pattern not found (already updated?)")
t = t.replace("| M-10 | MinerU", "| M-10 | (旧) MinerU")
p.write_text(t, encoding="utf-8")
print("written")
