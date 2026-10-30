# 文献入库审核（管理员后台）— 审计与最小实现方案

> 状态：**方案（未动代码）**。按你的要求先审计现状、给出最小修改方案，确认后再实现。
> 目标：问答中抓取到的论文 → 管理员后台候选队列 → 批准/拒绝/重审 → 批量入库为**新一代语料** → 可整批回滚；**永不覆盖已有语料**；仅管理员可操作，服务端校验。

---

## 1. 现状审计（可复用的部分比预想多）

| 能力 | 现状 | 位置 | 复用判断 |
|---|---|---|---|
| 语料导入器 | **已存在**，事务式：manifest 校验 → 切块 → 建新 generation → 校验 → 原子切换 `active_index.json` | `src/retrieval/corpus_admin.py::ingest_main()`（506–670） | **直接复用**，不新写切块/嵌入 |
| 回滚 | **已存在**：`activate_main(--generation)` 切回旧 generation；`rollback_main` | `corpus_admin.py:773` / `:737` | **直接复用**，回滚=一次指针切换 |
| 并发/互斥 | 文件锁 `.corpus_admin.lock` + `_serialize_mutations` 装饰器 | `corpus_admin.py:78,103` | 复用 |
| 管理员鉴权 | `require_admin(request)` → `src.auth.require_admin(user)`；6 个 admin 端点已用 | `src/api/main.py:235,691–737` | 复用 |
| 管理员审计日志 | `admin_audit` 表已在用 | `src/api/main.py` | 复用记审核动作 |
| 管理员页面 | `web/admin.html` + `web/assets/admin.js` 已存在 | — | 加一个「文献审核」页签 |
| 工具结果证据库 | `tool_results.record()` 写 `tool_call_id/…/content`（200k 首尾截取） | `src/core/tool_results.py:138` | **只做索引**，全文另存 |
| 抓取胜出结果 | `_race_candidates()` 返回 winner dict（`title/url/content/format`），在 `deepseek_fetch_paper` 内 | `src/tools/deepseek_search_fetch.py:514,584–601` | 从 `general_agent_graph.py:804` 的 `record()` 处旁路取 |
| 文献库目录结构 | `agent/data/{cqa_*}` + `corpus_generations/` + `lancedb_generations/` + `active_index.json` | — | 不变；每次入库 = 一个新 generation |

**结论**：不需要新写导入管道。要新增的只是「候选队列 + 审核状态 + 批量触发 + 权限/审计接线」，约 3 个文件 + 1 张表 + 5 个端点 + 1 个页签。

---

## 2. 数据模型（新库，不动现有 6 个库）

`agent/state/corpus_review.sqlite3`

```sql
CREATE TABLE IF NOT EXISTS corpus_candidates (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL,
  status         TEXT NOT NULL DEFAULT 'pending',   -- pending|approved|rejected|ingested|failed
  doi            TEXT NOT NULL DEFAULT '',
  title          TEXT NOT NULL DEFAULT '',
  journal        TEXT NOT NULL DEFAULT '',
  year           TEXT NOT NULL DEFAULT '',
  url            TEXT NOT NULL DEFAULT '',
  source_kind    TEXT NOT NULL DEFAULT '',          -- fetch_paper|upload|manual
  providers      TEXT NOT NULL DEFAULT '[]',        -- 竞争来源与各自状态
  char_count     INTEGER NOT NULL DEFAULT 0,
  content_sha256 TEXT NOT NULL DEFAULT '',
  fulltext_path  TEXT NOT NULL DEFAULT '',          -- 全文落盘位置（不写入 ToolMessage）
  requested_by   INTEGER NOT NULL DEFAULT 0,        -- 发起提问的用户
  session_id     TEXT NOT NULL DEFAULT '',
  request_id     TEXT NOT NULL DEFAULT '',
  decided_by     INTEGER NOT NULL DEFAULT 0,
  decided_at     TEXT NOT NULL DEFAULT '',
  batch_id       TEXT NOT NULL DEFAULT '',
  note           TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_candidates_doi   ON corpus_candidates(doi)   WHERE doi <> '';
CREATE        INDEX IF NOT EXISTS ix_candidates_status ON corpus_candidates(status, created_at);

CREATE TABLE IF NOT EXISTS corpus_batches (
  id            TEXT PRIMARY KEY,
  created_at    TEXT NOT NULL,
  status        TEXT NOT NULL,                      -- applying|applied|failed|rolled_back
  generation    TEXT NOT NULL DEFAULT '',           -- 入库后产生的 generation
  prev_generation TEXT NOT NULL DEFAULT '',         -- 回滚目标
  corpus_ids    TEXT NOT NULL DEFAULT '[]',
  candidate_ids TEXT NOT NULL DEFAULT '[]',
  note          TEXT NOT NULL DEFAULT ''
);
```

