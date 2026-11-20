# Nature Figure 资源只读审计（R2）

- 审计日期：2026-11-20
- 基线仓库：`<candidate-repo>`
- 分支：`feat/invite-auth-nature-figure`
- HEAD：`efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`
- 性质：只读资源审计；未修改 vendor、源码、配置；未执行导入脚本、安装、部署、提交、推送或任何网络调用。
- 说明：审计开始时 `git status --short` 为空；审计过程中出现另一份并行审计产物 `docs/audits/auth-current.md`（不属本工单），本次未触碰。

## 1. 工单目标

核对远程仓库中 Nature Figure（`nature-figure` 2.8.0）资源的来源、固定版本、文件清单、实际依赖与可交付边界；对上传 ZIP、只读解包目录和仓库 vendor 做逐文件 SHA-256 对照；逐项判定六基本类型、五模板、自由 Matplotlib、图注/美学、SVG/QA、仅参考 R、禁止执行的外部 AI 的 installed / discoverable / callable / sandbox_ready / QA_verified / delivered 状态；给出 F1 的最小保留固定版本方案。

## 2. 读取文件

必读文件均已读取（按实际路径）：

- ZIP manifest：`review/nature-figure/manifest.yaml`（ZIP 内 `nature-skills-main/skills/nature-figure/manifest.yaml`）
- `review/nature-figure/static/core/contract.md`
- `review/nature-figure/static/core/stance.md`
- `review/nature-figure/references/figure-contract.md`
- `review/nature-figure/references/design-theory.md`
- `review/nature-figure/references/chart-types.md`
- `review/nature-figure/references/template-catalog.md`
- `review/nature-figure/references/common-patterns.md`
- `review/nature-figure/references/multipanel-evidence-architecture.md`
- `review/nature-figure/references/figure-legend-conventions.md`
- `review/nature-figure/references/qa-contract.md`
- `review/nature-figure/references/vector-schematic-workflow.md`
- `review/nature-figure/references/vector-schematic-style-rules.md`
- 上游 `review/nature-figure/SKILL.md`
- 两套 provenance：`repo/skills/nature-figure/provenance.json`、`repo/skills/nature-figure/vendor/nature-skills/provenance.json`
- `repo/skills/nature-figure/SKILL.md`、`ADAPTER.md`、`RELEASE.md`、`references/bridge.md`
- 源码/接口辅助阅读：`packages/citrus_figures/{cli,core,verification,nature_runtime}.py`、`02_config/skills.catalog.json`、`01_src/src/agent/{skill_adapter,research_skill_catalog}.py`、根 `requirements-base.txt`、`pyproject.toml`

## 3. 写文件

本工单只写入了：

- `docs/audits/nature-inventory.md`（本报告）

未写任何 vendor、源码、配置、日志或其它仓库文件。

## 4. 接口与依赖

### 4.1 入口与接口

- 四个 Citrus 适配器：`skills/nature-figure/scripts/citrus/{render,bundle,revise,verify}_figure.py`。均为 430 B 薄封装，调用 `citrus_figures.cli.main([...])`，默认把 `vendor_root` 指向 `skills/nature-figure/vendor/nature-skills`。
- Python 包：`packages/citrus_figures/`（20 个文件）。公开接口 `verify_current_figure`、`render_figure`、`revise_figure`、`write_bundle`、`load_spec_bundle`、`FigureSession`。
- CLI：`python -m citrus_figures {verify,render,bundle,revise}`；`render/bundle` 同时支持 `--spec` 与 `--source`，后者即自由 Matplotlib 源文件入口。
- QA 检查项：`format_integrity`、`source_preflight`、`panel_alignment`、`pdf_text`、`pdf_collisions`、`svg_schematic`、`visual`。未注入 `visual_inspector` 时 visual 固定为 `not_run/capability_absent`。
- 上游脚本接口：`plot_templates.py` 五个子命令；`audit_svg_schematic.py`、`audit_pdf_text.py`、`audit_figure_collisions.py`、`audit_panel_alignment.py`、`validate_figure.py`、`nature_figure_backend.py` 均为 CLI。

### 4.2 实际依赖（源码文本证据）

