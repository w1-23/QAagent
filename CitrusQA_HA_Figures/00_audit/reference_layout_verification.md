# 参考图判读：「版式可复制 / 内容必须替换」清单

> **裁定（用户 2026-10-14）**：参考图**只作绘图效果（版式与视觉）参考，不作为数据来源**；
> **数据一律以本项目实测/论文冻结口径为准**；**每张图的叙事目标与原定一致**
> （Fig.1 系统架构、Fig.2 知识库构建、Fig.3 跨语言检索与双判官、Fig.4 质量/引用/联网、Fig.5 真实案例）。
> 因此参考图中的数字与标签**不构成"源冲突"**，仅是**出图时须逐项替换的示意值**。

> 适用对象（均为用户 2026-10-14 提供的设计参考，**非数据来源**）：
> - `source_assets/fig2_design_reference_20261014.png`（sha256 `855BCBBB…CDA3`）
> - `source_assets/layout_reference_fullpage_20261014.png`（sha256 `D205B73F…E3E5`）
> 用户指示：**"最终每个图绘制的整体排版视觉可以与这个类似"** → 版式语言采纳；**数值与模块名一律以审计核实结果为准**。

---

## 一、可复制的视觉语言（已确认采纳）

> **用户强调（2026-10-14）**：参考图的**主要价值在于其图标素材与视觉词汇**——每个模块都有可辨识的小图标、
> 清晰的标题与简短说明、整体有层次但不密集。正式出图要保留这种表达方式，但**使用一套统一的可编辑矢量图标**，
> 而不是每次让绘图模型临时新画。

| 图 | 采纳的版式要素 |
|---|---|
| Fig.1 | 五列横向流程（User question (CN) → Local RAG agent → Web agent → Assembly & audit → Two-section output）+ 底部四枚能力徽章条（Source isolation / Code-based verification / Chinese-friendly / Timely but bounded），**每个模块配一枚语义图标** |
| Fig.2 | (a) 四阶段色块流水线 + 箭头（每阶段配图标）；(b) 左侧 Dataset 泳道 → 中间 Source collection → 右侧 Resource class 的三段式流带（宽度=chunk 数） |
| Fig.3 | 上排两张判官热图（D3–D10 × 配置，同色标）+ 下排 Track A−Track B 配对差森林图（含 0 线与 95% CI），双判官左右并列 |
| 全图 | 纯白画布、柔和浅色模块、小圆角矩形、细箭头、深色标题、面板标签 (a)(b)(c)、紧凑多面板；投稿版用英文标签 |

**色板配合**：参考图使用的青/绿/琥珀/紫四色与既定设计规范一致
（Supervisor `#287D99`／Local RAG `#4F8868`／Assembly-Audit `#BE9346`／Metadata `#7772A8`），
可直接沿用。

**图标方案**：参考图用的是"彩色实心插画风"图标（版权不明，**不可直接复制**）。
本项目改用**一套有明确授权的矢量图标库**并统一风格；逐模块映射见
`icon_manifest.csv`（本轮已扩展到 27 项，覆盖两份参考图出现的全部语义）。

---

## 二、必须替换的内容（与真实数据/实现不符）

