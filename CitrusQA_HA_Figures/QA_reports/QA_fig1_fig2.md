# QA 报告 · Fig.1 & Fig.2（Round 2 定稿）· 2026-10-14

> 任务书 §10 十二项检查逐图机检（`qa_fig12.py` → `qa_fig1_fig2_checks.json`）。
> 本轮变更：图标切换为 **Material Symbols（实心）**；Fig.1 审计列**按源码重构**；
> Fig.2 阶段 2 改为工具无关表述。**结果：Fig1 12/12 PASS · Fig2 12/12 PASS。**

## Fig.1 — 系统设计总览（174.0 × 135.0 mm；2026-10-15 配色修订）

| # | 检查 | 结果 |
|---|---|---|
| 1 | SVG 有效性 | PASS（lxml 解析，UTF-8 无损） |
| 2 | SVG 可编辑性 | PASS — 文本 123 个 `<text>`、矩形 33、矢量路径 44（含 25 枚 Material Symbols 实心图标内联）；零位图/base64 |
| 3 | 字体 | PASS — 全图 `Arial, Helvetica, sans-serif` |
| 4 | 文本溢出 | PASS — 56 项逐框检查，**0 溢出** |
| 5 | 连线方向 | PASS — 10 条箭头全带箭头端、方向单调 |
| 6 | 颜色一致性 | PASS — 仅用 §3.1 固定色板 ∪ 白底 |
| 7 | 数值/类别 | PASS — 19 项必含全在图；禁词（187,441 / 4,992 / MinerU / GPT-4 / Claude）零命中 |
| 8 | 统计检验一致性 | PASS — 结构图无 p 值/CI/显著性标记 |
| 9 | 尺寸可读性 | PASS — 6.0–9.0pt 阶梯，无 <6pt |
| 10 | HA 格式 | PASS — 174.0mm 双栏宽、高 118mm、面板小写 (a)(b)(c)、图内无题名 |
| 11 | 交叉渲染 | PASS — PDF 260,690 B + PNG（1508×1023 @220dpi），零缺字体 |
| 12 | 源码一致性 | PASS — `build()` 两次重建逐字节一致 |

### 本轮按源码修正的 Fig.1 审计列（重要）
读码结论（`01_源码_src/src/core/audit.py`、`graph/expert_graph.py`，证据存档
`QA_reports/_evidence_audit_gate.txt`）：
1. **"Citation audit" 不是独立阶段** —— `audit.py` 把它明确写在
   `# ===== Stage A：代码硬门 =====` 之下（`check_citation_ids`：越界即 critical），
   与统计口径（`check_caliber`）、时效共现（`check_recency`）并列为 Stage A 三项检查。
2. **"Re-validate → back to Stage A" 回环不存在** —— 策略行写的是
   `critical -> 1 次修复循环；minor -> 标注 + ledger，不重生成`；返修由
   `_repair_writeback_reject()` 做写回护栏；全库检索 re-valid/回环 无命中。
→ 图中改为：**Stage A（IDs within pool / caliber / recency）→ Stage B → repair (once, critical only) → Outcome（pass / partial, refs all in pool）**，
删除了参考图中的独立 "Citation audit" 卡与 "Re-validate" 箭头。

## Fig.2 — 知识库构建（174.0 × 112.0 mm）

同为 12/12 PASS（文本 59、矩形 20、矢量路径 22、溢出 0/24、渲染 PDF 249,872 B + PNG）。
差异点：
- **数值**：16 项必含全在图（4,906 / 126,494 / 112,890 / 13,137 / 467 / 62,803 / 33,790 / 9,801 / 6,496 / 250 / 217 / 103,089 / 10,018 / 800 / SHA-256 / e5-large / LanceDB / BM25Plus）；三层逐列闭合。
- **阶段 2 措辞**：按 PI 裁定改为工具无关的 **"PDF extraction (text)"**（不点名具体解析工具）。
- 467 与 217 两条 <0.2% 色带按最小可见宽绘制，脚注已声明。

## 图标体系（本轮切换）

| 项 | 值 |
|---|---|
| 库 | `@material-symbols/svg-400`（**filled** 变体），**Apache-2.0** |
| 本地素材 | `01_design_system/icons_ms/`（34 枚，已核验下载） |
| 保留备选 | `01_design_system/icons/`（Lucide 线描，ISC，35 枚） |
| 设计系统开关 | `design.py: ICON_STYLE = "ms"`（切回线描只需改为 `"lucide"` 并保留同名文件） |
| 可编辑性 | 图标以 `<g>`+`<path>` **内联矢量**写入 SVG，可着色可缩放；PPTX 孪生中为占位圆（版式可拖拽），矢量真值在 SVG 母版 |

## 交付物（每图 8 件）

`fig*_system.svg`（可编辑母版）· `fig*.pptx`（可编辑孪生）· `fig*.pdf` ·
`preview/page-0001.png` · `*_builder.py`（绘图源码）· `figure_manifest_*.json`（含 fit 明细）·
`caption_draft_*.md` · `pptx_mirror_log.json`。

**已知限制（如实记录）**：① PPTX 孪生里图标为占位圆（无 SVG→PNG 光栅化器可用；
矢量真值在 SVG 母版）；② 6.0–6.2pt 字样仅用于徽章副题与脚注；③ 未做视觉目检
（本环境读图受限），以结构/字段/数值机检为准。