| 组件 | 实际依赖 | 证据 |
|---|---|---|
| `plot_templates.py` | numpy、matplotlib（Agg） | 顶部可选导入，`require_dependencies()` |
| `audit_figure_collisions.py` | PyMuPDF（`pymupdf`/`fitz`） | 第 247/250、567/570 行延迟导入 |
| `audit_pdf_text.py` | 仅标准库（`re`、`zlib` 等） | 顶部导入 |
| `audit_svg_schematic.py` | 仅标准库（`xml.etree`、`re` 等） | 顶部导入；第 94 行使用 `math` 但未导入 |
| `audit_panel_alignment.py` | 仅标准库（`json`、`re`、`tempfile` 等） | 顶部导入 |
| `validate_figure.py` | 仅标准库（`ast`、`re` 等） | 顶部导入 |
| `figure_safety.py` | numpy | 第 8 行 |
| `nature_figure_backend.py` | 仅标准库 | 顶部导入 |
| `generate_openrouter_schematic.py` | 标准库 `urllib` + 外部 OpenRouter API | `API_URL=https://openrouter.ai/api/v1/images`，需 `OPENROUTER_API_KEY` |
| `panel_alignment.R` | R + patchwork、grid、jsonlite | R 注释/调用 |
| 包 `citrus_figures` | numpy、pandas、matplotlib、PyMuPDF | `pyproject.toml` 的 `figures` extras |
| 技能 requirements | `PyMuPDF>=1.24.0,<2.0.0` | `skills/nature-figure/vendor/nature-skills/skills/nature-figure/requirements.txt` |

补充探测：系统 Python 3.12.3 中 `numpy=False`、`matplotlib=False`、`fitz=False`、`pymupdf=False`、`PIL=True`；`Rscript`/`R` 均不存在。仓库根 `requirements-base.txt` 声明 `numpy==2.4.6`、`matplotlib>=3.7.0`、`PyMuPDF==1.26.7`、`Pillow>=10.0.0`，但本次未验证服务运行环境是否已安装。

## 5. 实际命令（只读）

```bash
git rev-parse --show-toplevel; git branch --show-current; git rev-parse HEAD; git status --short
find review/nature-figure -type f; find repo/skills/nature-figure -type f
sha256sum input/nature-skills-main.zip
unzip -Z1 input/nature-skills-main.zip
unzip -p input/nature-skills-main.zip nature-skills-main/skills/nature-figure/<file> | sha256sum
(cd review/nature-figure && find . -type f -print0 | sort -z | xargs -0 sha256sum)
(cd repo/skills/nature-figure/vendor/nature-skills && find . -type f -print0 | sort -z | xargs -0 sha256sum)
diff -u review/nature-figure/<file> repo/skills/nature-figure/vendor/nature-skills/skills/nature-figure/<file>
jq -r '.files[] | "\(.sha256)  \(.path)"' provenance.json | sha256sum -c -
jq -r '.skills[]|select(.id=="nature-figure")|.files[].path' 02_config/skills.catalog.json
grep -nE '^(def |class )' <scripts>
python3 -I -c "import importlib.util as u; ...find_spec..."
command -v Rscript; command -v R
```

## 6. 证据结果

### 6.1 来源与三层目录

| 层 | 路径 | 文件数 | 说明 |
|---|---|---|---|
| 原始上传包 | `input/nature-skills-main.zip` | 897 | 37,438,640 B；SHA-256 `a8f7b52f13d83539a2ec3a6677b5dd11513576b639082b6811f42549cdb45819`；顶层 `nature-skills-main/` |
| 只读解包（审查） | `review/nature-figure` | 130 | ZIP 中 `skills/nature-figure/` 子树的独立解包；非整仓解包 |
| 仓库固定 vendor | `repo/skills/nature-figure/vendor/nature-skills` | 46 | 只保留固定快照；不含图片资产、tests、evals、README 等 |

ZIP 内技能子树为 130 个文件；解包目录同为 130 个文件。抽查 4 个文件的 ZIP 流哈希与解包哈希完全一致：`manifest.yaml`、`scripts/plot_templates.py`、`scripts/audit_svg_schematic.py`、`static/core/contract.md`。

### 6.2 “40 / 44”固定版本结构

- 内层 `vendor/nature-skills/provenance.json` 固定 44 个文件：
  - 40 个 core：`LICENSE` + `skills/nature-shared/*`（4）+ `skills/nature-figure/*` core（35）
  - 4 个 pinned peripherals：`panel_alignment.R`、`audit_svg_schematic.py`、`nature_figure_backend.py`、`generate_openrouter_schematic.py`
- 44 个文件逐一 `sha256sum -c` 全部 OK，无缺失。
- 规范化 inventory 的 SHA-256 为 `a9b8e661bf460a401e17017f134a735432da9d7832e3f561a63c6cf47fb92b9e`，与 `packages/citrus_figures/nature_runtime.py` 中的 `PROVENANCE_FILES_SHA256` 完全一致。
- 因此：**44 文件固定快照自身完整、可校验**。

