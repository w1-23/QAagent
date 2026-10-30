# -*- coding: utf-8 -*-
"""Sync the human-readable reports with the Fig.1 colour revision (2026-10-15)."""
from pathlib import Path

F = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")

# ---- QA_reports/QA_fig1_fig2.md ----
p = F / "QA_reports" / "QA_fig1_fig2.md"
t = p.read_text(encoding="utf-8")
t = t.replace("## Fig.1 — 系统设计总览（174.0 × 118.0 mm）",
              "## Fig.1 — 系统设计总览（174.0 × 135.0 mm；2026-10-15 配色修订）")
t = t.replace("| 10 | HA 适用格式 | PASS — 宽度恰 174.0mm（双栏），高 118 ≤ 234mm，面板标记小写 (a)(b)(c)，**图内无 Fig. 题名** |",
              "| 10 | HA 适用格式 | PASS — 宽度恰 174.0mm（双栏），高 135 ≤ 234mm，面板标记小写 (a)(b)(c)，**图内无 Fig. 题名** |")
t = t.replace("""**两张已知旗标（不阻塞，需 PI 裁定后定稿）**：""",
              """### 2026-10-15 配色修订（PI 标准配色表）

- **标题条改为实心主色 + 白字**：User question `#287D99`（Supervisor）、Local RAG `#4F8868`、
  Web agent `#C87558`、Assembly/audit `#BE9346`、Two sections `#7772A8`；副题行用对应**浅色**
  （`#E4F2F8`/`#E7F1E5`/`#FBEAE1`/`#FAF0D8`/`#F0EDFA`）。
- **大面积模块保持浅填充**，主色只用于标题条、重点边框与必要连线——即 PI 建议的用色规则。
- 其余色板项（文字 `#26343B`/`#52616A`、边框 `#D4DCE0`、背景 `#FFFFFF`）与修订前一致；
  机检 `6_colors` 仍为 **无杂色**（未引入任何色板外颜色）。

### 同轮修掉的 3 处遗留版式缺陷（视觉目检发现）

| 缺陷 | 证据 | 修法 |
|---|---|---|
| 第 2/3 列底部卡片（Generation/Tiering，底边 y≈238）被徽章条（y=222 起）压住 | 渲染目检 + 坐标核算 | 徽章条 222→250、面板行 246→292，画布高 118→135mm；**列内卡片几何与文字完全不动** |
| 徽章盒高 20，第二行副题基线在 +24.1 → 落在盒外 | 坐标核算 | 徽章盒高 20→26 |
| 面板 (b) 芯片副标签越出芯片边框 0.9 单位 | 坐标核算 | 芯片高 30→34，脚注 +64→+70 |

新增机检脚本 `_containment_audit.py`（检查每条文字字形盒是否在其容器盒内）：
**Fig.1 = 0 违规**（Fig.2 尚有 1 条 0.5 单位≈0.18mm 的旧瑕疵，见下）。

**两张仍需 PI 裁定的旗标（不阻塞）**：""")
p.write_text(t, encoding="utf-8")
print("updated", p.relative_to(F))

# ---- FIGURE_DELIVERY_REPORT.md ----
p = F / "FIGURE_DELIVERY_REPORT.md"
t = p.read_text(encoding="utf-8")
t = t.replace("| Fig.1 | 系统设计与工作流（五列流水线 + 知识库概览 + 评测概览） | 174.0 × 118.0 |",
              "| Fig.1 | 系统设计与工作流（五列流水线 + 知识库概览 + 评测概览） | 174.0 × 135.0 |")
t = t.replace("`Fig1/fig1_system.svg`（123 文本，矢量） | PPTX 孪生 · PDF（矢量，EPS 等效）· PNG 220 dpi | 12/12 |",
              "`Fig1/fig1_system.svg`（123 文本，矢量） | PPTX 孪生 · PDF（矢量，EPS 等效）· `fig1_system.png`（300 dpi 成品图） | 12/12 |")
t = t.replace("""## 三、按源码定案的实现一致性（本轮新增）""",
              """## 二·补、Fig.1 配色规则（2026-10-15 PI 标准色表）

大面积模块用浅填充；**主色只用于标题条、重点边框与必要连线**：
标题条实心主色（Supervisor `#287D99` / Local RAG `#4F8868` / Web `#C87558` /
Audit `#BE9346` / Metadata `#7772A8`）+ 白字 + 浅色副题行。
同轮修掉 3 处遗留叠压（列 2/3 底卡被徽章条压住、徽章副题溢出、芯片副标签溢出），
新增 `QA_reports/_containment_audit.py` 做"文字是否全在容器内"的机检（Fig.1 = 0 违规）。

## 三、按源码定案的实现一致性（本轮新增）""")
p.write_text(t, encoding="utf-8")
print("updated", p.relative_to(F))

# ---- MIGRATION_HANDOVER ----
p = F / "MIGRATION_HANDOVER_会话迁移交接.md"
t = p.read_text(encoding="utf-8")
t = t.replace("| **Fig.1** | 系统设计与工作流（5 列流水线 + 知识库概览 + 评测概览） | 174.0 × 118.0 mm |",
              "| **Fig.1** | 系统设计与工作流（5 列流水线 + 知识库概览 + 评测概览） | 174.0 × 135.0 mm |")
t = t.replace("`_migration_inventory.txt`、`_migration_inventory.py`（本交接书的清单生成器）",
              "`_migration_inventory.txt`、`_migration_inventory.py`（本交接书的清单生成器）\n`_containment_audit.py` + `_containment_audit.json`（文字字形盒 ⊂ 容器盒 的机检；Fig.1 = 0 违规）")
t = t.replace("""## 八、待 PI 裁定项（重绘前建议先确认）""",
              """## 七·补、Fig.1 最近一次变更（2026-10-15，配色修订轮）

按 PI 标准配色表改 Fig.1：**标题条由浅填充改为实心主色 + 白字**（副题行用对应浅色），
大面积模块仍用浅填充——其余色板项与文字/几何不变。
同轮修掉 3 处遗留叠压缺陷（渲染目检 + 坐标核算发现）：
① 第 2/3 列底卡（Generation/Tiering，底边 y≈238）被徽章条压住 → 徽章条 222→250、
面板行 246→292、**画布高 118→135 mm**（列内卡片与文字零改动）；
② 徽章盒高 20 → 26（第二行副题原本落在盒外）；
③ 面板 (b) 芯片高 30 → 34、脚注 +64 → +70（副标签原本越框 0.9 单位）。

**Fig.2 尚存 1 条同类旧瑕疵**：`unparseable · too short` 文字越出"排除"小框 0.5 单位（≈0.18 mm）；
属另一张图，本次未动，需要修的话把 `Fig2/fig2_builder.py` 里该小框高度 +2 即可。

## 八、待 PI 裁定项（重绘前建议先确认）""")
p.write_text(t, encoding="utf-8")
print("updated", p.relative_to(F))