| # | 参考图中的元素 | 参考图值 | **权威值 / 来源** | 处置 |
|---|---|---|---|---|
| 1 | Fig.3 判官名称 | 「Judge 1 (e.g., **GPT-4 family**)」「Judge 2 (e.g., **Claude family**)」 | **kimi-k2.6 / glm-5.3**（`judge_scores_{kimi,glm}.jsonl`；RUNTIME_TRUTH judge.models） | **必须改**；不得写成 GPT-4/Claude |
| 2 | Fig.3 热图**行名** | CitrusQA (ours) / **RAG (general)** / **w/o delivery check** / **Web mixed** / **Web separated** | **Track A–E**（论文表 2：A=全链、B=同基座无检索+本文提示词、C=同基座无检索+出厂提示、D/E=另一基座） | **必须改**；"Web mixed/separated"属 Fig.4 联网五处置语义，不可混入 Fig.3 配置对照 |
| 3 | Fig.2(B) 饼图百分比 | 36 / 38 / 22 / 15 / 13（**和=124%，非 100%**） | 无对应机读数据；我们只有 **chunk 级**类别计数（103,089 / 10,018 / 13,137 / 250） | **数据不可用** → 请提供文档级类别数，或改用 chunk 级类别（条形/堆叠） |
| 4 | Fig.2(B) 来源集合数字 | 62,803 / **33,708** / **8,805** / **6,509**（合计 111,825） | **62,803 / 33,790 / 9,801 / 6,496**（论文 v7 §3.2；四项合计 **= 112,890** 主语料；七项合计 = 126,494） | **必须改 3 处**；名称「SCI novelty resources」→「UCR 品种资源」 |
| 5 | Fig.2(B) 释读标签 | 「(13,137 entries)」标为 Translated resources | 13,137 = **翻译语料**（chunk 数，非 entries） | 措辞更正为 chunks / 文本块 |
| 6 | Fig.1/Fig.2 解析工具 | 「PDF / Markdown / Word → **MinerU** → Markdown」 | 源码 `04_脚本_scripts/ingest.py` = **PyMuPDF(fitz) 全文提取** | 待裁定后删除或改注 |
| 7 | Fig.1 审计段 | 「**Citation audit**」画作独立阶段 | 源码中属 **Stage A** 的检查项（`check_citation_ids`） | 待裁定：并入 Stage A 或保留独立画法 |
| 8 | Fig.1 审计段 | 「**Re-validate** → Back to Stage A **if needed**」 | 未定位到显式回环路径（`audit.py`：critical → 1 次修复循环） | 待裁定：保留箭头或删除 |
| 9 | Fig.1 联网模块 | 「Multi-source search：**official sites, databases, news, extension resources**」 | 仅证实「服务端联网（DeepSeek 原生 web_search）+ **T1/T2/T3 分级**」；且 `source_tiers.py` 明确 **tier 不得用作丢弃闸门** | 收敛措辞；删除"多数据库检索"暗示 |
| 10 | Fig.2(A) 四阶段名 | Source admission / Cleaning / **Metadata QA** / Chunking & Indexing | **与 WxRev §2.2 正文一致 ✓** | 可采纳；但**当前修订稿图注写的是"来源准入、清洗切块、索引、服务四阶段"** → 图注须与正文统一 |
| 11 | 热图数值 | 4.6 / 4.4 / 3.4 … 等 | 须由 `per_dimension_means.csv`（源自逐题 `judge_scores`）重算 | 一律重绘，禁止沿用示意值 |
| 12 | Fig.3 维度范围 | D3–D10（双判官） | 与论文一致（**D1/D2 不参与跨配置比较**）✓ | 可采纳；图注需写明该规则与 Track D n=80 |

---

## 三、由参考图新增的待裁定项（并入 Round 1 阻塞清单）

1. **Fig.3 热图行名**：采用 Track A–E 原名，还是采用业务别名（如 "RAG (general)"）？若用别名，须与表 2 建立一一映射并在图注说明。
2. **Fig.2(B) 饼图**：提供文档级类别计数，还是改用 chunk 级类别（103,089/10,018/13,137/250）？
3. **Fig.1 审计段画法**：Citation audit 是否独立成阶段、Re-validate 回环是否保留？
4. **图注/正文四阶段名统一**：以"来源准入、清洗、元数据质检、切块与索引"为准（与 §2.2 正文一致）。

---

## 四、结论

- **版式**：两份参考图的布局与视觉可直接作为 Round 2 的施工图。
- **内容**：Fig.2(B) 的**来源集合数字有 3 处错误且中列不平**、饼图百分比和为 **124%**；Fig.3 的**判官名与配置行名均为占位**。这些必须在出图前替换为审计核实的数值与名称。
- **数据缺口未变**：Fig.2(B) 饼图所需"文档级类别分布"仍属缺失项（M-2 未解决）。
