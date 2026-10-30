# 并行全文抓取：去重 / 筛选 / 上下文预算 —— 函数级修改方案

> 状态：**方案稿，未改行为**。落地前先在本机与 staging 验收，生产 8000 不动、不 push。
> 依据：`b2fd9ae` 已查明的 479,708 字符 ToolMessage（`deepseek_fetch_paper` 两篇 OA 论文全文合并），
> 以及 `src/core/tool_results.py::clamp_for_model` 已加的 40k 兜底。

## 0. 现状审计（`src/tools/deepseek_search_fetch.py::deepseek_fetch_paper`，336–488 行）

| 环节 | 现有实现 | 缺口 |
|---|---|---|
| 候选构造 | `urls[:5]` + DOI 跳转 + Europe PMC 元数据，`dict.fromkeys` 只按**原字符串**去重（348–369） | `https://doi.org/10.3390/plants12010160` 与 `https://www.mdpi.com/2223-7747/12/1/160?utm_source=x` 视为两条，重复抓取 |
| 并发抓取 | `WEB_FETCH_EXECUTOR` + `wait(deadline=20s)`（387–406） | 无内容级去重 |
| PMC 二次展开 | 由元数据解析 PMCID 后抓 HTML/XML/OA-PDF（408–469） | 同一篇论文的 HTML 与 XML **都算 success**，正文重复 |
| 组装 | 所有非元数据成功页 **全文** 顺序拼接（471–488） | 无相关性筛选、无总量预算；单轮多次调用各写一份 |
| 失败处理 | `record_fetch_failures` 已存在（472–473） | 只记账，**不阻止**下一轮对同一 DOI 的无意义重抓 |
| 统计 | 只有 "N succeeded / M failed" 一行 | 无候选数、去重数、进模型字符数 |

## 1. 新增纯函数（先加函数与测试，不动调用点）

```python
# src/tools/deepseek_search_fetch.py
def _canonical_url(url: str) -> str:
    """小写 scheme/host、去 fragment、去 utm_*/gclid/fbclid、去尾部斜杠、doi.org 统一。"""

def _paper_key(doi: str, url: str, title: str) -> str:
    """优先 DOI（规范化小写、去 https://doi.org/ 前缀）；否则 canonical URL；
    都没有才退回 title 的归一化哈希（仅用于“同标题同来源”这种强信号）。"""

def _content_fingerprint(text: str) -> str:
    """正文指纹：去标签/空白 → 取前 2000 字符 + 全长 → sha256。
    只用于判断“同一篇论文的不同网页版本”，不做相似度合并。"""

def _dedupe_pages(pages: list[dict]) -> tuple[list[dict], list[dict]]:
    """按 _paper_key 与 _content_fingerprint 归并；同 key 保留“最优版本”：
    优先级 = 有 DOI > 正文更长 > XML/HTML 全文本 > PDF 文本。
    返回 (kept, merged)，merged 记录被合并的 URL 与未入选原因（可追溯，不静默丢弃）。"""

def _select_relevant_passages(text: str, query: str, budget: int) -> tuple[str, dict]:
    """按段落/章节切分并打分（query 词重叠 + 命中 DOI/图表编号加权），
    保留章节标题与定位；budget 用尽即停。打分不可用时降级为「头 60% + 尾 40%」。"""

def _fetch_stats(candidates: int, attempts: list[dict], merged: list[dict],
                 chars_raw: int, chars_model: int) -> dict: ...
```

## 2. 调用点改动（`deepseek_fetch_paper` 内部，逐段最小改）

1. **请求前去重**（替换 369 与 380 行的 `dict.fromkeys`）：
   `seen = set()` + `_paper_key(doi_value, url, title)`；同一 key 只派发一次，其余进 `attempts` 标 `skipped: duplicate_candidate`。
2. **抓取后去重**（插在 407 行 `pages = [...]` 之后）：
   `kept, merged = _dedupe_pages(pages)`；`merged` 写进 `fetch_attempts`（`ok=False, error="merged into <url>"`）。
3. **相关性筛选 + 单条预算**（替换 478–485 的拼接循环）：
   每条 kept 页 → `_select_relevant_passages(content, query, per_page_budget)`，
   `per_page_budget = min(16000, remaining_turn_budget // max(1, len(kept)))`；
   片段必须带 `DOI/Section/Table n/Figure n` 之一，否则整段丢弃（避免只剩自然语言摘要）。
4. **单轮总预算**（新增 `src/core/tool_results.py::turn_budget_remaining()`，
   ContextVar 由 `general_agent_graph._run_regular_calls` 在轮开始时置 `TURN_TOOL_BUDGET=120_000`）：
   每次调用先 `take(min(40_000, remaining))`，再组装；`clamp_for_model` 继续做最后一道保险。
5. **失败不重抓**（复用 `src/core/web_failure_ledger.py`）：
   新增 `should_skip(key) -> str | None`，对 `auth/403/404/permission/blocked` 这类**确定性**失败直接跳过；
   超时类允许一次重试。
6. **统计出口**：返回体 `meta` 增加 `fetch_stats`（候选数/派发数/成功数/失败数/去重合并数/
   进模型片段数/原始字符/进模型字符），工具文本首行改为一行紧凑摘要，明细仍进 `fetch_attempts`。

## 3. 测试清单（`src/tools/test_fetch_paper_dedupe.py`，全部 mock，不打网络）

1. 同一 DOI 的两个 URL → 只抓一次
2. 相同正文的多个镜像 → 合并为一条，merged 记录可查
3. 相似标题但不同 DOI → 两条都保留
4. 正文 + 补充材料 → 不合并
5. 两篇完全不同的长论文 → 各保留、总量受预算约束
6. 全部失败 / 部分成功 → 状态准确
7. 无 DOI 的网页 → 按 canonical URL 去重
8. 同轮多次调用累计超预算 → 第二次起被截断且记 `budget_exhausted`
9. 用户明确要求全文解读 → 走分章节路径，不被相关性过滤砍成摘要
10. 旧历史超长消息 → 仍由 `clamp_for_model` 兜底（回归）

## 4. 验收指标（真实两篇样本 + 脱敏文本）

| 指标 | 现状 | 目标 |
|---|---|---|
| 同篇论文重复抓取 | 有（DOI + publisher URL） | 0 |
| 进模型字符（两篇 OA 全文场景） | 479,708 | ≤ 32,000，且保留 DOI/图表定位 |
| 候选→派发比 | 无统计 | 记录并可复算 |
| 失败重抓 | 无阻止 | 确定性失败 0 次重抓 |
| 证据完整性 | 全文进证据库 | 不变（证据库仍存全文，模型只看片段） |

## 5. 回滚与发布

- 新逻辑用开关 `CITRUS_FETCH_DEDUPE=1`（默认 off）包裹；off 时行为与 `b2fd9ae` 完全一致。
- `b2fd9ae`（40k 兜底）与本次改造**分开验收、分开部署**：前者是安全修复，后者改的是模型看到的科研证据。
- 回滚：删开关即回到旧路径；证据库与历史数据不迁移、不删除。

## 6. 明确不做

- 不做跨轮次的全局论文缓存（会引入状态与失效问题）。
- 不用标题相似度删除资料（不同研究可能同名）。
- 不为每篇论文增加付费模型调用（相关性用词重叠 + 已有检索评分）。
