# -*- coding: utf-8 -*-
"""Record the 2026-10-16 Fig.1 flow fixes in the README + delivery report + handover."""
from pathlib import Path

F = Path(r"E:\codex_WORKSPACES\Citrus_QA_Agent\CitrusQA_HA_Figures")

readme = F / "Fig1_system_original" / "README.md"
t = readme.read_text(encoding="utf-8")
t = t.replace("## 六、已知项（如实记录）", """## 五·补、2026-10-16 流程修正 + 字体统一（PI 逐条意见）

| # | PI 意见 | 处理（依据） |
|---|---|---|
| 1 | Local RAG → Audit 的箭头起点应在**分支输出端**，不是 Rerank 高度 | 改为**容器输出端口**：箭头从容器右侧底部（y=15.69 cm）出发 → 走两列之间的空隙（x=5.25）→ 在审计容器中线（y=10.22）进入 |
| 2 | Web Agent → Audit 同理，应从整条联网流程结束后引出 | 镜像处理（右空隙 x=11.75）。**并把联网内部 5 个模块加上顺序箭头**，第 2→3 步标 "if insufficient"（judge 条件触发，`web_agent.max_rounds=2` 硬上限） |
| 2b | 联网内部顺序要**与真实代码一致** | 按 `web_evidence_pipeline.LAYER_ORDER = (fetch, tiers, content, meta, embed, adjudicate, rerank)` 把 **tier tagging（L1）提到 cleaning & metadata（L2–L3）之前**；T3 文案由 "cannot support" 改为 "(labels only)"（`source_tiers.py`：tier 不作弃用硬门） |
| 3 | Supervisor 与 Assembly & audit 的控制关系要明确 | 新增**中轴控制箭头** Supervisor orchestrator → Assembly & audit（直线，不与其他线交叉）；两路输出端口汇入审计容器，再下接 Supervisor final — 即 协调两路 → 汇集 → 审计 → 最终交付 |
| 4 | 实现细节标签逐个核对 | 见表下；`output to [4]` → **"to Supervisor final"**；Section 2 "never pooled with local" → **"not in the local citation pool"**（指最终引用池/输出边界，非内部完全隔离） |
| 5 | 字体统一 Arial（标题 Bold / 正文 Regular），不得靠缩小字号塞细节 | matplotlib 端 `font.family → ["Arial","Helvetica","DejaVu Sans"]`；PPT 端 `FONT_LATIN → "Arial"`，并修掉原始孪生把英文也用中文字体（SimSun）渲染的默认分支。字号一律未改 |

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

## 六、已知项（如实记录）""")
readme.write_text(t, encoding="utf-8")
print("README updated")

# delivery report
p = F / "FIGURE_DELIVERY_REPORT.md"
t = p.read_text(encoding="utf-8")
t = t.replace("| Fig.3 | 跨语言检索 + 五配置判官热图 + A−B 配对差 | 174.0 × 132.0 |",
              "| Fig.3 | 跨语言检索 + 五配置判官热图 + A−B 逐维配对差 + **Mean (D3–D10)** | 174.0 × 142.0 |")
t = t.replace("`Fig3/fig3_retrieval.svg`（199 文本）",
              "`Fig3/fig3_retrieval.svg`（9 行森林图：D3–D10 + Mean (D3–D10)）")
t = t.replace("## 二·补·1、Fig.1 现行版本（2026-10-16 回滚 + 配色）",
              """## 二·补·0、2026-10-16 第二轮修订（PI 逐条意见）

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

## 二·补·1、Fig.1 现行版本（2026-10-16 回滚 + 配色）""")
p.write_text(t, encoding="utf-8")
print("delivery report updated")

# handover
p = F / "MIGRATION_HANDOVER_会话迁移交接.md"
t = p.read_text(encoding="utf-8")
t = t.replace("## 七·补·0、Fig.1 现行版本 = 原始版式（2026-10-16）",
              """## 七·补·-1、2026-10-16 第二轮修订要点（迁移必读）

* **Fig.1**：原始版式不变，按 PI 意见加/改箭头与标签、字体统一 Arial；
  机检脚本 `Fig1_system_original/_layout_check.py`（应输出 FAILURES: 0）；
  三条覆盖层都堆在 `flow_layout.py` 末尾（`_apply_pi_palette` → `_apply_flow_fixes`），
  重跑顺序：`_setup_original_pipeline.py` → `_patch_second_pass.py` → `_add_flow_fixes.py` → 出图。
* **Fig.3e/f**：森林图为 **D3–D10 + `Mean (D3–D10)`** 九行，点估计 + 95% CI + 零线，
  **无 MDD 灰带/判定**；数值来自 `statistics/recompute_forest_D3_D10.py`
  （glm +0.5804、kimi +0.0789）。论文旧的 +0.6372/+0.0173 是"各自可用维度"口径，需同步更正。
* 已核对：阈值公式、早停参数、联网轮次、BM25/reranker/embedding 模型名均与源码一致（见 README §五·补）。

## 七·补·0、Fig.1 现行版本 = 原始版式（2026-10-16）""")
p.write_text(t, encoding="utf-8")
print("handover updated")
