# Round 4：验收缺口补齐与服务器隔离实例验收

> 基线：生产 `/home/WZQ/CitrusQA` 仍运行 `ff58b1f` 字节，本文件记录的是**隔离实例**（`/home/WZQ/staging_citrus`，端口 8011）上的验收结果。
> 本地仓库 `E:\codex_WORKSPACES\Citrus_QA_Agent` 为唯一源，生产未被改动。

## 0. 本轮做了什么（一句话一项）

| # | 缺口（来自目标验收矩阵） | 处理 |
|---|---|---|
| 1 | 跨用户可读他人任务状态 | `GET /jobs/{id}`、`GET /sessions/{id}/jobs` 增加会话归属校验 |
| 2 | 重复点停止会重复改状态 | `jobs.request_cancel` 只从 `running/detached` 迁移，重复请求返回 `False` |
| 3 | 写文件类工具取消后可能留半个文件 | 已有原子落盘；本轮补**真文件系统**断言（写前取消 / 提交前取消 / 正常一次三例） |
| 4 | 停止晚于完成会被写成 cancelled | 已有 `mark_completed_if_open`；本轮补断言（completed + 说明） |
| 5 | 进度只在运行期，刷新后丢失 | 最后一条公开进度写入 `task_jobs.progress_summary`；完整序列仍只在运行期 |
| 6 | PDF 空白/失败无说明 | 403 / 404 / 超 40 MB / 断网 / 非 PDF / 空文件 分别给可行动文案 |
| 7 | 验收只在本地跑过 | 建服务器隔离实例（8011），用同一组探针复验 S1/S2/S3/S4 |

## 1. 代码改动（文件 + 行为）

### 1.1 任务端点归属校验 —— `01_源码_src/src/api/main.py`

- `get_job_status(job_id, request)`：读取到 job 后，若 `job["session_id"]` 存在则 `require_owned_session(...)`；不属于当前用户 → 404（不泄露"存在但无权"）。
- `list_session_jobs(session_id, request)`：进函数先校验会话归属。

变更前：只要知道 `job_id` / `session_id` 就能读到别人的任务状态、停止原因、`progress_summary`。
变更后：跨用户一律 404。

### 1.2 停止请求幂等 —— `01_源码_src/src/core/jobs.py`

```sql
-- 变更前
WHERE job_id=? AND status IN ('running','detached','cancel_requested')
-- 变更后
WHERE job_id=? AND status IN ('running','detached')
```

`caller _cancel_one_job` 原本用 `accepted = request_cancel(...) and not already` 表达"重复点击不算再次接受"，现在数据库层直接返回 `False`，语义与实现一致。

`mark_stopped` / `mark_completed_if_open` **保持**允许 `cancel_requested`（终态迁移仍需从该态出发）。

### 1.3 进度落库 —— `01_源码_src/src/core/public_progress.py`

新增 `_persist_last_note()`（`_PROGRESS_SUMMARY_MAX = 200`），在 `emit_public_progress()` 成功之后调用：

- 通过 `tracing.get_job_id()` 拿当前任务；
- 写 `task_jobs.progress_summary`（截断 200 字符）；
- 任何异常静默（进度持久化绝不影响执行）。

**明确的边界（用户要求想清楚）**：

| 信息 | 保存位置 | 生命周期 |
|---|---|---|
| 完整进度序列（每条工作说明） | 不写库，只走 SSE | 运行期，刷新即失 |
| 最后一条进度 | `task_jobs.progress_summary` | 随任务记录保留，刷新/重连可见 |
| 最终回答 | 消息历史 | 长期 |
| 工具执行行（状态/耗时/结果摘要） | 消息历史 `execution_json` | 长期 |

理由：完整序列是模型的工作假设，条数随轮次增长；长期入库既会让历史表膨胀，也越过了"公开工作说明≠隐藏推理"的边界。

### 1.4 PDF 预览失败态 —— `01_源码_src/web/assets/session_files.js`

`loadPdfPreview()` 变更：

- `fetch` 前声明 `MAX_INLINE_PDF = 40 * 1024 * 1024`；
- 响应按状态码分流：403 → `forbidden`，404 → `not-found`，其它非 2xx → `HTTP n`；
- 读取 `content-length`，超过上限抛 `too-large:<实际>MB:<上限>MB`，**不把整份文件拉进内存**；
- catch 分支逐类给文案：