外层 `repo/skills/nature-figure/provenance.json` 额外覆盖 Citrus 适配层，共 56 项。注意：其中列出的 `vendor/nature-skills/.gitattributes`（SHA-256 `03cb93717fd024bb2c52904b56ffed975624cc543960f505fa8290a76ee550f8`，111 B）在磁盘和 git index 中均不存在；56 项 `sha256sum -c` 因此有 1 项失败。

### 6.3 上传包与固定 vendor 的差异

同名可比文件 39 个：31 个完全一致，8 个不同。另有 vendor 专属 7 个、上传包非资产专属 7 个（上传包另有 84 个资产文件未入 vendor）。

| 文件 | vendor 大小 / SHA-256 | 上传包大小 / SHA-256 | 差异性质 |
|---|---|---|---|
| `references/r-workflow.md` | 8070 / `ad5403b890801d8e350976d65462babcab2dc1f525351d20bc6136b92900d9d6` | 8107 / `99af2cf2d9b715ce009b5fe01034f7d22998276baae1324027b66de5b6e326d4` | 上传包改用显式 `svglite()/cairo_pdf()/agg_tiff()` + `print/dev.off()`，不再用 `ggsave(device=...)` |
| `scripts/audit_figure_collisions.py` | 27709 / `4bd020238a9f207c8a67c61d7209cef5959f2995a1d9cd95911d6fa93de7aa36` | 27878 / `4ee745c540355835944f33594833a6a87c44fca8d2232188d6c50a858bc33dcb` | 仅多 `sys.stdout/stderr.reconfigure(encoding="utf-8")` |
| `scripts/audit_pdf_text.py` | 8586 / `0b3d3e30ce9ec7191b8ba6dd053226487ab41594297005afcd127acda3b08281` | 9965 / `6d570cc8efe91825d6da156ac2599b1ef8fb61ce7074ddc9d1646aeb7a02d9d4` | 多 UTF-8 reconfigure；并新增 `Tc`/`Tw` 字符/词间距采集与 `spacing_issue_*` 报告字段 |
| `scripts/audit_svg_schematic.py` | 12722 / `8776cb9da07dc2ef6edf65c1ad23184567172b03685b880c78f3ef74d7241db8` | 12891 / `a6c37a081c640383214c78c0fe3aee7b28b50403fa8e94ba7c980bf59e1db452` | 仅多 UTF-8 reconfigure |
| `scripts/generate_openrouter_schematic.py` | 10586 / `0b8a9ac399be326fa1b575e800f5d7541516a7f1d0d7696dcc81096a3f4edb4b` | 10755 / `5be6ff8dd0a3df9cfcd95b124ef3f7cc278a3e9d8e335d8c832e6368d3a1244f` | 仅多 UTF-8 reconfigure |
| `scripts/nature_figure_backend.py` | 2737 / `ae15bc3f19217250edbbb8bfcb33227d185469f67e81071780369224e63d0dca` | 2906 / `9c3c031a57935443d34e183fb9414160694a1f289c5ee4bc69bdd211fef6b559` | 仅多 UTF-8 reconfigure |
| `scripts/plot_templates.py` | 29761 / `c5c042bc09464d58cc0363bc8542db946acb4c7efb53360a70386e1a6704691a` | 29930 / `e9a8b02768361910758f7188b35dab3e79b94b0783aea662017ef796ee7c736e` | 仅多 UTF-8 reconfigure（+169 B） |
| `scripts/validate_figure.py` | 33447 / `b9800e93dfa289eddd2e9438af755fcc6dd4a5f0f1da7778e4f999a91b69b042` | 33708 / `efe65ff2ed243a7c09819bf3093a7cc167f6e829fe31a6f3e6fa0d9c99d72d31` | 多 UTF-8 reconfigure；并把 `device=svglite` 写法纳入 SVG 检测 |

重点确认：

- `plot_templates.py`：差异仅为移除 5 行 `reconfigure` 块；模板逻辑、五个子命令、依赖和 QA 逻辑无差异。
- `audit_svg_schematic.py`：差异仅为移除同一 `reconfigure` 块；审计规则无差异。
- 上传包整体表现为较新的上游内容（含 Windows/UTF-8 兼容补丁与两处功能增强），而仓库 vendor 固定在 `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`。不能据上传包直接宣称等于固定 pin。
- 上传包非资产专属：`agents/openai.yaml`、`evals/evals.json`、`.gitignore`、`README.md`、`README_EN.md`、`tests/test_figure_safety.py`、`tests/test_schematic_url_safety.py`。
- vendor 专属：`LICENSE`、`INTEGRATION.md`、`provenance.json`、`skills/nature-shared/*`（4）。

### 6.4 五模板（实际源码函数与统计）

