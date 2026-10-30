# CitrusQA × HA 绘图项目 · 第一轮审计总结报告（Round 1）

> 交付目录：`E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures\00_audit\`
> 本轮性质：**资产、数据与 Skill 审计（只审计）**——不改论文、不重算最终统计、不出正式图。
> 审计基线：WxRev-1014（王霞 2026-10-14 改稿）+ 王自强修订版（同日）。
> **按任务书 §12，本轮结束即停机，等待用户审核后才进入第二轮（Fig.1 + Fig.2）。**

---

## 一、本轮产出清单（均已落盘）

| 文件 | 用途 | 规模 |
|---|---|---|
| `asset_inventory.csv` | 全项目资产清单（7 个区、11 类资产） | **4,206 行** |
| `source_hashes_baseline.csv` | 关键源文件 SHA-256 基线（证明本轮前后源文件零改动） | 96 个文件 |
| `figure_data_map.csv` | 逐图逐面板 → 数据文件/字段/口径/分母/代码出处 | 22 行 |
| `statistical_recomputability.csv` | 逐指标 A/B/C 可复算等级 | 12 行 |
| `source_conflicts.csv` | 版本/口径冲突登记 | 8 条 |
| `module_status.csv` | Fig.1/Fig.2 模块六态标注（带源码证据） | 23 行 |
| `icon_manifest.csv` | 图标素材与许可证登记（初版） | 9 行 |
| `missing_data_report.md` | 缺失数据清单（含 10 项补数待办） | — |
| `implementation_consistency_report.md` | 实现一致性分级与待裁定项 | — |
| `skill_availability_report.md` | 三个候选 Skill 的三态审计 + 第二轮技术路线 | — |
| `round1_scripts/` | 本轮全部探针脚本（可复跑复核；关键结论已转录进上列 CSV/MD） | 9 个脚本 |
| `reference_layout_verification.md` | 两份设计参考图的判读：版式可复制要素 + 出图前必须替换的示意值 | — |
| `source_assets/` | 归档的两份参考图（含 sha256） | 2 张 PNG |

> **补充（用户 2026-10-14 裁定）**：两份参考图**只作绘图效果参考（主要是图标素材与版式），不是数据来源**；
> 数据一律以本项目实测/论文冻结口径为准；每张图的叙事目标与原定一致。参考图中的数字与标签**不登记为源冲突**，
> 仅列入 `reference_layout_verification.md` 的"出图前替换清单"。

---

## 二、六条必答结论（按用户指定）

### 1. Figure 3 是否找到了逐题评分？—— **找到了（A 级）**
- `07_交付与文档_delivery_docs/03_图件_figures/英文_figures_en/_sources/judge_scores_kimi-k2.6.jsonl`（244 KB）与
  `judge_scores_glm-5.3.jsonl`（143 KB）。
- 字段：`blind_id, config, id, D1…D10, crux, reason, error`，各 **416 行**、**84 个题目 id**、盲评编号 `B00001…`。
- 配置映射与正文完全对上：

  | 论文 Track | 文件 config | kimi 均分 | glm 均分 |
  |---|---|---|---|
  | A | `citrusqa` | 4.2215 | 4.4022 |
  | B | `same_base_trackA` | 4.1979 | 3.7485 |
  | C | `same_base_trackB` | 3.6280 | 3.4062 |
  | D | `mainstream_trackA` | 3.9891 | 3.7156 |
  | E | `mainstream_trackB` | 3.5893 | 3.3318 |

- 因此 Fig.3 的**双热图与逐维配对差（含 MDD）可以直接复算**；逐维数字与正文一致（kimi D4 +0.3452、D6 +0.25、D10 −0.6190；glm D3 +0.9167、D4 +1.1190、D6 +0.8929、D10 −0.2619）。
- **两处必须写进图注**：① **Track D 只有 80 题**（其余 84）；② **D1/D2 仅 Track A 有值**，不参与不检索配置之间的跨配置比较。
- ⚠️ 唯一 **C 级**：正文"**十维合并**配对差 +0.6372 / +0.0173 与 MDD 0.1596 / 0.1643"**只在文本转储中出现**（README、`_scratch/*.txt`），无结构化产物或生成脚本 → 需人工确认"合并"是否含 D1/D2 及其空值规则，或改用逐维结果表述（冲突 C7）。

### 2. Figure 3 的两套分母是否已区分？—— **已查明，且必须逐面板标注**
- `exp1_summary.json` 明确：`stage = "2 实验1 跨语言 2×2（合成 96 题）"`、**`n_questions = 96`**、**`n_gold = 48`**；
- 另有 `by_anchor.A（带锚点，n=48）` 与 `by_anchor.Z（无锚点，n=48）` 两组。
- → **96（全部合成题）与 48（含金标的锚点子集）都是合法分母**，属"总体 vs 子集"关系，**任何一张图只能用其中一个并写明**。
- 逐题明细在 `experiment/13_评测管道_pipeline/results/retrieval/crosslingual_20260918/per_question.jsonl`（字段 `id/cell/retrieval_lang/rerank_lang/pool_gold/rank/n_passed/threshold/passed_ids`）。
- **系统层 84 题配置对照**（judge_scores）与**跨语言检索实验**（96/48 题）是**两个独立数据集**，本报告与数据映射表已分列，未合并。

### 3. Figure 2 是否找到了清洗管道源码与冻结清单？—— **部分找到（C 级为主）**
- 源码：`04_脚本_scripts/ingest.py`（入库/切块）、`01_源码_src/src/tools/readfile.py`（PDF 读取）、
  `data_archive/01_corpus/{pipeline,stats,batches}.md`（流程与批次记录）。
- 冻结清单：`07_交付与文档_delivery_docs/04_数据_data/冻结_freezed/freeze_manifest.json`
  （`freeze_label=v10_49_format_sidebar`、125 项、`corpus_fingerprint: "187441 → 一致: True"`）。
- ❌ **关键缺口**：论文口径的冻结产物（**4,906 份文档 / 126,494 块**及分项 103,089/10,018/13,137/250）**没有机读产物**；现有指纹是旧口径 187,441。
- ❌ **切块参数冲突**：`pipeline.md`（800 单词、10% overlap）vs 论文（Markdown 标题划分 + 递归字符二次切分，块长中位数 941）。
- ❌ **清洗各阶段的排除/丢失计数未落盘** → 该面板**只能画流程骨架，不能画数字**（任务书明确要求显示排除分支）。
- ⚠️ **MinerU 未在实现中出现**：`ingest.py` 是 `PDF → fitz 全文本`；全仓 MinerU 仅 1 处命中且属无关参考论文。

### 4. Figure 1 的每个模块是否与源码一致？—— **17/23 一致，4 项 UNVERIFIED，2 项措辞需收敛**
详见 `module_status.csv` 与 `implementation_consistency_report.md`。要点：
- ✅ 已证：`asyncio.gather` 双链并发、Stage A（代码硬门，先跑）→ Stage B（模型回审）、关键问题 → **1 次修复循环**、`[Wn]` 独立命名空间、池外编号清除、两段式输出与分隔符、编号重排。
- ❌ **UNVERIFIED（必须裁定）**：
  1. **"Re-validate → Back to Stage A" 回环**在源码中未定位到显式路径；
  2. **"Citation audit"** 在参考图中画作独立第三阶段，源码中属 **Stage A** 的检查项；
  3. "多源搜索（official sites/databases/news/extension resources）"——代码只证明"服务端联网 + T1/T2/T3 分级"；
  4. 参考图/口头表述中的 **MinerU**。
- ⚠️ **措辞收敛**：`source_tiers.py` 注释明确「**tier 不得用作丢弃闸门**」（T3 占 60.2%、95.5% 为 `default_un…`）→ 参考图 "T3 self-media (cannot support)" 的语义偏重，建议改为"标注来源层级，不作为拦截"。
- ⚠️ **论文 vs 开关状态**：WxRev 2.3 把"数值类论断必须所引块出现该数值"写成生效能力，对应 `enhance/support_check.py` 与 `recheck` 在 `flags.py` 中**默认关闭** → 需确认冻结批运行时的开关状态。

### 5. 核心口径冲突：**187,441（未去重）已废弃，论文口径为准**
- 冲突双方（已登记 `source_conflicts.csv` C1）：
  - 旧：`data_sources.py` 的 `CORPUS_BATCHES/L1/YEARS`、`data_archive/01_corpus/{batches,stats}.md`、`freeze_manifest.corpus_fingerprint` → **187,441 chunks / 4,992 篇 / 11 批次**（未去重）
  - 新：WxRev-1014 §2.2/§3.2 → **4,906 份文档 / 126,494 个文本块**
- **裁定（PI 2026-10-14）**：以论文为唯一权威；187,441 一律标 **DEPRECATED**，**禁止进入正式图表**（仅可在审计记录中作弃用说明）。
- 附随待办：`data_sources.py` 的常量与基于旧口径的语料图脚本需在第二轮前更新（本轮不改源文件）。

### 6. Skill 审计结论（三级）—— 三个候选均为 **Candidate**，本机能力为 **Available**
- `Techd81/SciFig`（MIT，Claude Code skill，121 图表类型）→ **Candidate**：未安装、未做兼容性验证，**不得声称已兼容 DSH**。
- `tvhahn/matplotlib-skill`（Claude Code skill，38★，含 `SKILL.md`/`style-reference.md`）→ **Candidate**：可作**风格参考**，作为执行器未验证。
- `lilingm963/scifig-ai-scientific-figure-skill` → **Candidate（仅规划/提示词）**：自身不渲染，依赖外部工具 → 不满足矢量图产出要求。
- 网络检索到的 `scifig.com`/`scifig.ai` 是**商业 SaaS**，与上述开源仓库不是同一事物。
- **实际执行路线（已可用）**：Matplotlib（统计图）+ python-pptx/**PptxGenJS**（Node v24.21.0 可用）（结构图）+ 合法授权 SVG 图标 + LibreOffice 渲染校验。

---

## 三、逐图"能否开始第二轮"结论

| 图 | 能否开始第二轮 | 前提/限制 |
|---|---|---|
| **Fig.1** 系统架构 | ✅ **可以（有条件）** | 先在第二轮开工前裁定 4 项 UNVERIFIED（尤其"回环"与"Citation audit"）；(a) 面板的语料数字须待 M-1/M-2 落实后才能上数字 |
| **Fig.2** 清洗与知识库 | ⚠️ **只能先画"无数字骨架"** | 流程可画（源码+WxRev 已证）；**数字面板（4,906/126,494 及各阶段计数）需 M-1/M-3/M-4 落实**；MinerU 字样在裁定前不得出现 |
| **Fig.3** 检索与比较 | ✅ **可以** | 逐题与逐维数据齐（A 级）；两处分母须逐一标注；Track D n=80；"十维合并"表述待 M-5 确认 |
| **Fig.4** 质量/引用/联网 | ⚠️ **可做 (a)(c) 描述性，不做新 CI**；**(b) 需先定口径** | (a) 分母语义须写清；(b) 本地逐论断支撑记录缺失（M-7）；(c) 3.1% 不得归因隔离机制、n=96/48 语义待确认（M-6） |
| **Fig.5** 真实案例 | ⚠️ **两例可做，时效例待定** | 黄龙病（real-013）与砧木（real-051）已定位；**时效例（农残限量）在 real132 中无匹配提问**（M-9） |

---

## 四、需要用户裁定/提供的事项（阻塞项）

**A. 必须裁定（影响第二轮画法与图注）**
1. "Re-validate → Stage A" 回环是否存在？→ 保留箭头 or 删除？
2. "Citation audit" 画作独立阶段还是 Stage A 子项？
3. MinerU 是否为仓库外预处理工具？→ 保留 or 删除该字样？
4. "十维合并"配对差的定义（是否含 D1/D2、空值规则）？
5. 联网五条件 n=96 与 n=48 的聚合语义（是否 48 题 × 2 判官）？
6. WxRev 2.3 的"数值硬门"在冻结批运行时是否开启？

**B. 必须提供（影响能否上数字）**
7. 去重后冻结语料的**清单/指纹产物**（4,906 / 126,494）与**类别分项**机读表；
8. 清洗管道**各阶段排除/丢失计数**；
9. 当前冻结语料的**真实切块参数**；
10. **时效案例（农残限量）**的真实会话或指定截图来源。

**C. 参考图相关（只影响画法，不影响数据）**
13. 图标风格二选一：**Lucide 线描**（ISC，易改色、与统计图更协调）或 **Material Symbols 实心**（更贴近参考图的"实心插画"观感）——**禁止两者混用**；逐模块映射见 `icon_manifest.csv`（34 行）。
14. Fig.2(B) 的组成面板形态：文档级饼图（需补数据）或 chunk 级类别（数据已有）。
15. Fig.1 审计段的画法：Citation audit 是否独立成阶段、Re-validate 回环是否保留。

**C. 可选加强**
11. 本地侧**逐论断**引用支撑/事实性核验记录（Fig.4b 的独立口径）；
12. 三池 e2e 的逐题判官评分行（用于复算池间区间）。
---

## 五、源文件完整性核验（本轮前后零改动证明）

审计开始时对 96 个关键源文件（WxRev 修订稿、系统层统计文件、判官逐题评分、联网五臂汇总、
冻结清单、绘图布局脚本、权威文档等）建立 SHA-256 基线（`source_hashes_baseline.csv`）；
审计结束后**逐一重算比对**，结果：

| 项 | 结果 |
|---|---|
| 基线文件数 | 96 |
| **未改动（哈希一致）** | **96** |
| 已改动 | **0** |
| 丢失 | **0** |

→ 本轮对项目原始文件与历史产物**零写入、零覆盖、零移动、零删除**；
所有新建产物均在 `CitrusQA_HA_Figures/00_audit/` 内。

---

## 六、纪律声明

- 本轮**未修改任何论文正文、参考文献、实验数据或绘图脚本**；源文件读写状态与 SHA-256 基线见 `source_hashes_baseline.csv` 与第五节核验结果（96/96 未改动）。
- 审计目录内新建文件全部位于 `CitrusQA_HA_Figures/00_audit/`。
- 参考图中的**模拟统计值、示例 DOI、示意误差线**一律未采信、未复用。
- 视觉排版未目检（本环境读图受限），结论均基于结构、字段与源码机检。
