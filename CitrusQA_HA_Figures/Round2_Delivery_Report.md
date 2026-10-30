# Round-2 交付报告（Fig.1 + Fig.2 定稿）· 2026-10-14

> 任务书 §12 第二轮：**SVG + PPTX + 预览图 + 绘图代码** 全量交付，§10 十二项机检
> **Fig1 12/12 · Fig2 12/12 PASS**（`QA_reports/qa_fig1_fig2_checks.json`）。
> 本轮已按 PI 四条裁定修正：图标换 Material Symbols 实心、Fig.1 审计列按源码重构、
> Fig.2 解析措辞工具无关、最终图保持可编辑。

## 一、交付清单

```
CitrusQA_HA_Figures/
├─ 01_design_system/
│  ├─ design.py          # 设计系统单一事实源（色板/画布/图标/箭头；ICON_STYLE="ms"）
│  ├─ svg2pptx.py        # SVG→PPTX 可编辑孪生镜像器
│  ├─ icons_ms/ (34)     # Material Symbols 实心 SVG（Apache-2.0）← 本轮采用
│  └─ icons/    (35)     # Lucide 线描 SVG（ISC）备选
├─ Fig1/  fig1_system.{svg,pptx,pdf} + preview/page-0001.png + fig1_builder.py
│         + figure_manifest_fig1.json + caption_draft_fig1.md + pptx_mirror_log.json
├─ Fig2/  fig2_corpus.{svg,pptx,pdf} + preview/page-0001.png + fig2_builder.py + …
├─ QA_reports/  QA_fig1_fig2.md · qa_fig1_fig2_checks.json · qa_fig12.py · design_meta.py
│               · _evidence_audit_gate.txt（源码判读证据）
└─ 00_audit/    （第一轮审计，含参考图判读与图标清单）
```

## 二、数值真实性（全部来源可追溯）

| 数字 | 出处 |
|---|---|
| 4,906 docs · 126,494 chunks · 103,089 / 10,018 / 13,137 / 250 | WxRev §2.2/§3.2（PI 裁定唯一权威） |
| 112,890 / 13,137 / 467（三数据集）；62,803 / 33,790 / 9,801 / 6,496 / 250 / 217（七来源） | v7 §3.2；审计已验证**逐列闭合**（≠参考图错误值 33,708/8,805/6,509） |
| 切块 target ≈ 800 | PI 2026-10-14 裁定 |
| 84 / 144(48×3) / 132 / 48题五处置 / 3.1%→24.0% / 1,649/1,649 · 0越界 / kimi-k2.6·glm-5.3 | judge_scores、web_arms_summary、RUNTIME_TRUTH、WxRev |
| **187,441 / 4,992 / 11 batches** | **全图禁用**（QA 禁词命中 = 0） |

## 三、按源码定案的两处（Fig.1 审计列）

- **引用审计 = Stage A 的内部检查**（`check_citation_ids`，越界即 critical），**不是独立阶段**；
- **不存在 "Re-validate → Stage A" 回环**；实际策略是 `critical → 1 次修复循环；minor → 仅标注`。

图内因此呈现：`Stage A（IDs within pool / caliber / recency）→ Stage B → repair (once, critical only) → Outcome`，
证据见 `QA_reports/_evidence_audit_gate.txt`；`00_audit/module_status.csv` 与
`source_conflicts.csv` 已同步为该结论。

## 四、可编辑性说明（PI 要求"最终给的图是可编辑的"）

| 资产 | 可编辑性 |
|---|---|
| **SVG 母版** | ✅ 全部文字为 `<text>`（可选中改写）、图形为矢量、**图标为内联矢量路径**（可换色/缩放/替换）；可直接在 Illustrator / Inkscape / Figma 编辑 |
| **PPTX 孪生** | ✅ 全部模块为独立形状、文字为文本框、箭头为连接线，可拖拽重排；图标为占位圆（本机无 SVG→PNG 光栅器，故未嵌入位图）——如需更换图标，改 SVG 母版后重跑 `svg2pptx.py` 即可 |
| 导出 | PDF/PNG 由 PPTX 经 LibreOffice 渲染（零缺字体） |

## 五、下一步（第三轮，待确认）

1. **Fig.3**：跨语言检索（96 / 48 双分母逐面板标注）+ 双判官热图（Track A–E × D3–D10，Track D n=80）+ Track A−B 配对差逐维复算；
   "十维合并"按 PI 定义（十维求和平均、含 D1/D2）重新计算并核对正文数值。
2. **Fig.4**：引用可解析（分母=标记出现次数）+ 判官 D2/D3 支撑与事实性 + 联网五条件（去读
   `experiment/09_联网五组对照_web_arms` 源码厘清 arms n=96 与 pairs n=48 的聚合语义后再画）。
3. **Fig.5 不绘制**（PI 裁定）。
4. 投稿格式复核（EPS/1200dpi 等，依 HA 指南）。
