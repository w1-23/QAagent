# 绘图 Skill / 工具可用性报告（Round 1 · skill_availability_report.md）

> 审计边界（遵任务书 2.2 与用户第 5 条）：**本轮只审计安装状态、依赖、兼容性、许可证与可编辑矢量输出能力**；
> 不安装未经批准的软件、不下载大体积依赖、不执行不可信安装脚本、不生成正式测试图。
> 状态三级：**Available**（本机已有、依赖与基础功能可验证）／**Candidate**（已找到项目与说明，未完成兼容性验证）／**Unavailable**（当前环境不可用，记录原因）。

---

## 一、三个候选仓库的核查结果（均已确认存在，逐个给出事实）

### 1. `Techd81/SciFig` — **Candidate**
- 事实：GitHub 页面标题为「Turn experimental data into publication-ready scientific figures directly inside Claude Code：121 种图表类型，支持 Nature/Cell/Science 等期刊排版风格（**MIT 开源**）」。
- 形态：**Claude Code 的 skill 形态**（面向 Claude Code 使用，非通用 Python 库）。
- 可编辑矢量输出：README 未取得全文，**未能验证**是否产出可编辑 SVG。
- 结论：**Candidate**。未在本环境安装、未做兼容性验证 → **不得声称"已兼容 DeepSeek Harness"**。

### 2. `tvhahn/matplotlib-skill` — **Candidate**
- 事实：GitHub 仓库存在，描述为「Claude Code skill for publication-quality matplotlib/seaborn charts」（约 38 stars）；含 `skills/matplotlib/SKILL.md` 与 `skills/matplotlib/style-reference.md`。
- 形态：Claude Code skill（SKILL.md + 风格参考）。
- 可用价值：其**风格参考文档**可作为我们 `00_shared/mpl_style.py` 的对照物（纯阅读参考，无需安装）。
- 结论：**Candidate**（作为**风格参考**可用；作为**执行器**未验证）。

### 3. `lilingm963/scifig-ai-scientific-figure-skill` — **Candidate（仅规划/提示词）**
- 事实：GitHub 仓库存在，描述为「An open-source agent companion for scientific figure **planning and prompts**, with eight-language guides and links to SciFig resources. **Image generation uses your configured tools**.」
- 形态：**图件规划与提示词助手**，自身不渲染图形；生成依赖外部配置工具。
- 结论：**Candidate（规划用途）**；**不能**产出可编辑矢量图 → 不满足本项目的结构图/统计图产出要求。

> 另：网络检索到的 `scifig.com` / `scifig.ai` 是**商业 SaaS**（仅 Windows、WPS/Office），与上面第 1/3 个开源仓库不是同一事物 → 混淆风险已在冲突表登记（避免把 SaaS 当作开源 skill 使用）。

---

## 二、本机实际可用的绘图能力（Available）

| 能力 | 状态 | 证据 |
|---|---|---|
| Python + Matplotlib 出版级绘图 | ✅ Available | `experiment/2026927-python绘图latest/00_shared/mpl_style.py`（现成 rcParams/助手函数）+ 已产出的 svg/pdf/png/tif 四格式 |
| python-pptx（PPTX 生成） | ✅ Available | 本轮已用它生成《主图图注_A4.pptx》 |
| Node.js v24.21.0 | ✅ Available | `node --version` → v24.21.0（可用 PptxGenJS 路线） |
| pandas / numpy / SciPy | ✅ 可用（rag-agent 环境） | 统计复算（配对 t、Wilson CI、QWK）可用 pip 环境中已有库；具体版本在第二轮执行时固化 |
| LibreOffice（渲染校验） | ✅ Available | 本轮渲染修订稿成功（18 页） |
| PDF 解析（审计用） | ✅ Available | pypdf / PyMuPDF(fitz) |

---

## 三、第二轮技术路线（按用户裁定）

| 用途 | 选定路线 | 理由 |
|---|---|---|
| 架构图 / 清洗流程图（Fig.1、Fig.2） | **PptxGenJS（首选）** 或原生 SVG | 大量可编辑形状与连线；PptxGenJS 更适合综合流程图；SVG 保底 |
| 统计复算（Fig.3、Fig.4） | Python + pandas + SciPy | 逐题数据复算均值/配对 t/MDD/Wilson CI |
| 统计图绘制 | **Matplotlib** | 已有出版级规范与四格式导出链 |
| 图标 | 合法授权 SVG（Lucide / Material Symbols；Bioicons 逐图标核验） | 见 `icon_manifest.csv`；一套体系、禁止混风格 |
| 投稿导出 | 按 HA 官方指南（SVG/EPS + TIFF + PNG；图宽 84/174 mm） | 已在 `paper/系统篇_v7/HA绘图风格与配色提炼.md` 归纳 |

---

## 四、可编辑矢量的验证标准（写入第二轮验收）

**不因"Matplotlib 能导出 SVG"就认定可编辑**。第二轮每张图必须逐项检查：
1. 文字是否为**文本对象**（`svg.fonttype='none'`，可选中、可搜索）；
2. 形状是否仍为**矢量路径**（非整图栅格化）；
3. 线宽/字号在最终印刷尺寸（174 mm 宽）下的实际可读性；
4. PPTX 孪生版中每个模块是否**可拖拽、可改字**；
5. PDF/EPS 与 SVG 的**交叉渲染一致性**。
