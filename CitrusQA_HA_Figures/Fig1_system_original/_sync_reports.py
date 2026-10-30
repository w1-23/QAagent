# -*- coding: utf-8 -*-
"""Mark the redesigned Fig1/ as superseded and point the reports at the original layout."""
from pathlib import Path

F = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")

# 1) marker in the superseded folder
(F / "Fig1" / "_SUPERSEDED_改用原始版式.md").write_text(
    "# 本目录已废弃（2026-10-16）\n\n"
    "PI 要求回滚到**原始 system 架构图版式**（我从零重设计的版式布局不满意）。\n\n"
    "**现行 Fig.1 = `CitrusQA_HA_Figures/Fig1_system_original/`**\n\n"
    "- 绘图：`Fig1_system_original/flow_system_master.py`（原始 matplotlib 入口，几何未改）\n"
    "- 版式源：`Fig1_system_original/flow_layout.py`（原始 `LAYOUT1` + PI 配色覆盖层）\n"
    "- 可编辑 PPT：`Fig1_system_original/out/fig1_system_original.pptx`\n"
    "- 最终图：`Fig1_system_original/out/fig1_system_original.png`（600 dpi）\n\n"
    "本目录（`Fig1/`）保留仅供比对：它是同内容但**版式不同**的重设计版本，**不要再作为交付件**。\n"
    "`QA_reports/qa_fig12.py` 里的 Fig1 条目对应这一旧版式，仅供历史质检引用。\n",
    encoding="utf-8")
print("marker written:", (F / "Fig1" / "_SUPERSEDED_改用原始版式.md").relative_to(F))

# 2) delivery report: replace the Fig.1 row + add a rollback note
p = F / "FIGURE_DELIVERY_REPORT.md"
t = p.read_text(encoding="utf-8")
old_row = "| Fig.1 | 系统设计与工作流（五列流水线 + 知识库概览 + 评测概览） | 174.0 × 135.0 | `Fig1/fig1_system.svg`（123 文本，矢量） | PPTX 孪生 · PDF（矢量，EPS 等效）· `fig1_system.png`（300 dpi 成品图） | 12/12 |"
new_row = ("| Fig.1 | **系统架构（原始版式）**：User input → Supervisor → Local RAG / Assembly & audit / Web Agent → Supervisor final → Section 1 / 2 | 17.0 × 20.4 cm | "
           "`Fig1_system_original/out/fig1_system_original.svg` | PPTX 孪生（`…out/fig1_system_original.pptx`）· PDF · PNG 600 dpi · TIFF 600 dpi | 几何一致 + 配色核对（见该目录 README） |")
if old_row in t:
    t = t.replace(old_row, new_row)
else:
    t = t.replace("| Fig.1 |", "| Fig.1（旧·已废弃） |", 1)
t = t.replace("## 二·补、Fig.1 配色规则（2026-10-15 PI 标准色表）",
              "## 二·补·1、Fig.1 现行版本（2026-10-16 回滚 + 配色）\n\n"
              "**PI 明确要求回滚到原始 system 架构图版式**，因此 Fig.1 现由\n"
              "`Fig1_system_original/`（原始 `flow_layout.LAYOUT1` + PI 配色覆盖层）产出：\n"
              "只用颜色覆盖、几何零改动 —— `_parity_check.py` 证明两份 SVG 在颜色/id/日期归一后\n"
              "**逐字节一致（730 vs 730 行，IDENTICAL: True）**。旧的重设计版式 `Fig1/` 已标 `_SUPERSEDED`。\n"
              "无元数据模块，故 Metadata `#7772A8/#F0EDFA` 在本图未使用。\n\n"
              "## 二·补·2、旧 Fig.1（已废弃）的配色规则记录（2026-10-15 PI 标准色表）")
p.write_text(t, encoding="utf-8")
print("updated", p.relative_to(F))

# 3) handover: point Fig.1 at the original layout
p = F / "MIGRATION_HANDOVER_会话迁移交接.md"
t = p.read_text(encoding="utf-8")
t = t.replace("| **Fig.1** | 系统设计与工作流（5 列流水线 + 知识库概览 + 评测概览） | 174.0 × 135.0 mm |",
              "| **Fig.1** | **系统架构（原始版式，2026-10-16 回滚）**：User input → Supervisor → Local RAG / Assembly & audit / Web Agent → Supervisor final → Section 1 / 2 | 17.0 × 20.4 cm |")
t = t.replace("## 七·补、Fig.1 最近一次变更（2026-10-15，配色修订轮）",
              "## 七·补·0、Fig.1 现行版本 = 原始版式（2026-10-16）\n\n"
              "**目录：`CitrusQA_HA_Figures/Fig1_system_original/`**（旧的重设计版式 `Fig1/` 已标 `_SUPERSEDED`）。\n\n"
              "```powershell\n"
              "$PA='E:\\anaconda\\envs\\rag-agent\\python.exe'; $PB='<内置 py3.12>'\n"
              "cd Fig1_system_original\n"
              "& $PA flow_system_master.py   # → out/fig1_system_original.{svg,pdf,png(600dpi),tif}\n"
              "& $PB ppt_twin.py             # → out/fig1_system_original.pptx（可编辑，单页）\n"
              "& $PB _parity_check.py        # 几何一致性复核（应输出 IDENTICAL: True）\n"
              "```\n\n"
              "基础脚本（**只读、未修改**）：`paper/系统篇_v7/figures_main/fig1_system/{flow_layout.py,\n"
              "flow_system_master.py, mpl_style.py}` + `experiment/2026927-python绘图latest/00_shared/{ppt_style.py,\n"
              "ppt_twin.py}`。配色通过 `flow_layout.py` 末尾的 `_apply_pi_palette(LAYOUT1)` 覆盖层施加，\n"
              "**只改颜色字段，坐标/文字/连线零改动**；两份 SVG 颜色归一后逐字节一致。\n\n"
              "已知项：PPT 孪生 `Corpus layer` 卡正文在 PPT 里比图多折一行（原始工具的字体度量差异\n"
              "+ 10-14 版更长文案所致，与配色无关），详见该目录 README §六。\n\n"
              "## 七·补、旧 Fig.1（重设计版式，已废弃）的配色修订记录（2026-10-15）")
p.write_text(t, encoding="utf-8")
print("updated", p.relative_to(F))