| 触发 | 用户看到的 |
|---|---|
| 403 | 无权访问该文件（它属于其他会话或账号），请联系文件所有者。 |
| 404 | 文件不存在或已被删除，请刷新侧栏确认。 |
| > 40 MB | 文件为 X MB，超过 Y MB 的内嵌预览上限，为避免页面卡死没有直接加载；请下载或直接打开。 |
| 断网（`Failed to fetch` / `NetworkError` / `Load failed`） | 网络中断，文件没能下载下来；请检查网络后重试，或下载到本地再打开。 |
| 非 PDF 魔数 | 服务器返回的内容不是 PDF 文件，未做预览。 |
| 0 字节 | 文件内容为空（0 字节），未做预览。 |
| 其它 | 读取失败（原因），请重试或下载到本地打开。 |

### 1.5 探针自身修正（重要）

`probe_cancel_e2e.py` 原先两条断言是**假通过**，本轮改掉：

- `no_files_committed` 读的是 `files.get("files")`/`("items")`，而接口返回的是 `uploaded`/`generated` → 恒为真。改为只看本会话的 `generated`。
- `has_cancelled_row` 只看"有没有 cancelled 行"，但停止落在答案生成阶段时本就不该有 cancelled 工具行 → 改为**等价断言**：`历史里有 cancelled 行 == 执行期有 cancelled 结果`，两者都必须成立或都不成立。

顺带发现（未改，记录待决）：`/api/v2/files` 在 `scope=account` 下会返回**其它会话**的文件（本轮实测拿到上一探针会话的 `probe_history_jump.csv`）。这不是越权（同账号），但前端"会话文件"面板若用该接口需要按 `session_id` 过滤。

## 2. 测试

本地（`E:\anaconda\envs\rag-agent\python.exe -m pytest src -q -p no:cacheprovider`）：

```
259 passed, 3 warnings
```

本轮新增 22 例：

| 文件 | 例数 | 覆盖 |
|---|---|---|
| `src/core/test_cancel_acceptance.py` | 9 | 写前取消不落文件 / 提交前取消不留 `.part` / 正常一次只落一个文件 / 重复请求幂等 / 重复停止不重复清理 / 完成后取消被忽略 / 完成后到达记为 completed + 说明 / 跨用户读 job 404 / 跨用户读列表 404 |
| `src/api/test_pdf_preview_states.py` | 6 | 体积上限在读取之前 / 六个失败分支齐全 / 文案给出"下载"或"重试" / 不支持格式有下载入口 |
| `src/core/test_progress_persistence.py` | 5 | 进度写进 job 行 / 超长截断 / 任务不存在不报错 / 无绑定静默 / 进度不写消息历史 |
| 既有 `test_cancel_lifecycle.py` 等 | 2（修改） | 幂等语义变化后同步 |

## 3. 服务器隔离实例验收（`/home/WZQ/staging_citrus`，端口 8011）

### 3.1 实例是怎么建的（可复现）

```bash
cd /home/WZQ
rsync -a --exclude '.venv' --exclude 'agent/.hf_cache' \
         --exclude 'agent/logs' --exclude 'agent/.cache' \
         CitrusQA/ staging_citrus/
ln -sfn /home/WZQ/CitrusQA/.venv /home/WZQ/staging_citrus/.venv
# 嵌入模型缓存必须指回生产，否则 CPU 上无法加载 e5-large
rm -rf /home/WZQ/staging_citrus/agent/.hf_cache
ln -s /home/WZQ/CitrusQA/agent/.hf_cache /home/WZQ/staging_citrus/agent/.hf_cache
# 覆盖本轮代码（本地打包 src/ web/）
tar -xzf /home/WZQ/staging_code.tar.gz -C /home/WZQ/staging_citrus/01_源码_src
# 启动（生产端口 8000 不受影响）
cd /home/WZQ/staging_citrus && \
  setsid nohup env CITRUSQA_PORT=8011 bash 04_脚本_scripts/run_server.sh \
  > /home/WZQ/staging_citrus/staging_server4.log 2>&1 < /dev/null &
```

踩坑：第一次启动时报
`ERROR [RAG] MultiBatchRetriever preload failed: cannot load embedding model ... intfloat/multilingual-e5-large from any source`
—— 原因是 rsync 排除了 `agent/.hf_cache`，而 `FASTEMBED_CACHE_PATH` 指向它。软链到生产的 `.hf_cache` 后启动日志无 ERROR，本地检索可用。

### 3.2 S3（历史分页 + 文件→轮次）

`probe_history_jump_e2e.py` @8011：