`skills/nature-figure/vendor/nature-skills/skills/nature-figure/scripts/plot_templates.py`：29,761 B，609 行，23 个函数，5 个 argparse 子命令。

| 子命令 | 实际函数 | 函数行 | 关键参数/保护 |
|---|---|---|---|
| `volcano` | `plot_volcano` | 281 | `--gene-col/--effect-col/--p-col`；正 p 值检查、显式阈值、保留全部点 |
| `roc` | `plot_roc` | 343 | `--fpr-col/--tpr-cols`；`[0,1]` 范围、FPR 排序、AUC 记录 |
| `dotplot` | `plot_dotplot` | 380 | `--row-col/--column-col/--size-col/--color-col`；完整类别顺序校验 |
| `marginal` | `plot_marginal` | 437 | `--x-col/--y-col/--group-col/--bins`；保留全部观测 |
| `paired` | `plot_paired` | 480 | `--id-col/--condition-col/--value-col`；恰好两个条件，拒绝重复/不完整配对 |

输出为 SVG/PDF/600 dpi TIFF + QA JSON；`--demo` 仅用于显式冒烟测试并在 QA 中标记；缺少数值默认停止，`--drop-incomplete` 才记录排除。

### 6.5 六基本类型

按上游 `references/api.md` 与 `references/chart-types.md` 的实际函数/章节归并：

| 类型 | 源码证据 | 可执行性 |
|---|---|---|
| 分组柱状图 | `make_grouped_bar`（api.md:237） | 文档代码片段；无独立 CLI |
| 折线/趋势图 | `make_trend`（api.md:299） | 文档代码片段；无独立 CLI |
| 森林图 | `make_forest_plot`（api.md:339） | 文档代码片段；无独立 CLI |
| 热图 | `make_heatmap`（api.md:371） | 文档代码片段；无独立 CLI |
| 雷达/极坐标 | `plot_radar`（chart-types.md:31） | 文档代码片段；无独立 CLI |
| 散点/气泡 | `make_scatter`（chart-types.md:202） | 文档代码片段；无独立 CLI |

布局与多面板架构（GridSpec、hero panel、panel alignment）属于架构能力，不计入上述六类图表函数。

### 6.6 能力矩阵

状态口径：`yes`=有文件/有静态入口；`no`=无入口或明确禁止/缺失；`not_run`=未执行、无运行证据；`n/a`=纯文档能力不适用。

| 能力 | installed | discoverable | callable | sandbox_ready | QA_verified | delivered |
|---|---|---|---|---|---|---|
| 六基本类型 | yes（api/chart-types 文档与代码片段） | yes（manifest 路由） | 仅经自由 Matplotlib/source；not_run | no（库执行无沙箱） | not_run | no |
| 五模板 | yes（plot_templates.py，5 子命令） | yes（template-catalog + manifest） | 入口存在；系统 Python 缺 numpy/matplotlib，not_run | no（库执行无沙箱） | not_run（无 demo/QA JSON 运行） | no |
| 自由 Matplotlib | yes（`--source`、`render_figure(source=...)`、`capture_figure`） | yes（ADAPTER/SKILL） | 入口存在；依赖未装，not_run | no（ADAPTER 明示无沙箱） | not_run | no |
| 图注/美学 | yes（figure-legend-conventions、design-theory、stance、qa-contract） | yes | 文档only；validate_figure 源码检查存在但 not_run | n/a | not_run | no |
| SVG/QA | yes（audit_svg_schematic.py 12,722 B/296 行/13 函数，仅标准库） | yes（manifest vector route、verification `svg_schematic`） | 仅经 `citrus_figures.nature_runtime` 注入 `math` 后可调用；直接 CLI 未证实 | no | not_run | no |
| 仅参考 R | yes（panel_alignment.R、r-workflow、static/fragments/backend/r.md） | yes | no（Rscript/R 均不存在） | no | not_run | no |
| 禁止执行的外部 AI | yes（generate_openrouter_schematic.py 10,586 B；OpenRouter 文档） | yes（manifest + SKILL 路由） | no（禁止执行；需网络与 `OPENROUTER_API_KEY`） | no | not_run | no |

### 6.7 SVG/QA 细节

- `audit_svg_schematic.py` 检查：最终物理字号（5–7 pt）、画布宽度（89/183 mm）、文字越界、`script`/`foreignObject`/渐变/滤镜/外部引用。
- 它**不检查**框重叠、箭头交叉、文字轮廓化；文档明确要求仍需最终尺寸人工目检。
- 固定 pin 的脚本在第 94 行调用 `math.cos/math.sin`，但源码未 `import math`。`packages/citrus_figures/nature_runtime.py` 在加载时向模块命名空间注入 `math`，并以 `pinned_svg_missing_math_import` 标记；直接 CLI 执行该固定文件未经本次验证，按源码应在该分支失败。
- `packages/citrus_figures/verification.py` 的 `svg_schematic` 明确记录 `schematic_rules_only=true`、`visual_semantics=false`；visual 未注入检查器时固定 `not_run/capability_absent`。