**全文落盘**：`agent/data/_review_queue/<candidate_id>/fulltext.md`（+ `meta.json`）。
不入 `tool_results`（那里是 200k 截取副本），入库时由导入器切块；入库成功后目录标记 `ingested/`，保留以便审计与重跑。

**唯一性**：按 DOI 去重（同 DOI 已有 `pending|approved|ingested` → 不再新增，只累加 `providers`）。

---

## 3. 流程与函数级改动

### 3.1 自动候审（抓取成功即入队，不改主循环）

- `src/graph/general_agent_graph.py:804`（`_tool_results.record(...)` 之后）加一行旁路：
  ```python
  maybe_capture_candidate(tool_name, result_artifact, session_id, request_id, user_id)
  ```
- 新文件 `src/core/corpus_review.py`：
  - `maybe_capture_candidate(tool_name: str, artifact: Any, *, session_id: str, request_id: str, user_id: int) -> dict | None`
    - 仅当 `tool_name == "deepseek_fetch_paper"` 且 artifact 含 `content` 且 `len(content) >= 2000` 且能解析 DOI/标题；
    - 写全文到 `_review_queue/`，sha256 记库，状态 `pending`；
    - **任何异常都吞掉并记日志**（审核链路不得影响问答结果）。
  - `list_candidates(status, limit, cursor) -> dict`
  - `decide(candidate_id: int, action: str, *, admin_user_id: int, note: str = "") -> dict`
    - `approve`：`UPDATE ... SET status='approved' WHERE id=? AND status='pending'`（`rowcount==0` → 返回当前状态，**幂等**）；
    - `reject` / `reopen`（rejected|failed → pending）同理。
  - `apply_batch(*, admin_user_id: int, candidate_ids: list[int] | None = None) -> dict`
    - 取所有 `approved` 候选 → 建 `_review_queue/_batch_<id>/` 汇总目录（保留各文件原名 + `manifest.json`）；
    - 调导入器（子进程，避免阻塞事件循环）：
      ```
      python 04_脚本_scripts/ingest.py --dir <batch dir> --corpus-id cqa_literature_admin_<id>
        --family-id literature --display-name "管理员入库 <date>" --activate --cpu
      ```
    - 成功后把候选置 `ingested` 并写 `batch_id`；失败置 `failed` 并保留错误摘要（**不切换索引**）。
  - `rollback_batch(batch_id: str, *, admin_user_id: int) -> dict`
    - 调 `python -m src.retrieval.corpus_admin activate --generation <prev_generation>`（或 `rollback`），成功后批次 `rolled_back`，候选回到 `approved`（可重试）。

### 3.2 端点（全部 `require_admin`）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v2/admin/corpus/candidates?status=pending&limit=50` | 分页列表（含 DOI/标题/来源/字符数/时间） |
| POST | `/api/v2/admin/corpus/candidates/{id}/approve` | 幂等批准 |
| POST | `/api/v2/admin/corpus/candidates/{id}/reject` | 拒绝（可带备注） |
| POST | `/api/v2/admin/corpus/candidates/{id}/reopen` | 重审 |
| POST | `/api/v2/admin/corpus/batches` | 批量入库（approved → 新 generation → 激活） |
| GET | `/api/v2/admin/corpus/batches` | 批次列表与 generation |
| POST | `/api/v2/admin/corpus/batches/{id}/rollback` | 整批回滚 |

非管理员访问以上任一 → **403**（服务端 `require_admin`，前端只做显示隐藏，不作为权限控制）。

### 3.3 前端

`web/admin.html` 加页签「文献审核」+ `web/assets/admin.js` 加渲染：
- 列表：标题 / DOI / 来源数 / 字符数 / 抓取时间 / 状态按钮（批准·拒绝·重审）；
- 顶部：`待审 N · 已批准 M`，按钮「批量入库」（显示将入库的条数）；
- 批次区：每批显示 generation + 「回滚」；
- 内联预览（复用现有 markdown 渲染），不下载全文。

