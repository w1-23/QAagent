# CitrusScholar 工具执行架构：最小改造方案

状态：待用户确认，**尚未修改任何代码**。
制定时间：2026-10-30。基线：本地仓库 `ff58b1f`，与服务器 `/home/WZQ/CitrusQA/01_源码_src` 在下列 6 个待改文件上 LF 归一 sha256 完全一致。

## 0. 一句话结论

不重写 General Agent，不新增 Agent/规划器/Intent 类，不扩关键词强制组。
**删掉「关键词决定工具可见性」这一层，新增两个小模块（能力过滤 ~90 行、结果验证 ~80 行），改一张 17 行的注册表。**

净效果：`_scoped_toolset()` 从 90 行降到 ~25 行；13 个 `_explicit_*` 从「可见性闸门」降级为「执行期约束与结果验证信号」。

## 1. 现状事实（可逐条核对）

| 事实 | 位置 |
|---|---|
| 注册表 17 个工具，已声明 category / concurrency_key / write_scope | `src/tools/__init__.py:47-65` `_TOOL_DECLARATIONS` |
| L0 能力天花板：17/17 全放行，12 个受 `CITRUS_RESEARCH_TOOLS_ENABLED` | `src/graph/general_agent_graph.py:770-786` `_toolset()` |
| L1 来源路由：10 条正则 → auto/local/web/fusion | `general_agent_graph.py:101-140` `_source_scope()` |
| L2 请求级绑定：13 个关键词检测器决定 selected | `general_agent_graph.py:392-482` `_scoped_toolset()` |
| L3 强制执行：仅 4 组 | `general_agent_graph.py:1084-1114` `required_tool_groups` |
| 执行器：超时、批量上限 8、重复调用识别、权限 fail-closed | `src/tools/registry.py:263-396`、`general_agent_graph.py:49,923-943` |
| 真实执行记录（时间/状态/摘要/落库） | `src/core/progress_bus.py`、`src/core/turn_status.py`、`messages.execution_json` |
| 文件登记：可按 `client_request_id` 查本轮真实产出 | `src/session/files.py:720` `list_turn_files()` |
| `PERMISSION_MODE=auto_workspace`（`ask` 人工审批分支当前未启用） | 实测 `settings.PERMISSION_MODE` |

## 2. 目标架构 → 现有代码落点

| 目标段 | 落点 | 动作 |
|---|---|---|
| 能力注册 | `_TOOL_DECLARATIONS` | 改：加 `requires / produces / intent_gate` 三列 |
| 环境与权限过滤 | 新增 `src/graph/tool_availability.py` | 新增 |
| 模型自主选择 | `general_agent_node` 主循环 | **不动** |
| 统一执行器 | `src/tools/registry.py` | 不动，仅接一个请求级 `denied_tools` |
| 结果验证 | 新增 `src/core/deliverable_check.py` | 新增，替换 `required_tool_groups` |

## 3. 改什么

### 3.1 注册表加三列（`src/tools/__init__.py`）

`(tool, category, concurrency_key, write_scope)` → `(..., requires, produces, intent_gate)`

| 工具 | requires | produces | cost | intent_gate |
|---|---|---|---|---|
| deepseek_search | net | — | low | — |
| deepseek_fetch | net | — | low | — |
| deepseek_fetch_paper | net | — | med | — |
| local_hybrid_search | local_index | — | med | — |
| draw_svg | — | png/svg | low | — |
| read_account_file | account_files | — | low | — |
| inspect_uploaded_table | attachment:any | — | low | — |
| audit_uploaded_data | attachment:table | html | high | — |
| verify_uploaded_references | attachment:paper | — | med | — |
| fetch_and_audit_open_supplements | net | — | med | — |
| export_literature_evidence | — | xlsx,ris,bibtex | med | — |
| export_paper_analysis | — | html | med | — |
| export_experiment_design | — | html | med | — |
| export_research_notes | — | html | med | — |
| export_corpus_gap_report | — | html | med | — |
| save_research_note | account_write | — | low | 需明确保存意图 |
| recall_research_notes | account_files | — | low | — |

要点：
- `requires` 只描述**环境事实**，不描述问题语义。
- 4 个 `export_*` 只产出 HTML，`export_literature_evidence` 才产 xlsx/ris/bibtex。这就是"不能拿 HTML 冒充 Excel"的结构化依据。
- `net` 用「配置是否可用」判定（`/api/v2/config` 的 `web_search.configured`），不做请求期网络探测。

### 3.2 新增 `src/graph/tool_availability.py`（~90 行）