```json
{"total_rows": 6, "page1_ids": [415, 416], "page2_ids": [413, 414],
 "checks": {"newest_page_returned": true, "paging_walks_backwards": true,
            "rows_carry_message_id": true, "file_carries_turn_key": true,
            "jump_key_matches_history": true, "no_fake_jump_for_unknown_turn": true},
 "all_passed": true, "elapsed_s": 7.5}
```

### 3.3 S1（取消）

`probe_cancel_e2e.py` @8011（取消于第 3.0 s）：

```json
{"job_record": {"status": "cancelled", "stop_reason": "user"},
 "done_turn_status": "cancelled", "done_answer_chars": 0,
 "history_tool_rows": [{"name": "网页搜索", "status": "complete",
                        "summary": "获得 8 条网页资料", "elapsed_ms": 680}],
 "checks": {"job_id_exposed": true, "cancel_accepted": true, "job_cancelled": true,
            "stop_reason_is_user": true, "turn_status_cancelled": true,
            "no_fake_completed_row": true, "has_cancelled_row": true,
            "no_half_baked_answer": true, "no_files_committed": true}}
```

停止说明文案（真实产出）：

> 已按你的停止请求中止本轮：上面已完成的步骤与其结果保留；被停止的步骤没有采用结果，也没有登记任何成果。
> 停止前已执行的步骤：- 网页搜索 （完成） 获得 8 条网页资料 700ms

### 3.4 S2（公开工作说明）与本地检索

`probe_public_progress_e2e.py` @8011，问题「先检索柑橘黄龙病的传播途径，再对比本地资料与公开来源的异同，分条说明。」

```json
{"model_calls": 4, "input_tokens": 75783, "output_tokens": 7191,
 "elapsed_s": 84.8, "done_turn_status": "completed", "answer_chars": 3553,
 "progress_count": 3, "tool_starts": 7}
```

三条进度（原文）：

1. 我先同时查公开来源和本地文献库中的黄龙病传播途径，再逐条对照两者的差异。
2. 本地检索已命中几条记录，涉及木虱、嫁接、菟丝子和种子的证据。我再核实两点：种子是否能传病，以及非洲木虱等媒介范围。
3. 本地资料和公开来源已各有可用证据。我再补查取食获菌时间和媒介昆虫范围，确认哪些差异是资料范围造成的，哪些是真正的出入。

事件序列（节选）：`job → context_usage → assistant_progress → tool_call_start(网页搜索) → tool_call_start(本地检索) → tool_result → tool_result → context_usage → assistant_progress → …`

这同时证明了用户关心的第 2 条投诉（"本地检索不可以用"）在**同代码、同模型、同数据集**的隔离实例上是可用的：`本地检索` 工具真实执行并返回结果。

### 3.5 S4（PDF 预览）

`probe_pdf_preview_e2e.py` @8011：

```json
{"status": 200, "content_type": "application/pdf", "accept_ranges": "bytes",
 "range_status": 206, "bytes_equal_original": true,
 "checks": {"library_kind_is_pdf": true, "content_type_is_pdf": true,
            "body_is_real_pdf": true, "inline_disposition": true, "range_supported": true},
 "all_passed": true}
```

## 4. 尚存风险 / 未做

1. **前端未做视觉验收**：本机无 `node`（此前用于 `--check` 的 Node 运行时已不在），共享浏览器 provider 不可用。JS 只做了结构审读 + Python 静态断言，未跑真实浏览器。
2. **隔离实例是 CPU-only**：模型加载与推理慢（S2 一轮 84.8 s），不代表 GPU 生产表现；嵌入缓存靠软链复用生产。
3. `/api/v2/files?scope=account` 跨会话返回文件（同账号），前端如需"本会话"必须自行按 `session_id` 过滤。
4. 生产未部署：`/home/WZQ/CitrusQA` 仍是 `ff58b1f`，本轮代码只在 `staging_citrus`。生产重启会丢失隔离实例（不影响生产）。
5. 停止落在"答案生成中"时不会生成 cancelled 工具行（设计如此，探针已改为等价断言而非"必须有一行"）。

## 5. 部署建议与回滚

- 部署：`git archive` 或 rsync 把 `01_源码_src/src`、`web` 覆盖到生产 → `kill -TERM` 旧 uvicorn → `bash 04_脚本_scripts/run_server.sh`（约 17 s 后 `curl /` = 200）。
- 回滚：`git checkout ff58b1f -- 01_源码_src/src 01_源码_src/web` 后重启；数据库无需迁移（本轮只新增测试与 `progress_summary` 写入，已有列）。
- 迁移注意：`request_cancel` 语义变更（重复停止返回 `False`），若外部有脚本依赖 `True/False` 判断"是否接受"，需同步。
