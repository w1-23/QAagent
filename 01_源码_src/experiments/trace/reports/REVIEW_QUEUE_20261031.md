# 文献候审库（去切块化）交付说明

> 版本：本地 `93820f8`（未 push、未部署生产）
> 日期：2026-10-31（Asia/Shanghai）
> 范围：仅文献入库审核模块；未改检索、未改语料、未改数据审查

---

## 1. 用户口径

1. **攒批量入库**：批准不等于入库，允许先审后批量处理。
2. **不切块**：候审库只保存原始资料，不切块、不向量化、不建索引、不激活语料。
3. **保留原文**：抓取到的 Markdown 原文、用户上传的 PDF 原件。
4. **记录频次**：同一篇被多次抓取时累加次数，高频者可优先处理。
5. **用户上传的 PDF 也汇总一份到后台**。
6. **放管理页**；做完更新文档。

> 关于「能否复用自适应切块」：审计确认 `src/retrieval/corpus_admin.py::ingest_main()` 现状用的是
> **固定 800 字符段落打包**（`:578-588`），与 5 批语料所用的自适应切块不是同一实现，
> `src/` 内也不存在自适应切块代码。按用户指示采用「保留原文」方案。

---

## 2. 设计

### 2.1 存储

```
agent/data/_review_queue/archive/<key>/
├── paper.md          # 抓取来源：Markdown 原文（由 HTML 转换或站点直出）
├── original.pdf      # 上传来源：用户原始字节（逐字节一致）
└── meta.json         # 存档元信息（url/doi/title/格式/转换方式/sha256/时间）
```

`key` 优先取 DOI（`/` → `_`），否则 `sha256(内容)[:10]`。

### 2.2 数据表

`agent/state/corpus_review.sqlite3` → `corpus_candidates`

| 字段 | 说明 |
| --- | --- |
| `id / doi / title / source_url / source_kind` | 身份与来源 |
| `origin / fetched_at` | `chat_fetch` / `upload`，抓取或上传时间 |
| `raw_format` | `markdown` / `pdf_binary`（上传非 PDF 时为 `application/octet-stream`） |
| `conversion` | `none`（原件）/ `html_to_markdown` / `upstream_extract(...)` |
| `raw_sha256 / raw_bytes / stored_path` | 存档校验与定位 |
| `frequency` | 命中次数（同 DOI/同内容累加，不覆盖原文） |
| `status` | `pending` / `approved`（保留）/ `rejected` |
| `archive_state` | `stored` / `missing`（文件被移动或删除时由读接口标注） |

旧库就地 `ALTER TABLE` 补齐新列，不重建、不丢历史记录。

### 2.3 状态机

```
抓取/上传 ──> pending ──保留──> approved
                  └──拒绝──> rejected
approved / rejected ──重新审核──> pending
```

只做人工判定，不触发任何语料变更；`decide()` 用条件更新（`WHERE status IN (...)`）+ `rowcount`
保证幂等，重复点击不会重复计数。

### 2.4 抓取与上传的接入点

- **抓取**：`src/tools/deepseek_search_fetch.py` 在竞争胜出、拿到 `web_results[0]["text"]` 后调用
  `corpus_review.archive_fetch(...)`；失败只记日志，不影响工具返回。
- **上传**：`src/api/main.py::_capture_upload_candidate(name, text, raw=...)` 在附件正文提取后调用
  `archive_upload(...)`，PDF 原始字节一并落盘。两个调用点（聊天附件、会话附件）都覆盖。

### 2.5 接口与页面

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v2/admin/corpus/candidates` | 列表（`status` / `q` / 分页 + 统计） |
| GET | `/api/v2/admin/corpus/candidates/{id}` | 详情（含原文前 4,000 字符） |
| GET | `/api/v2/admin/corpus/candidates/{id}/raw` | 只读返回存档原件 |
| POST | `/api/v2/admin/corpus/candidates/{id}/approve` | 保留 |
| POST | `/api/v2/admin/corpus/candidates/{id}/reject` | 拒绝 |
| POST | `/api/v2/admin/corpus/candidates/{id}/reopen` | 重新审核 |

全部经 `require_admin(request)`；写操作走 `asyncio.to_thread`，不阻塞事件循环。
管理页 `web/admin.html` 新增「文献候审库」面板：状态筛选、统计行、查看原文、保留/拒绝。

---

## 3. 与旧实现相比删掉了什么

- 批次表 `corpus_batches` 与「批量入库」「整批回滚」端点（不再有入库动作）。
- `apply_batch()` / `rollback_batch()` / `_run_ingest()` 调用链。
- 切块参数（`chunk_size`）、激活开关（`--activate`）、语料 generation 的任何写入。
- `general_agent_graph.py` 中基于 `artifact` 的抓取捕获钩子。

保留：`src/retrieval/corpus_admin.py` 本身未改，仍可用于其他语料管理。

---

## 4. 验收记录（真实执行，非模拟）

| 项目 | 方法 | 结果 |
| --- | --- | --- |
| 抓取入库 | 真实调用 `deepseek_fetch_paper(10.3390/plants12010160)` | 存档 `paper.md` 239,048 字符 / 240,179 字节 |
| 上传入库 | 真实 HTTP 上传 237 字节 PDF | `original.pdf` 与原文件 **逐字节一致**（sha256 相同） |
| 不激活语料 | 比对 `agent/data/active_index.json` 与 generation 目录 | 前后一致，未新增世代 |
| 幂等批准 | 连续两次 `approve` | 第二次 `changed=false` |
| 状态流转 | approve → reopen → reject → reopen | 全部符合状态机 |
| 管理页渲染 | headless Chrome 截图 + DOM 断言 | 面板存在、统计行正确、条目含元信息与三个按钮 |
| 回归 | `python -m pytest src -q` | **427 passed** |

测试文件：`src/core/test_corpus_review.py`（13 例）、`src/api/test_corpus_review_admin_markup.py`（8 例）。

---

## 5. 已知边界

- 单条存档上限 32 MB（超出返回 413 风格错误），低于 2,000 字符的内容不入池。
- `raw` 接口按需读取整个文件；大 PDF 会占用较多内存（未做分片）。
- 同 DOI 再次抓取不覆盖已有原文，只累加 `frequency`；如需更新原文，当前需先删记录。
- HTML → Markdown 为内置转换（标题/段落/列表/链接/表格），复杂排版可能丢失结构。
- 存档目录依赖本地磁盘；生产部署时需同步 `agent/data/_review_queue/`。

---

## 6. 下一步（未实现）

数据审查模块改为「标注 Excel」：逐行给出 AI 标注与理由，而不是现在的八项规则检查报告。
草案：

1. `export_table_for_annotation()`：导出原表副本（不改原文件）+ 返回精简行视图给模型。
2. 模型逐行给 `{row, verdict, reason, field?}`。
3. `apply_table_annotations()`：把标注追加为「标注结论 / 标注理由」两列，另加「说明」工作表。
4. 输出仍走 `_save_artifact`，文件名 `<原名>_标注.xlsx`，原件只读。
