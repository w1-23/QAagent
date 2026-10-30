# Fig.1（原始版式）· PI 配色版

> 本目录 = **原始 system 架构图**（昨天我动手之前的版式）+ **只改配色**。
> 生成于 2026-10-16。原始脚本与原始产物**未被修改**（只读基准）。

## 一、基准（原始脚本，只读）

| 文件 | 位置 | 说明 |
|---|---|---|
| `flow_layout.py` | `paper\系统篇_v7\figures_main\fig1_system\` | 版式唯一事实源 `LAYOUT1`（节点坐标/尺寸/文字/配色）。原文件最近更新 **2026-10-14 16:02** |
| `flow_system_master.py` | 同上 | matplotlib 渲染入口 |
| `mpl_style.py` | 同上 | 画布/圆角框/箭头/折行工具 |
| `ppt_style.py`、`ppt_twin.py` | `experiment\2026927-python绘图latest\00_shared\` | 可编辑 PPT 孪生（与 `LAYOUT1` 1:1 共享版式） |
| 原始产物 | `…\fig1_system\flow_system_master.{svg,pdf,png,tif}` | 2026-10-14 16:02 的原图 |

这些原始文件**一个字都没改**（对比用的基准就是它们）。

## 二、本次改了什么（只有颜色）

把 `LAYOUT1` 的配色按 PI 标准色板**语义对映**（原布局自带 legend：S1 = Supervisor、
S3 = Local RAG Agent、S7 = Web Agent、S6 = Assembly）：

| 语义 | 主色 | 浅色 | 用在 |
|---|---|---|---|
| Supervisor 协调 | `#287D99` | `#E4F2F8` | User input / Supervisor orchestrator / Supervisor final 三条整幅蓝带 |
| Local RAG 本地文献 | `#4F8868` | `#E7F1E5` | Local RAG Agent 容器、5 张子卡、Section 1 |
| Web 联网 | `#C87558` | `#FBEAE1` | Web Agent 容器、5 张子卡、Section 2 |
| Assembly & audit 审核 | `#BE9346` | `#FAF0D8` | Assembly & audit 容器、Stage A / Stage B / re-validate / one minimal repair / critical? 菱形 |
| Metadata 元数据 | `#7772A8` | `#F0EDFA` | **本版式没有元数据模块 → 未使用**（备后续附图） |
| 文字 / 次文字 / 边框 | `#26343B` | `#52616A` | 标题 / 正文、箭头与次要文字 |
| 画布 / 门框 | `#FFFFFF` | `#D4DCE0` | 背景、嵌套 gate 边框 |

落地方式：`flow_layout.py` 末尾追加 `_apply_pi_palette(LAYOUT1)` 覆盖层（**只重写颜色字段**，
坐标/尺寸/文字/连线一律不动）；`flow_system_master.py`、`mpl_style.py`、`ppt_style.py`、
`ppt_twin.py` 中的文字色 `#1F1F1F→#26343B`、`#666666→#52616A`、箭头 `#333333→#52616A` 同步。

## 三、几何一致性（可复算的证明）

`_parity_check.py` 把两份 SVG 的颜色字面量、matplotlib 随机 clip-path id、
`<dc:date>` 时间戳全部归一后逐字比对：

```
lines                       : 730 vs 730
COLOUR-NORMALISED IDENTICAL : True
original colours (12): #1F1F1F #333333 #49C2D9 #666666 #A1D8E8 #A2C986 #D0E2C0
                       #F47254 #F59C7C #FDED95 #FFC1A6 #FFFFFF
new colours     (12): #26343B #287D99 #4F8868 #52616A #BE9346 #C87558 #D4DCE0
                       #E4F2F8 #E7F1E5 #FAF0D8 #FBEAE1 #FFFFFF
```

→ **版式与文字与原图完全一致，只有颜色变化**；且新用色**恰好等于 PI 色板**，无杂色。
（详细输出见 `_parity_check.txt`）

## 四、产物（`out/`）

| 文件 | 说明 |
|---|---|
| `fig1_system_original.svg` | 矢量母版（文字可选可改） |
| `fig1_system_original.pdf` | 矢量投稿件（EPS 等效） |
| `fig1_system_original.png` | **最终图**，600 dpi（4195×5007 px） |
| `fig1_system_original.tif` | 600 dpi TIFF（82 MB） |
| `fig1_system_original.pptx` | **可编辑 PPT 孪生**（与图 1:1，单页） |
| `fig1_system_original_ppt_preview.png` | PPT 渲染预览 |

