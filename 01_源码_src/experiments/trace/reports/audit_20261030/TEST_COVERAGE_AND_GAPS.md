# 测试覆盖现状与缺口

基线：本地仓库 HEAD `80bab4b`。最近一次全量：**173 passed / 0 failed**。
（此前基线 `ff58b1f` 为 145 passed / 2 failed，两个既有失败已在 `f51afbd` 修复并单独提交。）

运行方式（本地）：

```powershell
cd 'E:\codex_WORKSPACES\Citrus_QA_Agent\01_源码_src'
& 'E:\anaconda\envs\rag-agent\python.exe' -m pytest -q
```

---

## 1. 现有测试清单（`01_源码_src/src/**/test_*.py`，27 个文件）

| 类别 | 文件 | 覆盖的行为 |
|---|---|---|
| 工具可见性/绑定 | `test_tool_visibility.py`、`test_tool_bindings.py`、`test_general_agent_routing.py`、`test_prompt_inventory.py` | 环境决定可见性（12 例）、工具声明自洽、路由回归、提示词清单与工具名一致 |
| 执行记录/状态 | `test_progress_events.py`、`test_turn_status.py`、`test_turn_notices.py`、`test_outcome.py`、`test_lost_constants.py` | 事件字段（含 `elapsed_ms`/`status`/`reason_code`）、轮次状态推导（9 例）、单一事实源文案、失败分类、曾静默失效的模块常量 |
| 文件/会话一致性 | `test_files_lifecycle.py`、`test_file_turn_refs.py`、`test_artifact_lifecycle.py`、`test_file_safety.py`、`test_attachment_safety.py` | 上传→登记→轮次引用→删除/过期；悬空 ref 不残留；路径/大小/类型安全 |
| 语料契约 | `test_corpus_index_contract.py`、`test_corpus_registry.py`、`test_embedder_prefix.py` | 新语料元数据契约、批次注册、嵌入前缀 |
| 权限/账号 | `test_account_profiles.py`、`test_account_deletion.py`、`test_chat_capacity.py`、`test_admin_dashboard.py` | 账号隔离、删除、并发容量、后台 |
| 科研工具 | `test_audit_tools.py`、`test_research_notes_and_citation_exports.py`、`test_executors.py` | 数据审查、笔记、XLSX/RIS/BibTeX 导出、执行器 |
| 运行时契约 | `test_runtime_contracts.py`、`test_staged.py`、`test_detector_regressions.py` | 配置口径、分阶段门禁、检测器回归 |

## 2. 已知失败（已清零）

| 测试 | 原症状 | 处置 |
|---|---|---|
| `test_first_chat_upload_is_registered_to_server_created_session` | 桩函数签名/返回与实现漂移 | `f51afbd` 修桩，不改生产逻辑 |
| `test_idle_cleanup_...` | 清理计数硬编码期望 0，与真实清理量不符 | 同上 |

## 3. 覆盖缺口（按风险排序）

| # | 缺口 | 为什么危险 | 建议测试 |
|---|---|---|---|
| 1 | **断线/取消** | 客户端断开即取消整轮（`main.py:1456-1467`）；无测试证明“取消后数据库状态、文件引用、配额”三者一致 | 模拟 `CancelledError`：断言 `jobs.status=cancelled`、无半成品文件、`daily_usage` 不虚增 |
| 2 | **失败语义不回灌** | `auth/configuration_missing/quota` 只做展示标签（`outcome.py:109-125`）；真实出现“同一搜索连失败三次” | 断言：该三类失败第二次尝试时提示里带“本轮重试无效”，且 `required_tool_groups` 视为已尝试 |
| 3 | **`allow_retrieval=False` 边界** | 检索工具整组消失的触发条件由文本决定（`tool_policy.py:57-67`） | 20 条真实问句快照测试：哪些被判为不需检索，逐条人工确认 |
| 4 | **多请求并发隔离** | ContextVar 队列、`client_request_id`、`turn_refs` 都按请求隔离，但无并发测试 | 同一会话两轮并发 + 两账号同名文件并发，断言事件不串、引用不串 |
| 5 | **公开说明通道（尚未实现）** | 一旦新增 `public_note`，必须有“生成失败不影响执行”的护栏 | 让 `emit_public_note` 抛异常：工具仍执行、答案仍生成、`turn_status` 不变 |
| 6 | **真实模型端到端** | 现有 173 例全是静态/桩测试，无法发现“模型不调工具”“连续失败重试” | 已有 `experiments/trace/collect.py`（服务器）与 `collect_local.py`（本机），建议固定 5 个场景做冒烟基线 |
| 7 | **语料版本兼容** | 本地旧语料 11 批全被隔离（缺 `source/source_type/source_file`），无测试防“换了语料就静默降级” | 启动自检：批次被隔离时在 `/health` 暴露计数，并有测试断言非零即告警 |

## 4. 一句话结论

静态与契约层覆盖是够用的（173 例、27 个文件、按子系统分区），**缺口集中在三处：取消语义、失败语义、并发隔离**。这三处恰好都是“出事时用户很难归因”的地方，建议下一轮优先补测试而不是先补功能。