```python
@dataclass(frozen=True)
class Env:                      # 每请求构造一次
    research_enabled: bool
    net_ok: bool
    local_index_ok: bool
    has_any_attachment: bool
    has_table: bool
    has_paper: bool
    has_account_files: bool
    account_writable: bool
    source_scope: str           # auto | local | web | fusion
    allow_retrieval: bool       # 来自 tool_policy（用户显式约束）

def available_tool_names(env: Env) -> list[str]:
    # 只做环境事实过滤；唯一的查询派生规则是用户显式来源限制
```

- 唯一保留的查询派生：`allow_retrieval=False`（"不要检索"、纯寒暄、仅读上传论文）、`scope=local/web`。
- **完全不看**"审查/导出/补充材料/画图/表头"等词。

### 3.3 `_scoped_toolset()` 缩到 ~25 行

删除 `general_agent_graph.py:405-465` 中 13 个检测器参与可见性的全部判断；保留 `policy` 的来源否决与 scope 交叉白名单（并入 availability）。函数签名与环境构造不变，调用点不动。

### 3.4 `required_tool_groups` → 结果验证（`src/core/deliverable_check.py`，~80 行）

不看"有没有调用某个工具名"，只看三类可验证事实：
1. `progress_bus` journal 的工具终态（status / summary / elapsed）
2. `files.list_turn_files(session, uid, [client_request_id])` 的真实产出文件与扩展名
3. 本轮是否被来源限制拒绝过（`denied_tools` 命中记录）

规则表（5 条）：

| 用户要求 | 判定为完成的条件 | 未满足时 |
|---|---|---|
| 检索（本地/联网/融合） | 对应工具 status=complete 且 summary 含结果计数（0 条也算完成） | partial + `unmet=retrieval` |
| 核验 DOI/参考文献 | `verify_uploaded_references` complete，或有真实联网核验记录 | partial + `unmet=reference_check` |
| 导出 | 本轮真实登记了文件，且扩展名 ∈ 该工具 `produces` | partial + `unmet=export:<fmt>` |
| 数据审查 | `audit_uploaded_data` complete 且登记了 HTML 报告 | partial + `unmet=audit` |
| 保存笔记 | `account_record` 写入被确认 | failed + `unmet=note_save` |

输出进 `done` 事件的 `turn_status` 与 `unmet_requirements`，并在历史行沿用（B1-B3 已有落库通道）。

### 3.5 执行期硬约束（模型想违规也做不到）

| 约束 | 实现 | 返回 |
|---|---|---|
| 用户"只本地/只联网/不要联网" | 请求级 `denied_tools`，在 `_run_single_tool` 执行前判定 | `[ERR_SOURCE_SCOPE]` |
| 无表格却审查表格 | availability 不给 + 执行器二次校验（`get_request_tables()`） | `[ERR_NO_ATTACHMENT]` |
| 保存笔记无明确意图 | `intent_gate`：无保存意图且未人工授权 → 拒绝 | `[ERR_INTENT_REQUIRED]` |
| 导出格式不支持 | 请求格式 ∉ `produces` → 不生成，直接说明 | `[ERR_FORMAT_UNSUPPORTED]` |
| 重复失败调用 | 沿用现有 `[TOOL_DUPLICATE_SKIPPED]`，并加"同一工具连续失败 2 次即停止重试" | `[TOOL_DUPLICATE_SKIPPED]` |

## 4. 13 个关键词检测器的去留

| 函数 | 作为可见性闸门 | 处置 |
|---|---|---|
| `_explicit_literature_request` | 是 | **删除**（无其他必要用途） |
| `_explicit_data_audit` | 是 | 保留，仅用于"用户要求审查"→ 结果验证 + 高成本预算 |
| `_explicit_reference_check` | 是 | 保留，仅用于结果验证 |
| `_explicit_supplement_request` | 是 | 保留，仅用于结果验证与提示 |
| `_explicit_export_request` | 是 | 保留，仅用于结果验证（要文件） |
| `_explicit_experiment_design_request` | 是 | 保留，仅用于提示与"设计+导出"验证 |
| `_explicit_paper_analysis_request` | 是 | 保留，仅用于提示 |
| `_explicit_note_save_request` | 是 | 保留，改作**执行期授权依据** |
| `_explicit_note_recall_request` | 是 | 删除（笔记读取按需可见） |
| `_explicit_gap_export_request` | 是 | 删除（导出类全可见，模型自选） |
| `_explicit_visualization_request` | 是 | 保留，仅用于提示 |
| `_explicit_local_tool_request` | 是 | 保留，作为**用户显式指令**（属约束，非主题猜测） |
| `_local_corpus_matches` | 是 | 删除其可见性作用（本地检索恒可见） |

`_source_scope()` 全部保留——它表达的是用户显式来源约束，属于"权限/环境"，不是"主题猜测"。

## 5. 必须保留不动（不得削弱）