## 五、重绘命令

```powershell
$PA='E:\anaconda\envs\rag-agent\python.exe'      # matplotlib
$PB='C:\Users\...\dependencies\python\python.exe' # python-pptx
cd CitrusQA_HA_Figures\Fig1_system_original
& $PA flow_system_master.py     # → out/fig1_system_original.{svg,pdf,png,tif}
& $PB ppt_twin.py               # → out/fig1_system_original.pptx
& $PB _parity_check.py          # → 几何一致性复核
```

## 五·补、2026-10-16 流程修正 + 字体统一（PI 逐条意见）

| # | PI 意见 | 处理（依据） |
|---|---|---|
| 1 | Local RAG → Audit 的箭头起点应在**分支输出端**，不是 Rerank 高度 | 改为**容器输出端口**：箭头从容器右侧底部（y=15.69 cm）出发 → 走两列之间的空隙（x=5.25）→ 在审计容器中线（y=10.22）进入 |
| 2 | Web Agent → Audit 同理，应从整条联网流程结束后引出 | 镜像处理（右空隙 x=11.75）。**并把联网内部 5 个模块加上顺序箭头**，第 2→3 步标 "if insufficient"（judge 条件触发，`web_agent.max_rounds=2` 硬上限） |
| 2b | 联网内部顺序要**与真实代码一致** | 按 `web_evidence_pipeline.LAYER_ORDER = (fetch, tiers, content, meta, embed, adjudicate, rerank)` 把 **tier tagging（L1）提到 cleaning & metadata（L2–L3）之前**；T3 文案由 "cannot support" 改为 "(labels only)"（`source_tiers.py`：tier 不作弃用硬门） |
| 3 | Supervisor 与 Assembly & audit 的控制关系要明确 | 新增**中轴控制箭头** Supervisor orchestrator → Assembly & audit（直线，不与其他线交叉）；两路输出端口汇入审计容器，再下接 Supervisor final — 即 协调两路 → 汇集 → 审计 → 最终交付 |
| 4 | 实现细节标签逐个核对 | 见表下；`output to [4]` → **"to Supervisor final"**；Section 2 "never pooled with local" → **"not in the local citation pool"**（指最终引用池/输出边界，非内部完全隔离） |
| 5 | 字体统一 Arial（标题 Bold / 正文 Regular），不得靠缩小字号塞细节 | matplotlib 端 `font.family → ["Arial","Helvetica","DejaVu Sans"]`；PPT 端 `FONT_LATIN → "Arial"`，并修掉原始孪生把英文也用中文字体（SimSun）渲染的默认分支。字号一律未改 |
| 6 | **`User input (CN)` 用词**：CN 是 ISO-3166 国家代码，不是语言代码（中文的 ISO 639-1 是 `zh`） | 表头改为 **`User input (ZH)`**（PI 指定用语言码）；正文 `no [Wn] / URL; CN or EN` 同步改为 **`... ; ZH or EN`**（2 字符等长、零重排）。全图已无 `CN` |
| 7 | **位置注记未与一级标题对齐**："Assembly: renumber, drop dead markers" 原为左对齐（`T(A3_X+0.25, …, A3_W-0.5, align=left)`），与其上居中的 "Assembly & audit" 不齐 | 改为 **同一锚点居中**（`x=A3_X, w=A3_W, align=center` → 锚点 8.5 cm，SVG 实测与标题同为 `x=251.74pt` + `text-anchor: middle`），**纵向参数（y、h）一字未动** |

### 标签核对结果（对照 `01_源码_src/src`）

