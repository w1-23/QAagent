# CitrusQA 论文绘图 · 会话迁移交接书（MIGRATION HANDOVER）

> 生成于本次会话结束前。这份文档自包含：**目标 → 资产 → 数据 → 脚本 → 重绘流程 → 质量门 → 坑**，
> 换会话/换机器照此即可复原全部四张图。
> 项目根：`E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\`
> 逐文件清单（含字节数、存在性核验）：`QA_reports/_migration_inventory.txt`

---

## 一、目标

### 1.1 最初目标（原始题干）

**CitrusQA 科研论文绘图总任务（仅绘图，不改论文）**，目标期刊 **Horticulture Advances (HA)**。要点：

1. **四条权威链**（任何冲突以此裁定）：
   - **WxRev 控制技术叙事**
   - **项目原始数据控制数值**
   - **实际实现控制模块与流程**（以 `01_源码_src/src` 为准）
   - **HA 官方规范控制最终输出**
2. **未经 PI 明确批准，不得修改论文正文**（摘要/引言/方法/结果/讨论/结论/参考文献）——本项目只出图。
3. **先审计、后绘图**：第一轮只做资产/数据/Skill 审计并报告，不得直接重写论文或一次性生成全部正式图。
4. **每幅正式图必须过 §10 十二项质量验证**：① SVG 有效性 ② SVG 可编辑性 ③ 字体 ④ 文本边界与溢出
   ⑤ 连线方向 ⑥ 颜色一致性 ⑦ 数值与类别一致性 ⑧ 统计检验一致性 ⑨ 尺寸可读性 ⑩ HA 适用格式
   ⑪ PDF/EPS/PNG 交叉渲染 ⑫ 与绘图源代码的一致性。
5. **真实使用案例图（Fig.5）不绘制。**
6. 交付形态：**SVG + PPTX + 预览图 + 绘图代码**，且**最终给的图必须是可编辑的**。
7. 用户提供的两张版式参考图**只作绘图效果/图标素材**参考——"数据以我们真实为准"。

### 1.2 后续改进/更新的绘图目标（逐轮裁定，按时间顺序）

| # | 裁定/新增目标 | 落实位置 |
|---|---|---|
| 1 | 第一轮计划六点修订：Fig.3 必须逐题审计（question_id/track/judge/dimension/score/validity/version/配对/能否重算 CI）；**84 题系统层 ≠ 跨语言实验，48/96 分母不得混用**；Fig.4 指标语义（可解析 ≠ 支撑 ≠ 事实性，须给分母/聚合/n）；Fig.1 六态模块状态；新增 `source_conflicts.csv`（不静默弃用版本）；Skill 审计三态且**优先 PptxGenJS/无安装**；源文件只读、仅写 `00_audit/`、记录哈希 | `00_audit/*` |
| 2 | **真实切块参数 = 800** | Fig.2 |
| 3 | **类别分项**：冻结语料 **4,906 文档 / 126,494 块** = 研究文献 **103,089** + 品种记录 **10,018** + 翻译语料 **13,137** + 实验方法与协议 **250** | Fig.1(b)、Fig.2(b) |
| 4 | **十维合并定义 = 把 10 个维度的分数加一起平均**（含 D1/D2；端到端无空值） | Fig.3(d)(e) + `statistics/` |
| 5 | **n=96/48 聚合语义、数值硬门开关状态去查代码**（"不重要，可先画成可编辑内容"） | Fig.3(a)、Fig.4(c) |
| 6 | **MinerU 不重要、不要写具体工具**，模糊成 "PDF 提取" | Fig.2 阶段 2 + 图注 |
| 7 | **Fig.1 两问必须查源码定案**："Re-validate→Stage A" 回环是否存在？"Citation audit" 是独立阶段还是 Stage A 子项？ | Fig.1 第 4 列 + `QA_reports/_evidence_audit_gate.txt` |
| 8 | **图标换 Material Symbols（实心，更贴参考图）** | `01_design_system/icons_ms/` |
| 9 | **联网 5 条件去读实验源码** | `Fig4/fig4_builder.py` 面板 (c) |
| 10 | **48/96 两套分母逐面板标注、绝不合并**（跨语言合成集 vs 84 题系统层是两套独立实验） | Fig.3(a1)(a2) |
| 11 | 最终图**可编辑** | 全图 SVG 文字为 `<text>`、零位图；Fig.1/2 另有 PPTX 孪生 |
| 12 | Fig.5 **不绘制** | — |

### 1.3 已达成状态

四张图（Fig.1–Fig.4）全部产出，**§10 十二项机检 48/48 PASS**（Fig1 12/12、Fig2 12/12、Fig3 12/12、Fig4 12/12）。

---

## 二、资产台账（`CitrusQA_HA_Figures/`）

### 2.1 成品图

| 图 | 主题 | 尺寸 | 文件（同目录） |
|---|---|---|---|
| **Fig.1** | **系统架构（原始版式，2026-10-16 回滚）**：User input → Supervisor → Local RAG / Assembly & audit / Web Agent → Supervisor final → Section 1 / 2 | 17.0 × 20.4 cm | `Fig1/fig1_system.svg`（**可编辑母版**，123 文本/0 位图）· `fig1_system.pptx`（263 形状：33 自选图形 + 123 文本框 + **97 自由曲线矢量图标** + 10 连接线）· `fig1_system.pdf` · `preview/page-0001.png` · `fig1_builder.py` · `figure_manifest_fig1.json` · `caption_draft_fig1.md` · `pptx_mirror_log.json` |
| **Fig.2** | 知识库构建（4 阶段管道 + 三层组成带） | 174.0 × 112.0 mm | `Fig2/fig2_corpus.svg` · `.pptx`（107 形状 + 14 条矢量流带）· `.pdf` · `preview/page-0001.png` · `fig2_builder.py` · manifest · caption |
| **Fig.3** | 跨语言检索 + 五配置判官热图 + A−B 配对差 | 174.0 × 132.0 mm | `Fig3/fig3_retrieval.svg`（195 文本）· `.pdf` · `.eps` · `.png`(300dpi) · `.tif`(600dpi) · `fig3_builder.py` · manifest · caption · `fig3_qa_evidence.json` |
| **Fig.4** | 引用可解析性 + D2 支撑/D3 事实性 + 联网五处置 | 174.0 × 142.0 mm | `Fig4/fig4_quality.svg`（93 文本）· `.pdf` · `.eps` · `.png` · `.tif` · `fig4_builder.py` · manifest · caption · `fig4_qa_evidence.json` |

### 2.2 设计系统 `01_design_system/`

| 文件 | 作用 |
|---|---|
| `design.py` | 结构图设计系统单一事实源：色板 `C`（rag/web/sup/audit/meta + 文本/边框）、`Canvas`（rrect/text/block/icon/arrow/line/marker/numbers/save）、`fit()` 逐框溢出检查。**`ICON_STYLE = "ms"`** 控制图标风格（`"ms"` 实心 / `"lucide"` 线描） |
| `fig_style.py` | 统计图（matplotlib）样式：Arial、去顶右框、仅 Y 网格、`save()` 输出 **SVG(fonttype=none)/PDF/EPS/PNG300/TIFF600**、矢量 `heatmap()`（逐格矩形，避免位图）、`heat_legend()`、`forest()`、`bar_with_ci()` |
| `svg2pptx.py` | SVG → 可编辑 PPTX 镜像器；含 `path_to_polys()`（把 Material Symbols 路径扁平化为 PPTX 自由曲线，**真矢量图标**） |
| `icons_ms/` | **34 枚 Material Symbols 实心 SVG**（Apache-2.0，来源 `@material-symbols/svg-400`，本地名=语义名） |
| `icons/` | 35 枚 Lucide 线描 SVG（ISC）备选 |

### 2.3 审计档案 `00_audit/`（第一轮产物，只读参考）

`asset_inventory.csv`（4,206 行资产盘点）· `source_hashes_baseline.csv`（**96 个源文件 sha256 基线**）·
`figure_data_map.csv` · `statistical_recomputability.csv`（A/B/C 分级，逐面板）· `source_conflicts.csv`（8 条冲突）·
`module_status.csv`（23 条六态模块）· `icon_manifest.csv` · `missing_data_report.md` ·
`implementation_consistency_report.md` · `skill_availability_report.md` · `reference_layout_verification.md` ·
`Round1_Productivity_Report.md` · `source_assets/`（2 张版式参考 PNG + sha256）· `round1_scripts/`（9 个审计脚本）

### 2.4 质量报告 `QA_reports/`

`qa_fig12.py` + `qa_fig1_fig2_checks.json` + `QA_fig1_fig2.md`（结构图十二项机检）
`qa_fig34.py` + `qa_fig3_fig4_checks.json` + `QA_fig3_fig4.md`（统计图十二项机检）
`design_meta.py`（色板白名单，qa_fig12 依赖）
`_evidence_audit_gate.txt`（Fig.1 两个源码问题的判读证据）
`_editability_check.py` + `_editability_evidence.txt`（可编辑性实证：文本节点数/零位图/PPTX 改字闭环）
`_probe_exp1.txt`、`_probe_web_arms.txt`、`_probe_dimnulls.txt`（数据/源码探针原始输出）
`_migration_inventory.txt`、`_migration_inventory.py`（本交接书的清单生成器）
`_containment_audit.py` + `_containment_audit.json`（文字字形盒 ⊂ 容器盒 的机检；Fig.1 = 0 违规）

### 2.5 统计复算 `statistics/`

`_recompute_fig3.py` → `fig3_recomputed_stats.json`：从**逐题**判官评分重算
每维每配置均分、Track A−B 逐维配对差（delta/sd/n/t/95%CI/MDD）、十维合并配对差、各维非空 n。

### 2.6 总报告

`FIGURE_DELIVERY_REPORT.md`（四图总交付报告：数值出处、源码定案、残留项）·
`Round2_Delivery_Report.md`（第二轮 Fig.1/2 交付记录）

---

## 三、数据位置（画图用的"真值"来源）

### 3.1 主数据（本次绘图实际读取；均在 `E:\codex_WORKSPACES\Citrus_QA_Agent\` 下）

```
07_交付与文档_delivery_docs\03_图件_figures\英文_figures_en\_sources\
    judge_scores_kimi-k2.6.jsonl   244,312 B  判官1 逐题十维评分（84 题 × 5 配置，blind）
    judge_scores_glm-5.3.jsonl     143,504 B  判官2 同上（两判官永不平均）
    exp1_summary.json               51,873 B  跨语言 2×2 合成 96 题 + 金标锚点子集 48 题（Wilson CI、McNemar）
    per_dimension_means.csv          1,040 B  上游算好的每维均分（用于交叉核对）
    mdd.json                        35,468 B  各维 MDD（最小可测差）存档
    agreement.json                   1,802 B  判官一致性
    five_metrics.csv                 1,158 B  五项指标
    web_arms_summary.json            1,197 B  ★联网五臂比率 + 配对 diff/MDD（Fig.4c 的数值来源）
    webpipe_thresholds.json                    联网管道阈值（备用）
experiment\13_评测管道_pipeline\results\retrieval\crosslingual_20260918\
    per_question.jsonl             309,000 B  84 题×5 格逐题原始（含 A_A 英锚格；是"另一套实验"的原始件）
experiment\2026927-python绘图latest\00_shared\
    data_sources.py                 11,763 B  ★常量汇总：CITATION_LEVELS / E2E_POOL_MEANS / AUDIT_GATE /
                                              WEB_SUPPORT / THRESHOLD_FALSEKILL / USAGE / SATISFY …（Fig.4a 数值来源）
                                              ⚠ 其中 CORPUS_* 仍是弃用口径 187,441（冲突 C1，勿用）
experiment\09_联网五组对照_web_arms\01_实验脚本_scripts\
    analyze_web_arms.py              7,201 B  ★五臂定义与统计（A 不联网 / B 未过滤 / C 旧过滤 / C′ 模型可见 / D 摘要）
    tables_webarms.py                          ★"n=96 = 48 题 × 2 判官"的语义注释在此
data_archive\04_web_experiments\threshold\
    support_offline_arms.json        3,830 B  联网论断独立支撑核验（各处置 48 题）
01_源码_src\src\
    core\audit.py                   14,698 B  ★Stage A 硬门三项（check_citation_ids / caliber / recency）
    graph\expert_graph.py          136,368 B  ★_stage_a_audit 组装、_repair_writeback_reject 返修写护栏
```

> 完整存在性核验见 `QA_reports/_migration_inventory.txt` §B（14/14 全部 OK）。

### 3.2 同名副本（可作交叉校验，勿混用）

- `exp1_summary.json` 另存在于：`data_archive\02_experiments\exp1_crosslingual\`、
  `data_archive\02_experiments\exp2_reranking\`、`data_archive\02_experiments\exp3_multigranularity\`、
  `experiment\01_机制实验一_跨语言检索_crosslingual\02_结果数据_results\`、
  `experiment\2026927-python绘图latest\01_…\02_结果数据_results\`（**5 处**）
- `webpipe_thresholds.json` 5 处、`webpipe_falsekill.json` 2 处、`tables_webarms.py` 2 处
- **建议**：绘图脚本一律指向 §3.1 的 `_sources` 与源码路径，避免"同名不同版本"。

### 3.3 数值红线（严禁出现在正式图里）

| 禁止值 | 原因 |
|---|---|
| **187,441 块 / 4,992 篇 / 11 批次** | 未去重错误口径，**DEPRECATED**；权威值 = 4,906 文档 / 126,494 块 |
| 33,708 / 8,805 / 6,509（来源集合） | 参考图里的错误值；正确 = **33,790 / 9,801 / 6,496** |
| 饼图 36/38/22/15/13（合计 124%） | 参考图错误，无文档级类别数据，改用四类别数据 |
| 判官 "GPT-4 family / Claude family" | 错误；正确 = **kimi-k2.6 / glm-5.3** |
| 热图行名 "RAG (general) / w/o delivery check / Web mixed…" | 错误；正确 = **Track A–E**（citrusqa / same_base_trackA/B / mainstream_trackA/B） |

四图的机检里内置了禁词表（187,441 / 4,992 / MinerU / GPT-4 / Claude），命中即 FAIL。

---

## 四、脚本位置与运行方式

### 4.1 运行环境（本机实测可用）

| 环境 | 路径 | 用途 |
|---|---|---|
| 内置 Python **3.12** | `C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe` | 结构图（Fig.1/2）、`svg2pptx.py`、`qa_fig12.py`、复算脚本；**无 matplotlib** |
| Anaconda **rag-agent** Python 3.11.15（matplotlib 3.11.0 / numpy 2.4.6） | `E:\anaconda\envs\rag-agent\python.exe` | 统计图（Fig.3/4）、`qa_fig34.py` |
| Node + LibreOffice-Kit CLI | node：`E:\下载\新建文件夹\resources\runtime\primary-runtime\dependencies\node\bin\node.exe`<br>cli：`E:\下载\新建文件夹\resources\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js` | PPTX → PDF / PNG 渲染 |
| 字体 | `C:\Windows\Fonts\arial.ttf` 存在；**无 Helvetica**（matplotlib 会打印 `findfont: Helvetica not found` 警告，无害） | — |

### 4.2 重绘命令（逐个可复制）

```powershell
$PB='C:\Users\Administrator\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe'   # 内置 py3.12
$PA='E:\anaconda\envs\rag-agent\python.exe'                                                        # 统计图 py3.11
$NODE='E:\下载\新建文件夹\resources\runtime\primary-runtime\dependencies\node\bin\node.exe'
$CLI='E:\下载\新建文件夹\resources\app.asar.unpacked\dsh\node_modules\@deepseek-ai\libreoffice-kit\lib\cli.js'
$F='E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures'

# ① 结构图（SVG 母版 + 图注 + manifest + qa json）
cd $F\Fig1; & $PB fig1_builder.py
cd $F\Fig2; & $PB fig2_builder.py

# ② 结构图的 PPTX 可编辑孪生（含真矢量图标）
cd $F\01_design_system; & $PB svg2pptx.py

# ③ 统计复算（从逐题评分重算，产出 fig3_recomputed_stats.json）——Fig.3/Fig.4 都依赖它
& $PA $F\statistics\_recompute_fig3.py

# ④ 统计图（SVG/PDF/EPS/PNG300/TIFF600）
cd $F\Fig3; & $PA fig3_builder.py
cd $F\Fig4; & $PA fig4_builder.py

# ⑤ 渲染：PPTX → PDF / PNG（结构图）；统计图自带 PDF/EPS/PNG/TIFF，无需渲染
& $NODE $CLI convert --input "$F\Fig1\fig1_system.pptx" --output "$F\Fig1\fig1_system.pdf"
& $NODE $CLI render  --input "$F\Fig1\fig1_system.pptx" --output-dir "$F\Fig1\preview" --dpi 220
& $NODE $CLI convert --input "$F\Fig2\fig2_corpus.pptx" --output "$F\Fig2\fig2_corpus.pdf"
& $NODE $CLI render  --input "$F\Fig2\fig2_corpus.pptx" --output-dir "$F\Fig2\preview" --dpi 220
#   ⚠ render 的 --output-dir 必须是"不存在或空"的目录，否则可能静默失败（先删旧目录）

# ⑥ 质量门（十二项）
$env:PYTHONPATH="$F;$F\01_design_system;$F\Fig1;$F\Fig2;$F\QA_reports"
cd $F\QA_reports; & $PB qa_fig12.py      # 结构图 → qa_fig1_fig2_checks.json，期望 TOTAL FAIL: 0
cd $F\QA_reports; $env:PYTHONPATH="$F;$F\01_design_system"; & $PA qa_fig34.py   # 统计图，期望 TOTAL FAIL: 0
```

### 4.3 依赖关系图

```
原始数据（_sources/*.jsonl|csv|json、data_sources.py、源码 audit.py/expert_graph.py）
        │
        ├─ statistics/_recompute_fig3.py ──► statistics/fig3_recomputed_stats.json
        │                                            │
        │                                            ├─► Fig3/fig3_builder.py ─► fig3_retrieval.{svg,pdf,eps,png,tif}
        │                                            └─► Fig4/fig4_builder.py ─► fig4_quality.{svg,pdf,eps,png,tif}
        │
        ├─ Fig1/fig1_builder.py ─► fig1_system.svg ─┐
        ├─ Fig2/fig2_builder.py ─► fig2_corpus.svg ─┤
        │                                           └─ 01_design_system/svg2pptx.py ─► *.pptx ─(LO-Kit)─► *.pdf / preview/*.png
        └─ 01_design_system/{design.py, fig_style.py, icons_ms/}  ← 样式与图标单一事实源
```

---

## 五、从零重绘流程（迁移后建议顺序）

1. **核对环境**：确认 §4.1 四个可执行文件存在；`python -c "import matplotlib"`（rag-agent）通过。
2. **核对数据**：跑 `QA_reports/_migration_inventory.py`（内置 py 即可），确认 §3.1 的 14 个文件全部 OK；
   若有缺失，见 §3.2 的同名副本。
3. **核对源未被改动**：用 `00_audit/source_hashes_baseline.csv`（96 行 sha256）复算比对。
4. **跑复算**：`_recompute_fig3.py` → 检查 **十维合并 delta 应为 glm +0.6372 / kimi +0.0173**（逐位复现正文）。
5. **重绘四图**：按 §4.2 ①②③④ 顺序。
6. **过质量门**：§4.2 ⑥，两处都必须 `TOTAL FAIL: 0`。
7. **目检**：读 `Fig3/fig3_retrieval.png`、`Fig4/fig4_quality.png`、`Fig1|Fig2/preview/page-0001.png`，
   确认无叠压/裁切（本会话据此修过 8 处）。
8. **更新报告**：`FIGURE_DELIVERY_REPORT.md`、`QA_reports/QA_fig*.md`。

---

## 六、迁移清单

### 6.1 必须带走（约 110 MB，其中 TIFF 占 ~105 MB）

```
CitrusQA_HA_Figures\
├─ Fig1\ Fig2\ Fig3\ Fig4\          成品 + builder + manifest + caption（TIFF 可选，可随时重导）
├─ 01_design_system\               design.py · fig_style.py · svg2pptx.py · icons_ms\(34) · icons\(35)
├─ statistics\                     _recompute_fig3.py · fig3_recomputed_stats.json
├─ QA_reports\                     qa_fig12.py · qa_fig34.py · design_meta.py · QA_*.md · *_checks.json
│                                  · _evidence_audit_gate.txt · 各探针脚本与 .txt 输出
├─ 00_audit\                       审计档案（含 96 文件哈希基线，用于验证源未被改动）
├─ FIGURE_DELIVERY_REPORT.md · Round2_Delivery_Report.md · 本交接书
```

### 6.2 不必带走

`__pycache__/`、`QA_reports/_edit_probe/`（PPTX 改字实验的临时件）、
`Fig3/*.tif`、`Fig4/*.tif`（各 ~50 MB，重跑 builder 即得）、`QA_reports/_migration_inventory.txt`（可再生成）。

### 6.3 迁移后需要新主机提供的东西（不在项目目录内）

- rag-agent 环境（matplotlib+numpy）——否则 Fig.3/4 无法重绘（可改用任意 py3.11+ 装 matplotlib）；
- LibreOffice（经 LibreOffice-Kit CLI）——否则 PPTX→PDF/PNG 渲染与预览无法重建；
- 图标素材若丢失，按此规则重下（语义名 ← Material Symbols 名）：
  `https://unpkg.com/@material-symbols/svg-400@latest/outlined/<ms>-fill.svg` →
  保存为 `01_design_system/icons_ms/<语义名>.svg`。映射（34 条）：
  circle-user-round←account_circle · message-circle-question←help · languages←translate ·
  book-open-text←menu_book · sprout←eco · folder-lock←folder_managed · search←search ·
  list-ordered←format_list_numbered · pen-line←edit · globe←public · scale←balance ·
  brush←cleaning_services · layers←layers · shield-check←verified_user · clipboard-check←fact_check ·
  file-search←find_in_page · refresh-cw←sync · file-check-2←task_alt · globe-lock←lock · shield←shield ·
  code-xml←code · message-square-text←chat · clock←schedule · file-code-2←code_blocks ·
  clipboard-list←checklist · database←database · snowflake←ac_unit · bug←bug_report ·
  activity←monitor_heart · dna←genetics · tractor←agriculture · package←inventory_2 ·
  eraser←ink_eraser · chart-column←bar_chart · workflow←account_tree

---

## 七、坑与注意事项（实测踩过）

1. **LibreOffice-Kit 不支持 `svg → pdf/png/eps`**（只支持 doc/xls/ppt/pdf ↔ 及 pdf/office → png）。
   因此：结构图的 PDF/PNG 是**经 PPTX 孪生**渲染的；结构图**没有 EPS**（用矢量 PDF 等效），
   统计图的 EPS 由 matplotlib 直接输出。
2. **matplotlib 的 `imshow` / `colorbar` 在 SVG 后端会嵌入位图**（破坏可编辑性）。
   本项目已改为逐格 `<rect>` 热图 + 自绘矢量色阶图例（`fig_style.heatmap/heat_legend`）。
3. **不要用 `bbox_inches='tight'`**：会把 174 mm 画布裁成 162.6 / 撑到 210.9 mm。
   必须 `bbox_inches=None` 精确出图，并把注记放在画布内（`fig_style.save` 已固化）。
4. **matplotlib 的字体/字号写在 `style="font-family:…; font-size:…px"` 里**，不在属性上——
   写 QA 解析时要扫 style 字符串。
5. **matplotlib SVG 含 `<metadata>`（带日期）+ 随机 id**，字节级比较不可能相等；
   确定性检查应比较"绘图元素签名"（本项目做法：抽取 `path d`/`text` 内容做 sha256）。
6. **PPTX 图标**：Material Symbols **无圆弧命令**才可扁平化（本项目 35/35 满足）；
   若换成含 `A/a` 弧命令的图标，`path_to_polys` 会返回空 → 自动退化为占位圆（不会静默出错）。
7. **`_sources` 里 `judge_scores_*.jsonl` 的 D1/D2 只在有检索的配置有值**
   （Track A：kimi 68/84、glm 78/84；B–E 全空）。热图用 D3–D10，Fig.4(b1) 只画 Track A。
8. **两套实验不能合并**：`exp1_summary.json`（合成 96 题 / 锚点 48 题）与
   `judge_scores`（系统层 84 题）是不同实验；`crosslingual_20260918` 是 84 题那套的原始件。
9. **联网五臂的 n 语义**：每臂 48 题配对；**n=96 = 48 题 × 2 判官**的论断判定数，
   rate = 逐题均值；MDD 只对 A−B、A−C′ 存档；**B−C′ 差 5.2 pp < MDD 12.4 pp 判不可分辨**；
   **3.1% 只是"不联网"臂基线，不能当作"两段式隔离"有效的证据**。
10. **`render --output-dir` 要指向空目录**，否则可能静默不产出（先删再建）。
11. **内置 python 无 matplotlib**；统计图必须用 rag-agent 环境（或另装 matplotlib 的 py3.11+）。
12. `data_sources.py` 的 `CORPUS_*` 仍是弃用值 **187,441**（冲突 C1）——本任务不改源码，绘图时不要引用它。

---

## 七·补·-1、2026-10-16 第二轮修订要点（迁移必读）

* **Fig.1**：原始版式不变，按 PI 意见加/改箭头与标签、字体统一 Arial；
  机检脚本 `Fig1_system_original/_layout_check.py`（应输出 FAILURES: 0）；
  三条覆盖层都堆在 `flow_layout.py` 末尾（`_apply_pi_palette` → `_apply_flow_fixes`），
  重跑顺序：`_setup_original_pipeline.py` → `_patch_second_pass.py` → `_add_flow_fixes.py` → 出图。
* **Fig.3e/f**：森林图为 **D3–D10 + `Mean (D3–D10)`** 九行，点估计 + 95% CI + 零线，
  **无 MDD 灰带/判定**；数值来自 `statistics/recompute_forest_D3_D10.py`
  （glm +0.5804、kimi +0.0789）。论文旧的 +0.6372/+0.0173 是"各自可用维度"口径，需同步更正。
* 已核对：阈值公式、早停参数、联网轮次、BM25/reranker/embedding 模型名均与源码一致（见 README §五·补）。

## 七·补·0、Fig.1 现行版本 = 原始版式（2026-10-16）

**目录：`CitrusQA_HA_Figures/Fig1_system_original/`**（旧的重设计版式 `Fig1/` 已标 `_SUPERSEDED`）。

```powershell
$PA='E:\anaconda\envs\rag-agent\python.exe'; $PB='<内置 py3.12>'
cd Fig1_system_original
& $PA flow_system_master.py   # → out/fig1_system_original.{svg,pdf,png(600dpi),tif}
& $PB ppt_twin.py             # → out/fig1_system_original.pptx（可编辑，单页）
& $PB _parity_check.py        # 几何一致性复核（应输出 IDENTICAL: True）
```

基础脚本（**只读、未修改**）：`paper/系统篇_v7/figures_main/fig1_system/{flow_layout.py,
flow_system_master.py, mpl_style.py}` + `experiment/2026927-python绘图latest/00_shared/{ppt_style.py,
ppt_twin.py}`。配色通过 `flow_layout.py` 末尾的 `_apply_pi_palette(LAYOUT1)` 覆盖层施加，
**只改颜色字段，坐标/文字/连线零改动**；两份 SVG 颜色归一后逐字节一致。

已知项：PPT 孪生 `Corpus layer` 卡正文在 PPT 里比图多折一行（原始工具的字体度量差异
+ 10-14 版更长文案所致，与配色无关），详见该目录 README §六。

## 七·补、旧 Fig.1（重设计版式，已废弃）的配色修订记录（2026-10-15）

按 PI 标准配色表改 Fig.1：**标题条由浅填充改为实心主色 + 白字**（副题行用对应浅色），
大面积模块仍用浅填充——其余色板项与文字/几何不变。
同轮修掉 3 处遗留叠压缺陷（渲染目检 + 坐标核算发现）：
① 第 2/3 列底卡（Generation/Tiering，底边 y≈238）被徽章条压住 → 徽章条 222→250、
面板行 246→292、**画布高 118→135 mm**（列内卡片与文字零改动）；
② 徽章盒高 20 → 26（第二行副题原本落在盒外）；
③ 面板 (b) 芯片高 30 → 34、脚注 +64 → +70（副标签原本越框 0.9 单位）。

**Fig.2 尚存 1 条同类旧瑕疵**：`unparseable · too short` 文字越出"排除"小框 0.5 单位（≈0.18 mm）；
属另一张图，本次未动，需要修的话把 `Fig2/fig2_builder.py` 里该小框高度 +2 即可。

## 八、待 PI 裁定项（重绘前建议先确认）

1. **MDD 口径**：正文十维合并 MDD 用 **z = 1.96**（0.1596 / 0.1643）；本图按自由度用
   **t(.975,83)** 得 0.1621 / 0.1669（差 ~1.6%，结论不变：glm 远超阈、kimi 远低于阈）。
   是否统一到 t？
2. **Fig.1 审计列画法**：已按源码改为 `Stage A（IDs within pool / caliber / recency）→ Stage B →
   repair (once) → Outcome`，删掉了版式参考里的独立 "Cite audit" 卡与 "Re-validate" 箭头。
   若希望恢复参考版式，需在正文补齐对应机制。
3. **Fig.3 / Fig.4 是否需要 PPTX 孪生**（目前只有结构图有；统计图给 SVG/PDF/EPS/PNG/TIFF）。
4. **结构图 EPS**：若 HA 强制要求 EPS，需在新主机装 Inkscape/CairoSVG 后从 SVG 转一次。
5. **来源集合数值**：正文与参考图不一致处（33,790/9,801/6,496 vs 33,708/8,805/6,509）以 §3.3 表为准。
