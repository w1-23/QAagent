# 技术实现一致性报告（Round 1 · implementation_consistency_report.md）

> 方法：以 **L1 当前源码/配置**（`01_源码_src`、`02_配置_config`、启动配置）核对参考图与 WxRev 所描述的模块与调用关系；
> 状态六态：`IMPLEMENTED_ACTIVE` / `IMPLEMENTED_INACTIVE` / `MANUSCRIPT_ONLY` / `REFERENCE_ONLY` / `CONFLICT` / `UNVERIFIED`。
> 明细表：同目录 `module_status.csv`（23 行，逐行带源码证据）。
> **本轮只标注差异，不修改技术路线、不删除参考图中的模块。**

---

## 一、核对结论速览

| 参考图模块 | 状态 | 证据要点 |
|---|---|---|
| User question (CN) → Intent confirmation（1–3 候选） | ✅ IMPLEMENTED_ACTIVE | `intake.py`：`interpretations` 1~3 个、`AMBIGUITY_THRESHOLD`、`INTENT_WAIT_SEC=30` |
| Query rewriting（CN→EN，只译不改写） | ✅ IMPLEMENTED_ACTIVE | `two_section.py`「只判断与切分，绝不改写模型输出」；`web_evidence_pipeline.py`「只对齐，不改写」 |
| Local RAG Agent（语料/检索/精排/生成/编排） | ✅ IMPLEMENTED_ACTIVE | `search_both.py`、`agent_loop.py`、`evidence.py`；40+40 → RRF k=60 → top-10 |
| Web Agent（搜索/自判/清洗/分级） | ✅ IMPLEMENTED_ACTIVE（措辞需收敛） | `config.py` DeepSeek 原生 web_search；`source_tiers.py` |
| — 其中 "Official sites, databases, news, extension resources"（多源） | ⚠️ REFERENCE_ONLY | 代码是**单一服务端联网工具 + 分级标注**，未验证"多数据库检索" |
| — 其中 "Source tiering (T1/T2/T3, cannot support)" | ✅ IMPLEMENTED_ACTIVE（语义偏重） | `source_tiers.py` 注释：「tier **不得**用作丢弃闸门」；T3 占 60.2%、95.5% 为 default_un… |
| 本地链与联网链**并发** | ✅ IMPLEMENTED_ACTIVE | `search_both.py`：`local_res, web_res = await asyncio.gather(...)` |
| **不跨源仲裁**：联网不进引用池 | ✅ IMPLEMENTED_ACTIVE | `search_both.py`「不做跨源仲裁：联网内容不进引用池、不占编号」 |
| Assembly & audit：Stage A 代码硬门 | ✅ IMPLEMENTED_ACTIVE | `audit.py`「Stage A 代码硬门（确定性，零 LLM 成本，**先跑**）」；`expert_graph._stage_a_audit`（引用存在性/统计口径/时效共现） |
| Stage B 模型回审 | ✅ IMPLEMENTED_ACTIVE | `audit.py`「Stage B LLM 回看（review_with_llm，fast 模型 rubric）」 |
| 最小返修一次 | ✅ IMPLEMENTED_ACTIVE | `audit.py`「critical -> **1 次修复循环**；minor -> 标注」 |
| **Citation audit 作为独立第三阶段** | ❌ UNVERIFIED / 建议改画 | 引用核验属 **Stage A** 的检查项（`check_citation_ids`），未见独立阶段 |
| **Re-validate → Back to Stage A 回环** | ❌ UNVERIFIED | 未定位到"修复后回环到 Stage A 再校验"的显式代码路径；`expert_graph` 返回 `stage_a + stage_b 占位` |
| 编号重排 / 池外编号清除 | ✅ IMPLEMENTED_ACTIVE | `agent_loop.renumber_refs(...) → dropped`；`evidence.py` 死编号清除保 CiteExist |
| 联网独立命名空间 `[Wn]` | ✅ IMPLEMENTED_ACTIVE | `agent_loop.py`「联网 [Wn] = W{i+1}，上限 WEB_CITE_SLOT 唯一常量」 |
| Two-section output（`===== 联网小节 =====`，流式只第一段） | ✅ IMPLEMENTED_ACTIVE | `two_section.py`（v10.14）；`web_output_separator.py` |
| 增强包（联网过滤/回检闭环） | 🚫 IMPLEMENTED_INACTIVE | `enhance/flags.py`：`recheck: False`；与 RUNTIME_TRUTH「增强包关闭」一致 |
| Fig.2 "PDF/Markdown/Word → **MinerU** → Markdown" | ⚠️ REFERENCE_ONLY | `ingest.py`：`extract_text()` 用 **PyMuPDF(fitz) 全文提取**；全库 MinerU 仅 1 处命中且属无关参考论文 |

---

## 二、需要用户在第二轮前裁定的 4 项

1. **回环是否存在**：参考图有 "Re-validate → Back to Stage A if needed"，源码未见显式回环。
   → 请确认：(a) 实际实现确实是一次修复后再走一遍校验（只是没写成显式函数）；还是 (b) 图中应删除该回环箭头。
2. **Citation audit 的画法**：独立阶段（参考图）还是 Stage A 的检查项（源码）？
3. **MinerU**：语料 PDF 是否由**仓库外**工具预处理成 Markdown？若否，参考图与图注须删除 MinerU。
4. **多源联网表述**：是否要保留"official sites / databases / news"这类来源类型描述（当前实现仅能证明"服务端联网 + T1/T2/T3 分级"）。

---

## 三、与论文（WxRev）的一致性

| WxRev 表述 | 源码核对 |
|---|---|
| 「双链并发，生成阶段严格隔离、永不混池」 | ✅ 与 `search_both.py` 一致 |
| 「交付前代码校验分两段：第一段不消耗模型调用…第二段由快速模型按量表回审」 | ✅ Stage A / Stage B 顺序与成本描述一致 |
| 「任一段判为关键问题时，至多触发一次最小返修」 | ⚠️ 源码为 `critical → 1 次修复循环`（关键问题触发、一次）；**"返修后是否回环再校验"未证实** |
| 「联网条目不进入本地引用池、编号独立命名空间」 | ✅ 一致 |
| 「支持度校验不依赖模型自述：数值类论断要求所引文本块中确实出现该数值」 | ⚠️ 对应 `enhance/support_check.py`（Stage A1 数值硬门）**默认关闭** → 需要确认冻结批运行时该模块是否开启 |
| 「混合检索 RRF k=60 / 精排 top-K=10 / T=max(0.25, s_top×0.60)」 | ✅ 与 RUNTIME_TRUTH 现状口径一致 |

> ⚠️ `support_check`（数值硬门）与 `recheck`（回检闭环）在 `enhance/flags.py` 中**默认关闭**，
> 而 WxRev 2.3 把"数值类论断要求所引文本块中确实出现该数值"写成生效能力。
> 这是**论文声称 vs 当前开关状态**的潜在不一致 → 已登记，**待用户确认冻结批是否开启该模块**（若历史上开启，需以当时的配置为准）。