### 6.8 图注/美学与源码预检

`validate_figure.py` 的源码预检代码包含 21 类 finding：`SOURCE-SYNTAX`、`FONT-FAMILY`、`FONT-SIZE`、`FONT-GLYPH-FLOOR`、`LEGEND-LABEL-CASE`、`COLOR-MAP`、`EDITABLE-TEXT`、`EXPORT-VECTOR`、`EXPORT-RASTER`、`RASTER-DPI`、`FINAL-WIDTH`、`DATA-EXCLUSION`、`DATA-SAMPLING`、`DEMO-DATA`、`LOG-GUARD`、`INTERP-MONOTONIC`、`UNCERTAINTY-ENCODING`、`ROTATION-ANCHOR`、`ANNOTATION-WORKAROUND`、`BACKEND-EXCLUSIVE`、`PANEL-ALIGNMENT-GATE`。这是**代码能力证据**，本次未运行，因此不构成 QA 通过。

### 6.9 打包完整性缺口（关键）

1. 外层 `skills/nature-figure/provenance.json` 列出的 `vendor/nature-skills/.gitattributes` 不存在：56 项校验 1 项失败。
2. `02_config/skills.catalog.json` 的 `nature-figure` 清单为 57 项，但磁盘实际 56 项；缺失 `skills/nature-figure/tests/test_resources.py`（目录 `tests/` 整体不存在，git 也未跟踪）。`research_skill_catalog.py` 会逐项校验存在性与 SHA-256，因此该技能无法通过该 catalog 的完整性校验。
3. `01_src/src/prompts/skills/vendor/nature-skills` 是另一份旧 vendor 副本：与 `skills/nature-figure/vendor/nature-skills` 相比，`provenance.json` 不同，且缺少 4 个 peripheral 脚本。存在双 vendor 身份风险。
4. 固定 pin 的 `audit_svg_schematic.py` 缺 `import math`，仅靠包内注入兼容；任何绕过 `nature_runtime.load_module` 的直接调用都未证实可用。

### 6.10 两套固定 vendor 与 ZIP 执行脚本差分（复用优先级）

- A（canonical / 前向）：`skills/nature-figure/vendor/nature-skills`，46 个原始文件 = 44 个固定文件 + `INTEGRATION.md` + `provenance.json`。内层 provenance 44 项全部校验通过，含 4 个 peripheral：`panel_alignment.R`、`audit_svg_schematic.py`、`nature_figure_backend.py`、`generate_openrouter_schematic.py`。`packages/citrus_figures`、`skills/nature-figure/scripts/citrus/*`、`02_config/skills.catalog.json` 均指向 A；只有 A 支持 SVG 审计。
- B（legacy）：`01_src/src/prompts/skills/vendor/nature-skills`，42 个原始文件；内层 provenance 40 项全部校验通过。B 与 A 的所有同名文件字节一致，唯 `provenance.json` 不同；B 缺 A 的 4 个 peripheral。`01_src/src/figures/nature_runtime.py` 的 `_VENDOR_ROOT` 指向 B，其 `_MODULES` 为 `validate_figure/audit_panel_alignment/audit_pdf_text/audit_figure_collisions/figure_safety/plot_templates`，**不含** `audit_svg_schematic`；`01_src/src/prompts/nature_figure.py` 也从 B 读取指导文本；`01_src/src/figures/engine_bundle.py` 同时打包 B（`src/prompts/skills/vendor/...`，供 v1 worker receipt）与 A（`skills/nature-figure/vendor/...`，新独立资源）。
- ZIP 与 A/B 的执行脚本差分：B 的 6 个核心执行脚本与 A 完全同字节；ZIP 与 A 有 8 个文件不同（见 6.3），因此这组差异是两套固定快照共同面对的上游更新，不是 B 独有。`plot_templates.py` 与 `audit_svg_schematic.py` 仅多 UTF-8 reconfigure；`audit_pdf_text.py` 另加 `Tc`/`Tw` 间距采集；`validate_figure.py` 另加 `device=svglite` 检测；`r-workflow.md` 改用显式设备调用。
- 复用优先级：**新工作、SVG/QA、五模板与 F1 验收优先复用 A**。B 仅作为 v1 worker receipt 与旧 prompt 的冻结兼容副本保留，不向 B 增加新能力，也不要用 B 宣称 SVG 审计可用。不要把 ZIP 直接覆盖 A 或 B；若升级，先完成版本决策、重新 pin，并重算两层 provenance 与 catalog。
- 依赖复核（重点）：五模板 `plot_templates.py` 需要 `numpy` + `matplotlib`（Agg）；SVG 审计 `audit_svg_schematic.py` **仅标准库**（`xml.etree`、`re` 等），不需要 PyMuPDF；`audit_pdf_text.py` 仅标准库；PDF 碰撞审计需要 PyMuPDF。固定 pin 的 SVG 脚本第 94 行使用 `math` 但未导入，A 由 `citrus_figures.nature_runtime` 注入 `math`；B 不加载该脚本。

