# CitrusQA 科研论文绘图 · 总交付报告（Fig.1–Fig.4）

> 项目目录：`CitrusQA_HA_Figures/`　目标期刊：**Horticulture Advances (HA)**
> 权威链：**WxRev 控制技术叙事 · 项目原始数据控制数值 · 实际实现控制模块与流程 · HA 官方规范控制最终输出**
> 约束：**本任务只绘图，不改论文正文**；**真实使用案例图（Fig.5）按 PI 裁定不绘制**。
> 质量门：任务书 §10 十二项逐图机检，**Fig1 12/12 · Fig2 12/12 · Fig3 12/12 · Fig4 12/12 = 48/48 PASS**。

## 一、四张成品与形态

| 图 | 主题 | 尺寸 (mm) | 主母版 | 其他导出 | 机检 |
|---|---|---|---|---|---|
| Fig.1 | **系统架构（原始版式）**：User input → Supervisor → Local RAG / Assembly & audit / Web Agent → Supervisor final → Section 1 / 2 | 17.0 × 20.4 cm | `Fig1_system_original/out/fig1_system_original.svg` | PPTX 孪生（`…out/fig1_system_original.pptx`）· PDF · PNG 600 dpi · TIFF 600 dpi | 几何一致 + 配色核对（见该目录 README） |
| Fig.2 | 知识库构建（四阶段管道 + 三层组成带） | 174.0 × 112.0 | `Fig2/fig2_corpus.svg` | PPTX 孪生 · PDF（矢量）· PNG 220 dpi | 12/12 |
| Fig.3 | 跨语言检索 + 五配置判官热图 + A−B 逐维配对差 + **Mean (D3–D10)** | 174.0 × 142.0 | `Fig3/fig3_retrieval.svg`（9 行森林图：D3–D10 + Mean (D3–D10)） | PDF · **EPS** · PNG 300 dpi · TIFF 600 dpi | 12/12 |
| Fig.4 | 引用可解析性 + 支撑/事实性 + 联网五处置 | 174.0 × 142.0 | `Fig4/fig4_quality.svg`（93 文本） | PDF · **EPS** · PNG 300 dpi · TIFF 600 dpi | 12/12 |

> 注：结构图（Fig.1/2）由自建 SVG 画布绘制，本机无 SVG→EPS 转换器（LibreOffice-Kit 实测
> 不支持 svg→pdf/png/eps），故以**矢量 PDF** 作为 EPS 等效投稿件；统计图（Fig.3/4）由
> matplotlib 直接输出 EPS。

四图的 SVG 均为**全可编辑**：文字是真 `<text>` 节点、图形是矢量路径、**零位图/零 base64**
（统计图的热图与色阶图例也改为逐格矩形矢量，避免 `imshow`/`colorbar` 把渐变光栅化）。

## 二、数值真实性（每个数字都有出处）

| 数值 | 出处 | 用在哪 |
|---|---|---|
| 4,906 文档 / 126,494 切块 | WxRev §2.2/§3.2（PI 裁定唯一权威） | Fig1、Fig2 |
| 103,089 / 10,018 / 13,137 / 250（四类别） | WxRev §3.2 | Fig1、Fig2 |
| 112,890 / 13,137 / 467（三数据集）；62,803 / 33,790 / 9,801 / 6,496 / 250 / 217（七来源） | v7 §3.2（审计已验证逐列闭合） | Fig2 |
| 切块 target ≈ 800（正文另有中位 941） | PI 2026-10-14 裁定 | Fig2 |
| 84 题系统层 / 132 真实 / 48 题联网子集；3.1% → 24.0%；1,649/1,649 切分标记 | judge_scores、web_arms_summary、RUNTIME_TRUTH | Fig1、Fig3、Fig4 |
| 十维合并配对差 +0.6372（glm）/ +0.0173（kimi） | **本项目从逐题评分独立复算，与正文逐位一致** | Fig3 |
| **187,441 / 4,992 / 11 batches** | **弃用错误口径 → 全图禁用**（四图禁词命中 = 0） | — |

跨语言与配置对照是**两套独立实验**（合成 96 题/锚点 48 题 vs 系统层 84 题），
分属 (a) 与 (b)-(e) 面板、分母逐格标注，**从不合并统计**。

## 二·补·0、2026-10-16 第二轮修订（PI 逐条意见）

**Fig.1**：保留原始版式，按 PI 意见改（几何零移动）
① Local RAG / Web Agent → Audit 改为**分支输出端口**引出；② 联网内部加 5 步顺序箭头 +
第 2→3 步标 "if insufficient"（条件触发，`max_rounds=2`），并把 **tier tagging（L1）前置到
cleaning（L2–L3）之前**（依 `web_evidence_pipeline.LAYER_ORDER`）；
③ 新增 **Supervisor → Assembly & audit 中轴控制箭头**，明确"协调两路 → 汇集 → 审计 → 交付"；
④ 标签：`output to [4]`→`to Supervisor final`、`never pooled with local`→`not in the local
citation pool`、early-stop 文案精确化、T3 改 "(labels only)"；⑤ **字体统一 Arial**
（matplotlib 与 PPT 双端；修正 PPT 端英文误用中文字体的问题）。机检 `_layout_check.py` **0 失败**。

**Fig.3e/f**：森林图恢复汇总行，但**按正确口径重算并改名**为 **`Mean (D3–D10)`**：
每题先算 Δᵢ = mean_{d=3..10}(S_A − S_B)，再做配对均值 + 95% CI。
**删除 80 % power 灰带与 MDD 判定**（保留点估计 + 95 % CI + 零线）。
新值 glm **+0.580 [+0.418, +0.743]**、kimi **+0.079 [−0.070, +0.228]**；
论文旧值 (+0.6372 / +0.0173) 实为"各自可用维度求平均"，口径不一致，需在正文同步更正
（方向与结论不变）。见 `statistics/recompute_forest_D3_D10.py` 与 `statistics/fig3_forest_stats.json`。