---

## 4. 幂等与并发（你特别要求的两点）

1. **重复点击批准**：状态机迁移用条件更新 `WHERE status='pending'`，第二次点击 `rowcount=0` → 直接返回当前状态与批次，不重复入库。
2. **重复点击批量入库**：整个 `apply_batch` 包在文件锁 + DB 事务里；入库成功后才把候选置 `ingested`，中途失败不改状态（可重试）。
3. **同一 DOI 多来源**：候选唯一键是 DOI；批准后再抓到同 DOI 只更新 `providers` 与时间，不产生第二个候选。

---

## 5. 不覆盖已有语料（机制保证，不是约定）

- 导入器每次创建**新 generation 目录**（`corpus_generations/g<ts>_<hash>`），旧目录只读保留；
- 激活是**原子替换** `active_index.json` 指针（`_activate_pointer`），旧 generation 仍可被 `activate --generation` 切回；
- 回滚 = 指针切回 + 批次记录状态，**不删除**任何语料文件；
- 导入前校验 corpus_id 不与既有 `cqa_*` 冲突（新批次用 `cqa_literature_admin_<batch>` 命名空间）。

---

## 6. 测试计划（不调真实模型）

| 用例 | 断言 |
|---|---|
| 抓取成功入队 | 1 条 `pending`，全文落盘，sha256 一致，`tool_results` 未被改写 |
| 同 DOI 再抓 | 仍 1 条，`providers` 增加 |
| 批准幂等 | 连点 2 次 → 1 次状态迁移，第二次返回同批次 |
| 拒绝→重审 | 状态 `rejected` → `pending` |
| 非管理员调用 | 4 个端点全 403 |
| 批量入库（假导入器） | 生成新 generation 目录 + 指针切换 + 候选 `ingested` + 批次 `applied` |
| 入库失败（假导入器抛错） | 候选 `failed`、指针**未**切换、批次 `failed` |
| 回滚 | 指针回到 `prev_generation`、批次 `rolled_back`、候选回 `approved` |
| 并发批准 | 两线程同时批准 → 只有一次迁移 |

真实一次端到端（staging）：抓一篇新 DOI → 入队 → 批准 → 批量入库 → 检索能命中该篇 → 回滚后不再命中。

---

## 7. 风险与边界

| 风险 | 处置 |
|---|---|
| 嵌入耗时（单篇约 10–20 s；批量线性增长） | 批量入库放子进程 + 进度事件；不阻塞问答 |
| 入库期间内存（CPU 8 线程峰值约 1.6 GiB） | 与主服务同机时限制单次批量条数（默认 ≤20），必要时 `--cpu` |
| 抓取的 HTML/PDF 质量参差 | 入队时只保留「正文 ≥2000 字符且能解析 DOI 或标题」；其余不入队 |
| 版权/许可 | 只入队公开全文；批次记录来源 URL 与时间，便于追溯与撤回 |
| 误批准 | 回滚按批次整批撤，不做单篇删除（避免索引与目录不一致） |
| 审核动作审计 | 复用 `admin_audit` 记 `approve/reject/apply/rollback` + 操作者 + 批次 |

---

## 8. 改动清单与工作量

| 文件 | 类型 | 规模 |
|---|---|---|
| `src/core/corpus_review.py` | 新增 | ~320 行（候选 CRUD、批量、回滚、锁） |
| `src/api/main.py` | 改 | +7 端点 ~120 行 |
| `src/graph/general_agent_graph.py` | 改 | +3 行（旁路捕获） |
| `web/admin.html` / `web/assets/admin.js` | 改 | +1 页签 ~150 行 |
| `src/core/test_corpus_review.py` | 新增 | ~10 例 |
| `experiments/trace/reports/` | 文档 | 本方案 + 验收记录 |

预计 **1 轮**可完成（不含真实 bulk 入库的等待时间）。

---

## 9. 需要你确认的三点

1. **批次粒度**：批准是否立即入库，还是攒成批次统一入库？（方案默认「先批准、再手动批量入库」，便于回滚与控时）
2. **入库开关**：批次是否默认 `--activate`（立即生效）？默认建议「入库但不激活，管理员再点生效」，风险更低。
3. **页签位置**：放在现有管理员页 vs 独立 `/admin/corpus` 页面？（默认现有页加页签，改动最小）