## 7. 兼容性

- 44 文件固定快照：**通过**（hash、数量、canonical inventory 指纹均一致）。
- 外层 provenance：**不通过**（缺 `.gitattributes`）。
- research skill catalog：**不通过**（缺 `tests/test_resources.py`）。
- 上传包与固定 pin：**不一致**（39 个同名文件 8 个不同）；不能把上传包视为 pin 的等价物。
- 运行依赖：仓库声明齐全，但当前系统 Python 缺 numpy/matplotlib/PyMuPDF；服务运行环境未验证。R 完全缺失。
- 双 vendor 副本：**不一致**（旧副本缺 4 个 peripheral 与不同 provenance）。
- 外部 AI 与 R：按设计仅参考/禁止执行，未发生任何网络或执行行为。

## 8. 剩余点（最多 5）

1. `vendor/nature-skills/.gitattributes` 缺失，外层 provenance 的自校验失败；需从固定 pin 恢复该文件，或删除该条目并重算外层 provenance 与 catalog。
2. `skills/nature-figure/tests/test_resources.py` 缺失，`02_config/skills.catalog.json` 的 nature-figure 清单无法通过；需恢复该测试或移除清单条目并重算。
3. 上传 ZIP 与固定 vendor 存在 8 文件漂移；需明确“继续固定 7bff1d0”还是“重新 pin 到上传包版本”，不能混用。
4. `01_src/src/prompts/skills/vendor/nature-skills` 旧副本与 canonical vendor 不一致，存在双身份与误加载风险。
5. 无运行证据：系统 Python 缺绘图/PDF 依赖、R 缺失、无 QA receipt/bundle；`audit_svg_schematic.py` 直接 CLI 的 `math` 缺口未消除。

## 9. 移交主会话 / F1

### 9.1 最小保留固定版本方案（建议，未执行）

保留：

- `skills/nature-figure/{SKILL.md, ADAPTER.md, RELEASE.md, provenance.json, provenance.extra.json, references/bridge.md, scripts/citrus/*}`。
- `skills/nature-figure/vendor/nature-skills/{LICENSE, provenance.json, skills/nature-figure/*, skills/nature-shared/*}`，严格保持 44 文件固定字节。
- `02_config/skills.catalog.json` 中 nature-figure 条目（修复后）。
- 根 `requirements-base.txt` / `pyproject.toml` 中的 figures 依赖声明。

不纳入交付：

- 上传 ZIP 的 84 个图片资产、`agents/`、`evals/`、`tests/`、README、旧 vendor 副本。
- R 执行、OpenRouter/AI 图像生成执行。
- 任何未固定的上游新版本文件。

修复后冻结顺序：

1. 恢复或移除 `.gitattributes` 条目，重算外层 provenance。
2. 恢复 `tests/test_resources.py`（catalog 记录的 SHA-256 为 `2afb110d77d3cdd09290726fb4c33329ddaa258a8bd5a925baa131502a5df84f`，5,740 B）或移除该条目并重算 catalog。
3. 确认唯一 vendor 根，废弃 `01_src/src/prompts/skills/vendor/nature-skills` 旧副本。
4. 在服务 venv 中安装 `numpy`、`matplotlib`、`PyMuPDF`；R 保持不安装。
5. 以固定 pin 的 44 文件哈希和内层 provenance 指纹 `a9b8e661...` 作为 F1 验收门槛；上传包仅作为差异参考，不直接替换。

### 9.2 移交结论

- 可确认：44 文件固定快照完整且自洽；五模板、自由 Matplotlib、SVG/QA、R 参考、外部 AI 边界在文件和接口层均已就位。
- 不可确认：任何能力的运行通过、sandbox_ready、QA_verified、delivered。全部缺少执行证据，按 `not_run` 记录。
- 阻断 F1：外层 provenance 缺文件、catalog 缺测试文件、上传包与 pin 漂移、双 vendor 副本、运行依赖未验证。