## 二·补·1、Fig.1 现行版本（2026-10-16 回滚 + 配色）

**PI 明确要求回滚到原始 system 架构图版式**，因此 Fig.1 现由
`Fig1_system_original/`（原始 `flow_layout.LAYOUT1` + PI 配色覆盖层）产出：
只用颜色覆盖、几何零改动 —— `_parity_check.py` 证明两份 SVG 在颜色/id/日期归一后
**逐字节一致（730 vs 730 行，IDENTICAL: True）**。旧的重设计版式 `Fig1/` 已标 `_SUPERSEDED`。
无元数据模块，故 Metadata `#7772A8/#F0EDFA` 在本图未使用。

## 二·补·2、旧 Fig.1（已废弃）的配色规则记录（2026-10-15 PI 标准色表）

大面积模块用浅填充；**主色只用于标题条、重点边框与必要连线**：
标题条实心主色（Supervisor `#287D99` / Local RAG `#4F8868` / Web `#C87558` /
Audit `#BE9346` / Metadata `#7772A8`）+ 白字 + 浅色副题行。
同轮修掉 3 处遗留叠压（列 2/3 底卡被徽章条压住、徽章副题溢出、芯片副标签溢出），
新增 `QA_reports/_containment_audit.py` 做"文字是否全在容器内"的机检（Fig.1 = 0 违规）。

## 三、按源码定案的实现一致性（本轮新增）

1. **引用审计属于 Stage A**（`core/audit.py` 的 `Stage A：代码硬门` 段落内含
   `check_citation_ids`，与统计口径、时效共现并列），**不是独立阶段**；Fig.1 曾按版式参考
   画出独立 "Cite audit" 卡，已删。
2. **不存在 "Re-validate → back to Stage A" 回环**（策略为 `critical → 1 次修复循环；
   minor → 仅标注`，返修由 `_repair_writeback_reject` 做写回护栏）；Fig.1 中该箭头已删，
   改为 `Stage A → Stage B → repair (once) → Outcome`。
3. **D1/D2 只在检索发生的配置有定义**（Track A：kimi 68/84、glm 78/84；B–E 全空），
   故 Fig.3 热图用 D3–D10、Fig.4(b1) 只画 Track A。
4. **联网五臂的 n 语义**（`analyze_web_arms.py` / `tables_webarms.py`）：每臂 **48 题配对**；
   **n = 96 = 48 题 × 2 判官**的论断判定数，rate = 逐题均值；MDD 只对 A−B、A−C′ 存档。
5. 解析措辞按 PI 要求工具无关，统一写 **"PDF extraction"**（不点名工具）。

## 四、目录结构

```
CitrusQA_HA_Figures/
├─ 00_audit/            第一轮审计（资产 4,206 行、哈希基线 96 件、冲突 8 条、模块状态 23 条、
│                       图标清单、缺失数据报告、Skill 可用性、参考版式核验）
├─ 01_design_system/    design.py（色板/画布/图标/箭头）· fig_style.py（统计图样式）
│                       · svg2pptx.py（SVG→可编辑 PPTX 镜像）· icons_ms/（Material Symbols 实心，Apache-2.0）
│                       · icons/（Lucide 线描备选，ISC）
├─ Fig1/ Fig2/          结构图：SVG + PPTX + PDF + preview PNG + builder + manifest + caption
├─ Fig3/ Fig4/          统计图：SVG + PDF + PNG + TIFF + builder + manifest + caption + qa_evidence
├─ statistics/          _recompute_fig3.py + fig3_recomputed_stats.json（逐题复算）
└─ QA_reports/          QA_fig1_fig2.md · QA_fig3_fig4.md · qa_fig12.py · qa_fig34.py
                        · *_checks.json · _evidence_audit_gate.txt · 各探针脚本与原始探针输出
```

## 五、如实记录的残留与待 PI 裁定项

1. **MDD 口径差异**：正文十维合并 MDD 用 z = 1.96（0.1596 / 0.1643），本图按 t(.975,83)
   给 0.1621 / 0.1669；结论不变（glm 远超、kimi 远低于阈），差异已入 manifest。
2. **PPTX 孪生里的图标是占位圆**：本机无 SVG→PNG 光栅器（LibreOffice-Kit 不支持 svg→pdf/png，
   已实测），故 SVG 母版承载真矢量图标，PPTX 承载可拖拽版式；改母版后重跑 `svg2pptx.py` 即同步。
3. **两处版式与实现的差异已按源码修正**（见 §三.1、§三.2）；若 PI 希望保留参考图中的画法，
   需在正文中补齐对应机制。
4. **模块名/措辞**以实现审计为准（asyncio 并发、`[Wn]` 独立命名空间、T1/T2/T3 "labels — not a filter"、
   Faithful translation / No rewriting）。
5. Fig.5（真实使用案例图）**未绘制**（PI 裁定）。

## 六、复核入口

- 逐图十二项机检：`python QA_reports/qa_fig12.py`（结构图）· `python QA_reports/qa_fig34.py`（统计图）
- 统计复算：`python statistics/_recompute_fig3.py`
- 源码判读证据：`QA_reports/_evidence_audit_gate.txt`
- 重绘：`Fig*/fig*_builder.py`（结构图用内置 python，统计图用 `E:\anaconda\envs\rag-agent\python.exe`）