1. 本地 / 联网 / 融合检索与其参数、语料族清单
2. 用户显式来源限制（只本地 / 只联网 / 不要联网）
3. 账号与会话权限隔离（`files.py` 归属校验）
4. 私有实验方法访问限制
5. 表格读取与数据审查（含 `_AUDIT_CATEGORIES` 与 HTML 报告）
6. 参考文献核验（DOI/题名/作者/期刊/年份）
7. 研究笔记保存与读取
8. HTML 等科研成果导出
9. 已修复的执行时间、状态、摘要、历史落库
10. `_check_tool_sandbox` 的 fail-closed 语义

## 6. 回归测试（20 条 → 断言形式）

**A 静态绑定（不调模型，10 条）** — 断言 `available_tool_names(env)` 含/不含：
1. "你好" → 直接回答，`allow_retrieval=False`，无检索工具
2. "柑橘木虱的防治措施有哪些" → 含 `deepseek_search` + `local_hybrid_search`
3. "只查本地文献，不要联网" → 不含任何 `deepseek_*`
4. "只联网，不用本地资料" → 不含 `local_hybrid_search`
5. "这个表里有什么" + 表格 → 含 `inspect_uploaded_table`
6. "看看" + Excel → 含 `inspect_uploaded_table` + `audit_uploaded_data`，但 `wants_audit=False`
7. "分析这个表格是否存在异常" + 表格 → 含 `audit_uploaded_data`
8. "帮我找这篇论文的补充材料" → 含 `fetch_and_audit_open_supplements`
9. 未上传表格 → 不含 `inspect_uploaded_table` / `audit_uploaded_data`
10. `CITRUS_RESEARCH_TOOLS_ENABLED=0` → 11 个受控工具全不可见

**B 执行期约束（5 条）** — 断言拦截码：
11. 只本地时调用 `deepseek_search` → `[ERR_SOURCE_SCOPE]`
12. 无表格时调用 `audit_uploaded_data` → `[ERR_NO_ATTACHMENT]`
13. 无保存意图时调用 `save_research_note` → `[ERR_INTENT_REQUIRED]`
14. 请求 xlsx 时调用只产 HTML 的导出 → `[ERR_FORMAT_UNSUPPORTED]`，且不登记文件
15. 同一工具同参数连续 2 次 → 第 2 次 `[TOOL_DUPLICATE_SKIPPED]`；不同参数 → 两次都执行

**C 真实模型跑（5 条，`experiments/trace/collect.py` 采集落盘）**：
16. "核对这篇论文的 DOI" → 必须出现核验工具调用或 `turn_status=partial`
17. "导出实验方案" → 必须登记真实文件
18. "把结果导出成表格" → 不得声称生成 xlsx（除非确有 .xlsx 产出）
19. "保存这条研究笔记" → 确认写入；"不要保存这段内容" → 无写入
20. 工具超时 → `turn_status` 如实为 `partial/failed`，且不无限重试

断言同时覆盖：可见性、实际调用、结果状态、执行次数、超时。

## 7. 开发顺序（严格执行）

1. 本地检查（`E:\codex_WORKSPACES\Citrus_QA_Agent`，已确认 = 服务器，clean @ `ff58b1f`）
2. 本地修改
3. 本地测试（`python.exe -m pytest`，含 A/B 组新测试 + 既有 19 例）
4. 本地 git 提交
5. 服务器部署（同步 6 个文件，命令行重启，curl 200）
6. 服务器验证（C 组真实模型跑 + trace 落盘）
7. **用户确认后**才推送 GitHub（当前 `origin` 已移除，需先确认远端）

禁止：先在服务器改再反向同步；触碰 `.env` / API Key / 用户数据 / 私有语料 / 运行配置。

## 8. 风险与回滚

| 风险 | 缓解 |
|---|---|
| 每请求工具数 3 → 13，schema token 增加 | 实测单请求 token 增量；超阈值再评估分层披露 |
| 模型过度调用 `audit_uploaded_data`（贵） | `cost=high` 每次请求预算 1 次 |
| 模型频繁保存笔记 | `intent_gate` + `account_record` 权限 |
| 结果验证误判 partial | 只对"用户显式要求"生效；默认可完成 |

回滚：改造集中在 2 个新文件 + 3 处改动，单个 commit `git revert` 即可恢复；无数据迁移。

## 9. 需用户拍板的 3 点

1. 是否同意 17 个工具在环境允许时**全部下发**（不设上限）。
2. 导出仅 HTML 的 4 个工具：确认「不能满足 xlsx 时明确说明」，本次不新增 xlsx 能力。
3. 保存笔记的授权：按文本意图（`_explicit_note_save_request`）放行，或改走人工审批卡片（需把 `PERMISSION_MODE` 切到 `ask`，会影响所有写操作）。