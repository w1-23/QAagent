# Phase 1 交付：17 个工具的真实前置条件 + 最小能力过滤 + A 组静态测试

状态：**本地已完成并通过测试，未部署服务器，未推送 GitHub**。
日期：2026-10-30。基线提交：`ff58b1f`（main）。

本轮只做三件事，不改 `_scoped_toolset()`、不加 XLSX、不动 `PERMISSION_MODE`：

1. 把 17 个工具的真实 `requires / produces / authorization` 写成声明（单一定义点）；
2. 新增纯函数 `available_tool_names(env)`，按环境事实决定下发集合（不含任何关键词/意图判断）；
3. 补最小关联字段，使"本轮某工具产出了某文件"可被查证（`produces` 验证的前提）。

---

## 一、17 个工具的绑定矩阵

`requires` 只写**执行真实需要的外部条件**（不是工具名推测）。`produces` 只写该工具**实际写盘**的产物。

| # | 工具 | requires | produces | authorization | 依据（代码） |
|---|------|----------|----------|---------------|--------------|
| 1 | `deepseek_search` | `network` | — | — | `deepseek_search_fetch.py:155` |
| 2 | `deepseek_fetch` | `network` | — | — | `:244` |
| 3 | `deepseek_fetch_paper` | `network` | — | — | `:292`（DOI/PMID/题名由模型给） |
| 4 | `local_hybrid_search` | `local_corpus` | — | — | `:468` |
| 5 | `draw_svg` | `session` | `figure` | — | `draw_svg.py:72`，写入 SVG 产物 |
| 6 | `read_account_file` | `account_files` | — | — | `account_files.py:176` |
| 7 | `inspect_uploaded_table` | `attachment` | — | — | `research/tools.py:431` `_attachment` → `get_request_tables()` |
| 8 | `audit_uploaded_data` | `attachment` | `audit_report` | — | `:1385-1390`，空附件直接返回"审查未启动" |
| 9 | `verify_uploaded_references` | **—（无）** | `verification_sheet` | — | `:1143-1149`：传入 `references_json` 时不读附件 |
| 10 | `fetch_and_audit_open_supplements` | `network` | `supplement_archive`<br>`supplement_report` | — | `:1506-1560`，只吃 DOI，**不要求上传** |
| 11 | `export_literature_evidence` | `session` | `bibliography` | — | `:1684`；格式仅 `xlsx / ris / bibtex`（`:1681` 否则 `ValueError`） |
| 12 | `export_paper_analysis` | `session` | `report_html` | — | `:1780` |
| 13 | `export_experiment_design` | `session` | `report_html` | — | `:1786`，正文由模型生成，**不要求附件** |
| 14 | `save_research_note` | `session` | `note` | `explicit_user_request` | `:1792`，账号级持久写入 |
| 15 | `recall_research_notes` | —（无） | — | — | `:1807`，无记录返回空集 |
| 16 | `export_research_notes` | `session` | `report_html` | — | `:1824` |
| 17 | `export_corpus_gap_report` | `session` | `gap_csv` | — | `:1853` |

三处按用户意见纠正过的判断：

- **#10 补充材料**：`requires = {network}`，不是 `attachment`。用户给 DOI、给题名、或模型从检索结果拿到 DOI 都能执行（`:1506` 只校验 DOI 格式）。
- **#13 实验方案导出**：`requires = {session}`，不是 `attachment`。`design_markdown` 由模型生成。
- **#9 参考文献核验**：`requires = ∅`。`references_json` 有值时不读附件（`:1147` 只在 `not references` 时才回落到附件）。

反过来，**确实是硬前置**的三个：`inspect_uploaded_table` / `audit_uploaded_data`（本轮必须有上传表格，否则 `:1390` 直接返回"审查未启动"）、`read_account_file`（账号库必须 ≥1 个文件）。

---

## 二、`available_tool_names(env)` 的规则

`src/graph/tool_availability.py`（新，5,357 B）：

```python
@dataclass(frozen=True)
class Env:
    network: bool = True          # 出网可用（含 key 可用）
    local_corpus: bool = True     # 本地索引可用
    attachments: int = 0          # 本轮上传的表格/论文数
    account_files: int = 0        # 账号库文件数
    session: bool = True          # 有效会话（产物写入前提）
    research_enabled: bool = True # CITRUS_RESEARCH_TOOLS_ENABLED
    denied: frozenset[str] = frozenset()   # 用户明确禁止/管理员策略
```

`available_tool_names(env)` 只做四件过滤，**没有一条看用户措辞**：

1. 研究类工具在 `research_enabled=False` 时整体不可见（沿用既有开关语义）；
2. `requires` 逐项对照 `Env`（`network` / `local_corpus` / `attachment`→`attachments>0` / `account_files`→`account_files>0` / `session`）；
3. `env.denied` 显式排除；
4. 数量不设上限——17 个全可用就下发 17 个。

`authorization` **不在这里判定**（用户意见 E）：它是执行期检查，`save_research_note` 在**执行器**里核对"当前用户请求是否明确授权这次写入"，而不是靠可见性挡住。

`explain(env)` 返回 `{工具名: "" 或 不可用原因}`，供测试与排障用（原因码如 `no_attachment` / `no_network` / `no_local_corpus`）。

---

## 三、最小关联字段（`produces` 可验证的前提）

排查结论：改动前**没有**"某工具 → 本轮 → 某文件"的关联。