## 附录 A：44 个固定文件（路径 / 字节 / SHA-256）

| 路径 | 字节 | SHA-256 |
|---|---|---|
| LICENSE | 11357 | c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4 |
| skills/nature-figure/SKILL.md | 12367 | 5aefe708a2531c5ddaa8ee74d5155d747e96c0f21a33143d38316b7c5c88ffc4 |
| skills/nature-figure/assets/figures4papers/THIRD_PARTY_NOTICES.md | 982 | b0ef9d2fe959468fce4f45472b12ff4d4fc9450201caa42d394823cd4a5e20d5 |
| skills/nature-figure/manifest.yaml | 7782 | f6cf1e50f55c74e159134edb8ca5a57fd5097b72b37463c7b2612757e01c24be |
| skills/nature-figure/references/ai-graphical-abstract-workflow.md | 6263 | 258c59085ef3eaf28dcda8bf00bab5d3235f0736be1af652911de1af8aa14c4f |
| skills/nature-figure/references/api.md | 20759 | 1ec51840fdc45017735511b1275cada5e09f5d3e7dc44f35b2d8ba052809a4bc |
| skills/nature-figure/references/asset-adaptation.md | 5149 | 64c4f788962d73beba1bbd919255dffe620de3090579e7ec811e88e557ac4c3d |
| skills/nature-figure/references/backend-selection.md | 5937 | 9ff023b8103da861f2c6e4b8b244a0003120d180ab4fb004c346af5a12343dca |
| skills/nature-figure/references/chart-types.md | 11883 | 68008a60e0285e840a34973db5f945e88051280ac4dcad8a7eb97882c5384dfa |
| skills/nature-figure/references/common-patterns.md | 11668 | 5f176c70bb846744374a27a342d03761ff9c0a0c7844f87867add3a66c448581 |
| skills/nature-figure/references/demos.md | 3282 | dc34ab2d1600265f7e03af51afa0d4bda7b235f4cf3d2f73ac791dca8c605cec |
| skills/nature-figure/references/design-theory.md | 18102 | e8b31da9dec01f552acca6aeffbd06c8dbf53e737d7138075d1f97619c43a47e |
| skills/nature-figure/references/figure-contract.md | 5703 | db08b51fb080c5fe37f61140a1b7fd31247f90810f3a80cdcf58429a699879df |
| skills/nature-figure/references/figure-legend-conventions.md | 5165 | 7d00fc1839d5b152b289c8d892e723549be06c38ea75761763108a5ac34303fd |
| skills/nature-figure/references/multipanel-evidence-architecture.md | 12732 | 79ca617cc05088d6fcfb065140d0ee036eb849738bb79306964eb9aa3a4ed274 |
| skills/nature-figure/references/nature-2026-observations.md | 6270 | 1af8250733da77f1d82cc370e2e8d61266352517b1d1ab82ed6c834de8c98272 |
| skills/nature-figure/references/nature-article-requirements.md | 5822 | d59ebd80a99b2a91d9b0861529044f6242e1430b37fa4d7c73935a40a6a6be33 |
| skills/nature-figure/references/openrouter-image-generation.md | 5928 | 52a26512c9eabd040c95c5c97f6fba9b110cd4be5b41d667d372c489d11526c6 |
| skills/nature-figure/references/qa-contract.md | 24785 | 4f31375cdb614d74eddb8aa7691c0bdfc8a8f20defcca27ce4a36192308748a8 |
| skills/nature-figure/references/r-template-index.md | 3903 | 0ebbbd4171896b61639d7969a010401d0aa257bafb99783026a1ba32c91ce19e |
| skills/nature-figure/references/r-workflow.md | 8070 | ad5403b890801d8e350976d65462babcab2dc1f525351d20bc6136b92900d9d6 |
| skills/nature-figure/references/template-catalog.md | 2227 | f3dfe03d7ad6d004d17cb1f576e39020f55d1aa6d26f115a3be9ce6279e7d244 |
| skills/nature-figure/references/tutorials.md | 8728 | 0fbd92cedc8da312ec4b8655127d61ce2ba68f218b70a346d6716977ab4861d6 |
| skills/nature-figure/references/vector-schematic-style-rules.md | 7230 | fa96f0452b67deded2ce0437dc6bde7bedeae9dd438f18e22525d61953b32ee5 |
| skills/nature-figure/references/vector-schematic-workflow.md | 6629 | 9f96fa703bbe29581e50ddac9756900553b5617f46b4a0b31e8978278fffa329 |
| skills/nature-figure/requirements.txt | 23 | e2596dd9f3381fb6280bd18added6c3d0025d954c99a132e3d0bc8d3e8a24d00 |
| skills/nature-figure/scripts/audit_figure_collisions.py | 27709 | 4bd020238a9f207c8a67c61d7209cef5959f2995a1d9cd95911d6fa93de7aa36 |
| skills/nature-figure/scripts/audit_panel_alignment.py | 39469 | 4ed2d6afe9db45aaf6ca26aeb264cfc251ae92b49ee9fbf117d926e1956acf56 |
| skills/nature-figure/scripts/audit_pdf_text.py | 8586 | 0b3d3e30ce9ec7191b8ba6dd053226487ab41594297005afcd127acda3b08281 |
| skills/nature-figure/scripts/figure_safety.py | 2216 | 0e3e9601aacdafca32ef5fbf1967bdeec0abbf7f564edfd711875432bd47c212 |
| skills/nature-figure/scripts/plot_templates.py | 29761 | c5c042bc09464d58cc0363bc8542db946acb4c7efb53360a70386e1a6704691a |
| skills/nature-figure/scripts/validate_figure.py | 33447 | b9800e93dfa289eddd2e9438af755fcc6dd4a5f0f1da7778e4f999a91b69b042 |
| skills/nature-figure/static/core/contract.md | 6274 | a2b6523a48192cf1dff0d194264c5662c832f4d1da6e9e4763ccd98cc0664d4c |
| skills/nature-figure/static/core/stance.md | 4595 | c7e6860e955a2ee9c24a30dbe88e28741da4eef8290c019e849c6b220ea0f07a |
| skills/nature-figure/static/fragments/backend/python.md | 3535 | 1d4809e4ec4ed2d40995d74828732012a6a72c941dfec341745a3118e9312865 |
| skills/nature-figure/static/fragments/backend/r.md | 3299 | 4839a77846f0f71a7a44f077ee8fc665460033e7006cb18a884562712d667e2d |
| skills/nature-shared/core/ethics.md | 3424 | 065db5761d72c295eb1ed5c231deb0b0bcbb0f1065d530f994c044398bd75056 |
| skills/nature-shared/core/nature-results-discussion.md | 10288 | 16f1ce1ed6d2aad7853eabf304cdbd661530931747bce4cf3f10b3dfb655adc1 |
| skills/nature-shared/core/research-compliance.md | 7717 | 0ac172d2af0c2089acd7280712a89b23a5f232d4e436e9ae78dd501ff30cd855 |
| skills/nature-shared/journal-formats/nature-machine-intelligence.md | 18975 | d62a418fbd67841e745f553b98a267b5a01c9445457a07a0121095ce76fad0af |
| skills/nature-figure/scripts/panel_alignment.R | 6233 | aeb2db8355e2026e2f2640d25bf18edfa57fb0165ec2c7811f97bb75ba53d26b |
| skills/nature-figure/scripts/audit_svg_schematic.py | 12722 | 8776cb9da07dc2ef6edf65c1ad23184567172b03685b880c78f3ef74d7241db8 |
| skills/nature-figure/scripts/nature_figure_backend.py | 2737 | ae15bc3f19217250edbbb8bfcb33227d185469f67e81071780369224e63d0dca |
| skills/nature-figure/scripts/generate_openrouter_schematic.py | 10586 | 0b8a9ac399be326fa1b575e800f5d7541516a7f1d0d7696dcc81096a3f4edb4b |

