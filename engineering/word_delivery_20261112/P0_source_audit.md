# P0 源码审计与本轮修复（通用 Word 导出与交付工程能力）

范围：仅源码与工程能力改造 + 隔离工程测试。**未运行** Case A/B/C 三个真实科研案例，**未重跑** 原 FW20/40，未改动其 gold、评分器或历史结果。未提交、未推送、未重启当前服务。

## 1. 基线事实

| 项 | 值 | 来源 |
| --- | --- | --- |
| 工作目录 | `E:/codex_WORKSPACES/CitrusQA_Local`（git worktree） | 环境 |
| 分支 / HEAD | `main` / `8a40d773b18a0e9adb8d831274b6820084382ff0` | `git rev-parse HEAD` |
| 磁盘部署记录 | commit `8a40d77`，2026-11-11T17:29:55+08:00，pid_owner 16896 | `01_src/DEPLOYED_RECORD.json` |
| Python | 3.13.9 | `python --version` |
| python-docx / openpyxl | 1.2.0 / 3.1.5（本机已装，未执行安装） | 依赖导入 |

部署记录只是磁盘上的历史文件，**不代表本轮确认过运行中进程的内存版本**。改动需受控重启后才能在服务实例中生效。

工作区在开工前已有未提交修改（`data_audit_xlsx.py`、`general_agent_graph.py`、`test_reliability_contracts.py`、`research/tools.py`、`tools/outcome.py` 及未跟踪的 `export_contract.py`、`intent_text.py`）。本轮在其之上继续，未回退。初始已跟踪 diff 快照保存在 `_backups/pre_word_engineering_20261112.patch`（该快照不含当时未跟踪的两个新模块）。

## 2. 关键架构位置（实际核对，非按名猜测）