- `artifacts` 表只有 `(artifact_id, session_id, filename, mime, created_at, path)` —— 没有工具名、没有请求 id（`research/tools.py:304-340`）；
- `_save_artifact()` 调 `session_files.register_file()` 时**没有传** `client_request_id`，也没有写 `session_file_turn_refs`；
- `api/main.py:984` 的 `link_turn_files()` 只链接**上传**文件（`uploaded_ids + file_refs`），不链接本轮生成的报告。

带来的直接风险（用户意见 C）：只按扩展名判断"这轮审查成功"会把"实验设计 HTML 成功、数据审查失败"误判成审查成功。

本轮补的最小字段（加法式，不改变现有行为）：

| 位置 | 变更 |
|------|------|
| `src/core/tracing.py` | 新增请求级 contextvar：`client_request_id`、`current_tool_name`（+ get/set） |
| `src/api/main.py` | 请求入口 `set_client_request_id(req.client_request_id)`（与 `set_session_id` 同处） |
| `src/tools/registry.py` | `_run_single_tool` 在 `ctx` 上设 `current_tool_name`，工具体内可读 |
| `src/research/tools.py` | `_save_artifact` 把 `client_request_id` + `producer_tool` 传给 `register_file` |
| `src/session/files.py` | `register_file(..., producer_tool="")`；新增 `producer_tool` 列（带 `ALTER TABLE` 迁移）；插入后按 `client_request_id` 写 `session_file_turn_refs` |

之后 `list_turn_files(session, user, [client_request_id])` 就能返回"本轮这个工具产出的文件"，`deliverable_check`（Phase 2）据此判定成功，而不是看扩展名。

---

## 四、测试结果

新增 `src/graph/test_tool_bindings.py`（A 组静态测试，12,011 B，48 项断言，0 失败）：

```
$ python src/graph/test_tool_bindings.py
PASS  17 个声明齐全且与运行时注册表一致
PASS  每个工具都有 requires/produces/authorization 字段
PASS  requires 取值在受控词表内（无自由文本、无关键词）
PASS  produces 只出现真实产物类型（audit_report/report_html/bibliography/figure/...）
PASS  save_research_note 是唯一带 explicit_user_request 授权的工具
PASS  声明中不存在 intent/keyword 门控字段
PASS  无附件时：inspect_uploaded_table / audit_uploaded_data 不可见
PASS  无附件时：verify_uploaded_references 仍可见（references_json 可独立使用）
PASS  无网络时：3 个联网工具不可见，本地检索仍在
PASS  无本地索引时：local_hybrid_search 不可见
PASS  账号库为空时：read_account_file 不可见；recall_research_notes 仍可见
PASS  denied 命中即排除，且不影响其他工具
PASS  全环境可用时下发 17 个（不设人为上限）
PASS  explain() 对每个不可用工具给出原因码
...
FAILS: 0
```

回归（本地 `E:\anaconda\envs\rag-agent\python.exe`，`src/` 全量）：

```
2 failed, 145 passed
```

两个失败**在 HEAD `ff58b1f` 上同样失败**（已用干净 worktree 复核，非本轮引入）：

- `src/api/test_account_profiles.py::test_first_chat_upload_is_registered_to_server_created_session`
  `TypeError: <lambda>() takes 3 positional arguments but 4 were given`（测试自身 `asyncio.to_thread` 用法与实现签名不符）
- `src/session/test_files_lifecycle.py::test_idle_cleanup_includes_orphaned_file_activity`
  `assert result["purged_sessions"] == 1` 实际 `0`（清理计数口径与实现不符）

另修了两个**因 B1 变更而过期**的断言（它们把旧的关键词收窄结果写成"集合恰好等于某工具名"，属于把实现细节当规范）：

- `test_tool_capabilities_are_reduced_to_the_requested_workflow` → 改为三条不变量：相关能力可达、无关能力不可达、不下发全量；
- `test_project_notes_and_exports_are_only_available_on_explicit_intent` → 改为"该工具在集合内"而非"集合等于它"。

`test_progress_events.py` 的隐私断言也同步纠正：`action_hint` 是**有意展示给用户的操作描述**，会含检索词；仍然断言"事件不含工具返回正文、不含原始 `args` 结构、hint ≤80 字符"。

---

## 五、变更范围（8 改 2 增）

```
01_源码_src/src/api/main.py                          |  4 +-
01_源码_src/src/core/tracing.py                      | 27 ++
01_源码_src/src/graph/test_general_agent_routing.py  | 26 ++--
01_源码_src/src/graph/test_progress_events.py        |  8 +-
01_源码_src/src/research/tools.py                    |  5 +-
01_源码_src/src/session/files.py                     | 23 ++-
01_源码_src/src/tools/__init__.py                    | 68 ++++---
01_源码_src/src/tools/registry.py                    |  4 +
新增 01_源码_src/src/graph/tool_availability.py       | 5,357 B
新增 01_源码_src/src/graph/test_tool_bindings.py      | 12,011 B
```

未触碰：`_scoped_toolset()`、`_source_scope()`、`_explicit_*`、`required_tool_groups`、`registry.py` 沙箱逻辑、SSE/历史/去重/超时链路。

---

## 六、Phase 2 待确认事项

1. 是否需要先修上面两个 HEAD 既有失败（属测试与实现口径不一致，不影响运行）。
2. Phase 2 顺序建议：`_source_scope()` 10 条逐条甄别 → 移除 `_scoped_toolset()` 关键词层 → 执行期 `authorization`（`save_research_note`）→ `deliverable_check` 接 `producer_tool` → 四类真实任务端到端（普通问答 / 表格理解 / 科研任务 / 失败处理），并对比改造前后工具调用次数、超时次数、完成情况。
3. `MAX_TOOL_ROUNDS` 3→6 的改动目前只在 `state.py`，尚未纳入本轮 diff。