## 附录 B：复现命令（只读）

```bash
# 基线
cd <candidate-repo>
git branch --show-current && git rev-parse HEAD && git status --short

# 44 文件校验
cd skills/nature-figure/vendor/nature-skills
jq -r '.files[] | "\(.sha256)  \(.path)"' provenance.json | sha256sum -c -
jq -cS '.files | sort_by(.path)' provenance.json | tr -d '\n' | sha256sum
# 期望：a9b8e661bf460a401e17017f134a735432da9d7832e3f561a63c6cf47fb92b9e

# 外层 provenance 与 catalog
cd ../../..
jq -r '.files[] | "\(.sha256)  \(.path)"' skills/nature-figure/provenance.json | sha256sum -c -
jq -r '.skills[]|select(.id=="nature-figure")|.files[].path' 02_config/skills.catalog.json | while read p; do test -e "skills/nature-figure/$p" || echo "MISSING $p"; done

# 差异
cd <candidate-root>
diff -u review/nature-figure/scripts/plot_templates.py repo/skills/nature-figure/vendor/nature-skills/skills/nature-figure/scripts/plot_templates.py
diff -u review/nature-figure/scripts/audit_svg_schematic.py repo/skills/nature-figure/vendor/nature-skills/skills/nature-figure/scripts/audit_svg_schematic.py
```