| 图中标注 | 源码依据 | 结论 |
|---|---|---|
| `T = max(0.25, s_top × 0.60)` | `multi_retriever.py:637`：`max(RERANK_THRESHOLD, top_score*DYNAMIC_THRESHOLD_RATIO)`；`config.py`: 0.25 / 0.60 | ✅ 逐字一致 |
| `RRF k = 60, window W = 20` | `config.py`: `RRF_K=60`、`CANDIDATE_WINDOW=20` | ✅ 一致 |
| `evidence >= 6, gain < 0.25` | `config.py`: `EARLY_STOP_MIN_EVIDENCE=6`、`EARLY_STOP_NEW_RATIO=0.25`；`agent_runner.py:673`：`prev_unique ≥ 6 and new_ratio < 0.25 → break` | ✅ 语义正确；文案改为 `evidence ≥ 6 and new < 25%` 更清楚 |
| `Dense e5-large` | `config.py`: `EMBEDDING_MODEL = intfloat/multilingual-e5-large` | ✅ 缩写可接受（完整 checkpoint 写入 Methods 更佳） |
| `Sparse BM25+` | `retrieval/bm25.py`: `class BM25Plus(k1=1.5, b=0.75, delta=1.0)` | ✅ 一致 |
| `bge-reranker-v2-m3, top-K = 10` | `config.py`: `RERANKER_MODEL=BAAI/bge-reranker-v2-m3`、`TOP_K_FINAL=10` | ✅ 一致 |
| `EN rewrite` | `intake.py`: 英文改写/检索/回答全部以 `intent_confirmed` 为锚点 | ✅ 保留（HITL 确认后锚定改写） |
| `<= 2 rounds hard cap` | `web_agent_loop.py`：轮次上限由代码控制，`web_agent.max_rounds` 默认 2 | ✅ 一致 |
| `T1 official, T2 media, T3 self-media` | `source_tiers.py`：确定性域名分级；**T3 不作硬门** | ✅ 调整措辞为 "(labels only)" |

### 机检（本轮）

- `_layout_check.py`：**FAILURES: 0**；无卡片因 Arial 换行而增高；节点几何零变化；
  改动集合 = 4 处文字 + （2 条旧分支箭头 → 2 条端口路线 + 1 条控制线 + 7 条内部线）+ 1 条 "if insufficient" 注记。
- 画布 17.00 × 20.44 cm（170 × 204 mm）；字号未改（正文 8 pt / 标题 10 pt / 卡片 8 pt+7 pt），
  在 174 mm 栏宽下按 ≤1.02 倍放大呈现，可读性不降。

## 六、已知项（如实记录）

1. **PPT 孪生有 1 处文字折行溢出**：`Local RAG Agent → Corpus layer` 卡正文在 PPT 里折成 3 行、
   略越出卡片下沿（图本身正常）。原因：PPT 孪生的折行估算函数 `ppt_style.text_w_cm` 依赖
   matplotlib 字体度量，本机 Times New Roman 回退导致估算偏窄；而 10-14 版文字
   （"4,906 documents / 126,494 chunks / frozen manifest, DOI-keyed"）比 9-28 旧版
   （"11 batches / 187,441 chunks / 4,992 dedup papers"）更长，于是多折一行。
   **这是原始孪生工具链 + 新版长文字的产物，与本次配色改动无关**（未改配色时报同样现象）。
   可选修法（待 PI 决定，均不触碰 matplotlib 图）：① 给卡片高度加"一行余量"；
   ② PPT 正文 8pt→7.5pt；③ 把该卡正文缩成 2 行文案。
2. 原始基准文件保持只读未改；本目录是"复制 + 配色覆盖"的独立工作副本。

## 五·补·2、2026-10-16 PI 字号裁定（只改字体与字号，其余不动）

* 图内**只声明 Arial**：mpl_style.py → ont.family = ["Arial"]（去掉 Helvetica / DejaVu 回退）；
  ppt_style.py → FONT_LATIN = "Arial"，并修掉原始 _style_run 把英文也走中文字体的分支；
  ppt_twin.py → 所有文本框设 uto_size = TEXT_TO_FIT_SHAPE（Office 字宽大于 matplotlib 换行模型，故需自适应）。
* **三级字号**：一级模块标题 10 pt Bold；二级框标题 9 pt Bold；模块说明与全部辅助/箭头标签 8 pt Regular。
  SVG 实测 ont-size 只有 10 / 9 / 8 三档，ont-family 全部为 'Arial'。
* 两处必要避让（内容与字号不变）：local_goal ∥ web_goal → local_goal || web_goal（Arial 无 U+2225，
  否则回退 DejaVu Sans）；loop back to Stage A 与 if insufficient 改为右对齐锚点（分别避开审计出口线与容器边界）。
* 机检：_layout_check.py **FAILURES 0**、无盒子增高；low_system_master.py 无缺字告警；
  PPT 渲染 missingFonts 为空。导出件在 out/（已覆盖）：png / pdf / svg / tif / pptx + ppt 预览。
* 重跑顺序（_setup_original_pipeline.py 会还原原始副本，故必须按序）：
  _setup_original_pipeline.py → _patch_second_pass.py → _add_flow_fixes.py → low_system_master.py → ppt_twin.py。