- 单通用 Agent：`src/graph/general_agent_graph.py`
- 唯一活动工具声明：`src/tools/__init__.py::_TOOL_DECLARATIONS`（7 列，无意图/关键词列）
- 环境能力过滤：`src/graph/tool_availability.py`
- 交付契约纯函数：`src/graph/export_contract.py`
- 生成物发布：`src/research/tools.py::_save_artifact`
- 账号资料库与下载：`src/session/files.py`、`src/api/main.py::download_account_file`
- 固定提示拼接：`src/prompts/loader.py`（5 份 source/*.md）

原有实现中**没有**通用 DOCX 写入器，也**没有**动态 Skill 加载机制；资料库已支持 DOCX 类型、下载与卡片，因此本轮复用而非另造文件系统或前端。

## 3. 根因与修复

下表中"复现"一列区分本轮实际验证与用户提供的背景报告。用户上一轮 40 题报告中的日志次数、pid 21976 与 `v8_work/02_P4/SOURCE_DEFECT_REPORT.md` **未经本轮独立核验**，不作为结论依据。

### 3.1 交付被误判 / 产物被覆盖（对应用户报告的 A）

| 问题 | 根因 | 修复 | 证据 |
| --- | --- | --- | --- |
| 不同产品被同一文件算作完成 | 匹配层按 kind/format 全局去重，同名文件合并 | 按 `artifact_id`/`link` 身份去重，二分图增广匹配，每需求一份不同产物 | `test_document_contracts.py::test_registry_identity_and_original_indices` |
| 匹配结果索引反转 | 返回 requirement_index 当作 artifact_index | 返回需求→原始 validations 索引，含增广重排 | 同上 |
| 输入格式被当成输出需求 | 整轮扫描所有格式 token | 仅正向输出对象成需求；输入来源、否定子句排除 | `test_positive_products`（含"不要再导出"） |
| 同名新内容覆盖旧下载 | `append`/`replace_in_request` 直接覆写已发布字节 | 不可变发布：新字节新 artifact_id/version，旧文件字节保留 | `test_single_report.py::test_exact_retry_and_changed_output_have_consistent_versions` |
| 跨轮复用旧轮产物 | 去重键不含 request_id | 去重键纳入 request_id；同轮精确重试才复用 | `test_independent_turn_with_same_content_has_current_turn_attribution` |
| 资料库登记失败仍报成功 | 登记异常仅 warning 后返回 URL | 校验 file_id/sha256/size，失败抛 `artifact_registration_failed` 并保留字节供重试 | `test_registration_failure_is_visible_and_exact_retry_recovers` |
| 多报告被复制成多份 | 收尾把同一 answer 灌给每个报告需求 | 返回 `missing_product_content`，保留未完成状态 | `_force_deliverable_export` |

### 3.2 内容截断（对应用户报告的 C，以及"总是导出截断"）

- 删除 `_repair_truncated_json`：不再补引号/括号"修复"科学参数。截断 JSON 抛 `ToolArgumentError`，明确"本批未写入"并给出缩小批次或 `last_verification` 的出路。
- 删除 `_drop_trailing_unclosed_block`：不再删掉未闭合围栏后把半份正文当完整交付。HTML 正文校验失败直接 `DeliverableRejected`，提示补全或改用分块文档工具。
- `export_paper_analysis` / `export_experiment_design` 原先忽略 `append` 参数，已修正传递。
- `append=True` 不再绕过完整性校验。

证据：`test_truncated_json.py`（含半个 DOI 必须拒绝）、`test_reliability_contracts.py::test_unclosed_trailing_block_is_not_silently_published`、`test_single_report.py::test_append_rejects_corrupt_batch_before_any_publication`。

### 3.3 合并单元格批注中止审查（对应用户报告的 B）

原实现跳过合并区锚点并用宽泛 except 吞错。现在定位到 `MergedCell` 时改写到左上可写单元格，同锚点聚合问题、保留原批注与值/公式，问题清单仍保留原始 A2/B2 坐标。

证据：`test_merged_audit_marks.py`（合并 A2:B2 两处问题都汇总到 A2，原值/公式/批注不变，源工作簿不变）。

### 3.4 导出格式缺口（对应用户报告的 D、E）

- 强制书目导出原先只支持 xlsx/last_verification；现支持 **xlsx / ris / bibtex**，并从本轮真实核验工作簿或真实检索候选取条目，无证据时返回 `missing_bibliography_evidence`，不造条目。
- 新增通用 `export_table`（CSV/TSV/XLSX）。普通 CSV 不再被路由到固定字段的语料缺口导出器；缺口 CSV 仍必须有真实观测到的检索失败。
- `last_verification` 改为按 session + request_id + producer_tool 精确定位并校验受管路径与 hash，不再按文件名猜"上一步"。

证据：`test_document_contracts.py::test_bibliography_has_real_provenance`、`test_csv_is_not_a_corpus_gap_and_requires_real_rows`、`test_export_table.py`、`test_literature_handoff.py`。

### 3.5 工具可见但不可用（对应用户报告的 F）

`deepseek_search` 与 `deepseek_fetch*` 依赖不同 provider 配置，并非"执行映射漏名"。新增纯配置就绪函数 `provider_denials`：按实际 provider 分别判定 search/fetch 可用性，不输出密钥、不做网络探测，公共学术检索不受付费搜索缺 key 牵连。同时移除对 `verify_uploaded_references` 额外强制 `deepseek_search` 的不合理绑定。

证据：`test_provider_readiness.py`、`test_tool_visibility.py::test_missing_provider_configuration_hides_only_its_tools`。

### 3.6 状态误判（工具成功被文本兜底掩盖）

- SVG 的 `SVG_INPUT_MISSING`/`SVG_FORMAT_ERROR`/`SVG_ATTEMPT_LIMIT` 不再被当作 complete。
- 文献核验计数只取摘要首句避免重复累加，按状态 findall 求和，`network_error`/`not_checked` 全量计入；未知非空文字改为 `partial/unconfirmed_verification_status`，不再冒充完成。
- `_json_arg` 对 `0`/`false`/`"0"` 等错误类型不再静默回落到"全部附件"默认值。

证据：`test_outcome.py`、`test_truncated_json.py::test_wrong_json_types_cannot_silently_select_default_attachments`。

### 3.7 新增：受控增量 Word

`src/core/word_documents.py` + `src/research/word_tools.py`。要点见 `WORD_TOOL_API.md`。核心保证：

- 结构源、真实 DOCX BLOB、批次回执在**同一 SQLite 事务**提交；渲染失败整体回滚，不留已提交批次。
- batch_id + payload 哈希幂等；expected_version CAS；`BEGIN IMMEDIATE`。
- finalize 校验各节真实标题/正文/section_end 与整篇 document_end，ZIP/CRC、重新打开、表格与图片数量一致，再经资料库登记与字节验证才给下载入口。
- 草稿不算交付；账号删除时清理私有草稿、版本与批次回执。
- 排版按 Skill：A4、2.2cm 边距、正文 10.5pt/1.25 倍行距、中文微软雅黑/英文 Arial、标题蓝 2F62AB、真实可编辑表格与题注、图片等比。

本轮复核发现并修复的一处真实缺陷：文件名含空格/括号/超长时旧发布器会规范化名称，而 finalize 仍按原名比对资料库记录，导致**文件已登记却报 publication_unavailable**。现改为校验资料库实际 filename，并以 `requested_filename` 保留重试身份（`test_word_tools.py::test_publisher_normalized_filename_is_verified_and_reusable`）。

### 3.8 本轮复核额外发现并修复的六处

| 问题 | 触发 | 修复 |
| --- | --- | --- |
| 重试硬上限可被并发绕过 | `RetryLedger.begin` 先 SELECT 再 UPDATE，无事务锁；两个并发发布者可读到同一计数 | `BEGIN IMMEDIATE` + 连接 30s 超时，计数与自增成为一步；6 线程并发只放行 2 次 |
| 同名不同生产工具的追加被误拒 | `_artifact_row_by_name` 只按 session+filename 取最新一条，命中别的工具的文件后报 mismatch | 先按 producer_tool 精确取；确无本工具文件且存在他人同名文件时，给出明确可操作提示 |
| Word 参数类错误不可自动修参 | 失败码 `invalid_argument` 等不在 `safe_tool_step` 的可重试集合内 | 参数/容量类错误归一为 `invalid_arguments`（可缩小批次重试）；授权、冲突、完整性类保持不可盲重试 |
| 绘图请求识别不到 | 输出动作词表缺"画/绘制"，`画一张流程图 SVG` 不产生需求 | 补入动作词，同时把"如何绘制/怎么画"纳入讨论排除，避免把提问当交付 |
| 文献条目静默截断 | `_render_reference_export` 内部再切 `rows[:2000]`，与入口限制重复且会掩盖调用方错误 | 移除内部切片；批量上限只在入口判定并明确拒绝，渲染器不再丢行 |
| 审查发现静默截断 | `findings[:20000]` 直接丢弃超量条目，补充材料工作簿完全看不到截断事实 | 保留资源上限但在清单内追加 `findings_truncated` 说明，写明未列出条数与"不能判断其余无异常" |

证据：`test_deliverable_check.py::test_concurrent_publishers_cannot_exceed_the_retry_budget`、`test_single_report.py::test_append_uses_the_producing_tools_file_not_another_tools_same_name`、`test_word_tools.py::test_batch_size_fault_is_correctable_but_conflicts_are_not`。

### 3.9 Skill 接入与气泡收敛

- Skill 原样安装到 `01_src/src/prompts/skills/word-export/SKILL.md`；仅在正向 Word 需求且 `word_document` 可用时注入，不进固定 QA 前缀；缺失则报错不假装加载。
- 修复 `ensure_fixed_prompts(reload=True)` 只清外层缓存的问题，现同时清 `_read_prompt_cached`。
- Word 成功交付后气泡只给简短说明与下载入口（`document_delivery.py`），失败/未完成不压缩。

## 4. 测试证据

复跑命令（仓库根目录）：

```bash
python engineering/word_delivery_20261112/run_checks.py
```

三组各自独立进程，避免 API 测试在 import 时改写 `config.PROJECT_ROOT` 污染其他测试。日志与 JUnit 记录同目录：

| 组 | 结果 | 记录 |
| --- | --- | --- |
| main（Word 核心/适配、发布、契约、路由、能力、审查、笔记、生命周期、账号删除、文件归属） | **231 passed** | `main.log` / `main.xml` |
| word_download（真实账号下载接口字节与跨账号 404） | **1 passed**（1 条 starlette 弃用警告） | `word_download.log` / `.xml` |
| attachment_boundaries（既有 PDF/XLSX/DOCX 附件边界） | **14 passed** | `attachment_boundaries.log` / `.xml` |

合计 246 项，0 失败、0 跳过。全部使用临时目录与无科学结论的合成文本，无模型请求、无网络、无真实科研数据。

过程中修正的是**测试与实现脱节**，不是放宽生产契约：`test_tool_bindings.py` 原先只打印 PASS/FAIL 不 assert（pytest 下假通过）且工具数写死过时，已改真实断言与真实 20 项；visibility/routing 改为显式健康环境夹具，不再依赖本机是否配置密钥；audit 测试按表头定位列并适配 `(text, artifact)` 返回；publish guard 的跨轮覆盖断言改为不可变新版本断言。

## 5. 变更清单

- `source_changes.patch`：`git diff --binary` 全量补丁（含新增文件，已用 `git add -N -f` 登记，**未 commit**）。
- `P0_manifest.csv`：36 个源码/测试文件的路径、新增或修改、字节数与 SHA-256。
- 新增依赖声明：`requirements-base.txt` 增 `python-docx>=1.2.0,<2.0`。

## 6. 未覆盖与已知边界（不夸大）

1. **未做视觉验收**：本机没有 LibreOffice/pdftoppm，未渲染 DOCX 页面抽检首页/表格页/末页。已做的是 OOXML 结构、可重新打开、真实表格与图片数量、下载字节与可编辑再保存检查。
2. **未做在线模型验证**：profile 自动选择、长文写作质量、真实科研结论均未经模型运行验证；本轮只验证后端受控执行与交付工程链路。
3. **不支持任意既有 DOCX 保真编辑**、修订跟踪与期刊模板导入；工具与提示已明确要求如实告知。
4. **完整性 ≠ 科学正确性**：校验保证已提交内容与结构可恢复，不能证明模型未遗漏未写入大纲的内容。
5. **跨进程追加**：旧 append 路径目前只有进程内串行 + 批次幂等 + 重试预算原子性，不宣称多 worker 同名追加的完整一致性。Word 草稿自身用数据库 CAS。
6. **草稿空间**：每版保存完整 DOCX 快照，空间随版本增长；已接入账号删除清理，尚无按会话闲置 TTL 的自动回收。
7. **外部 ReadTimeout 不会因本轮修复消失**：服务商超时属外部条件，本轮只保证失败有界、可恢复、状态诚实。
8. **服务未重启**：改动需受控重启后生效；本轮未重启、未停止、未发起重试服务。
