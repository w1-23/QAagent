# R3 只读专项：General Agent → Skill → Worker 图件调用链（CitrusScholar）

> 审计性质：只读静态审计。未启动应用，未运行 pytest，未启动或访问 8031/8042，未读写真实 DB / 账号 / 凭据 / 附件 / 日志，未执行远端脚本，未部署，未提交或推送。
> 基线：远程 Linux 隔离候选仓库。
> 分支：`feat/invite-auth-nature-figure`；HEAD：`efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`（`efeb7ed feat: add interactive Browse with tabs and bounded concurrency`）。
> 审计开始时工作树为空；随后 `docs/` 目录由并行审计创建（`auth-current.md`、`nature-inventory.md` 已存在）。本次只新增本文件，未改源码 / 测试 / 配置。
> 所有“通过/失败/生成/重放”均指源码路径上的可预期接线，不是实际运行结果。未运行项在 §6.3 明列。

---

## 1. 工单目标

只读核对当前图件运行时调用链：General Agent 如何发现并调用图件工具，Skill 如何只作方法指导而不授权，声明式图与自由脚本 output v2 如何进入隔离 Worker，宿主如何发布与做作品重审；重点核实 `vendor_root=None`、PDF/SVG 字节关联、Web 不执行不可信脚本、未知图型可编辑边界，以及 `draw_svg` 到 `/figures` 工作台的实际断点。输出可交给主会话的接缝建议与 FIG-01–20 验收路径，避免跨所有权改动。

## 2. 读过文件

后端 Agent / Skill（全量或指定区段）：
- `01_src/src/agent/host.py`（全量 735 行）
- `01_src/src/agent/host_policy.py`（全量 66 行）
- `01_src/src/agent/skill_adapter.py`（全量 357 行）
- `01_src/src/agent/skill_tools.py`（全量 104 行）
- `01_src/src/agent/research_skill_catalog.py`（全量 168 行）
- `01_src/src/agent/preset_catalog.py`（全量 211 行）
- `01_src/src/prompts/loader.py`（全量 223 行）
- `01_src/src/graph/scene_skills.py`（全量 72 行）
- `01_src/src/graph/general_agent_graph.py`（全量 57 行）
- `01_src/src/graph/tool_policy.py`（全量 83 行）
- `01_src/src/graph/tool_availability.py`（1–240 行）
- `02_config/skills.catalog.json`（全量 491 行）

API / 工具 / 脚本：
- `01_src/src/api/figure_router.py`（全量 421 行）
- `01_src/src/api/main.py`（545–624、1470–1639、1770–1829、4860–4910 区段）
- `01_src/src/research/workspace_tools.py`（全量 235 行）
- `01_src/src/research/figure_tools.py`（全量 720 行）
- `01_src/src/research/script_execution.py`（全量 240 行）
- `01_src/src/research/script_runtime.py`（全量 290 行）
- `01_src/src/research/script_presets.py`（35–70 行及函数索引）
- `01_src/src/tools/draw_svg.py`（全量 99 行）
- `01_src/src/core/figures.py`（全量 114 行）
- `01_src/src/core/tool_catalog.py`（57–110 行及条目）

图件包与引擎：
- `packages/citrus_figures/spec.py`（全量 578 行）
- `packages/citrus_figures/bundle.py`（全量 248 行）
- `packages/citrus_figures/verification.py`（全量 206 行）
- `packages/citrus_figures/render_policy.py`（全量 75 行）
- `packages/citrus_figures/nature_runtime.py`（全量 89 行）
- `01_src/src/figures/nature_runtime.py`（全量 211 行）
- `01_src/src/figures/render_policy.py`（全量 61 行）
- `01_src/src/figures/worker.py`（全量 572 行）
- `01_src/src/figures/engine_bundle.py`（全量 129 行）
- `01_src/src/figures/current_artifact_api.py`（全量 368 行）
- `01_src/src/figures/projects.py`（全量 1135 行）
- `01_src/src/figures/store.py`（全量 527 行）
- `01_src/src/figures/trusted_render.py`（1–90 行）
- `01_src/src/figures/declarative_snapshot.py`（1–120 行）

Web：
- `01_src/web/figures.html`（172 行）
- `01_src/web/assets/figures.js`（1140–1299 行及关键符号）
- `01_src/web/assets/figures/scene_projection.js`（关键符号）
- `01_src/web/assets/app.js`（117、4225 行）

检索辅助（未逐行审计）：`01_src/src/prompts/skill_registry.py`、`01_src/src/prompts/skills/catalog.json`、`01_src/src/figures/sandbox.py`、`wsl_docker.py`、`apptainer.py`、`01_src/src/figures/script_outputs.py`、`01_src/src/figures/core.py`。

## 3. 写文件

- 新增：`docs/audits/figure-runtime-callchain.md`（本文件）。
- 未修改任何源码、测试、配置、前端、数据库或部署文件；未运行格式化 / 测试 / 服务；未提交、未推送。
- `docs/audits/auth-current.md`、`docs/audits/nature-inventory.md` 为并行审计产物，非本次写入。

## 4. 接口（确切注册函数与调用点）

### 4.1 真路径：General Agent 入口

1. `01_src/src/graph/general_agent_graph.py:build_general_agent_graph()` 组装 `session_load → candidate_prefetch → supervisor → session_save`，`supervisor` 节点绑定 `src.agent.host.general_agent_node`。
2. `01_src/src/api/main.py:@app.post("/api/v2/chat")`（1490）→ `_chat_v2_admitted()`；请求模型 `ChatRequest.visualization_enabled: bool = False`（579），`initial_state["visualization_enabled"] = bool(req.visualization_enabled)`（1795）。Web 客户端 `01_src/web/assets/app.js:117` 固定 `visualizationEnabled = true` 并在 4225 行随 chat 请求发送。
3. `general_agent_node()`（`host.py:30`）→ `_run()`（70）。`_run()` 中：
   - `build_preset_catalog()` 建立能力目录；
   - `register_context_reader` / `register_plan_tools` / `register_workspace_tools` / `register_code_tools` 追加工具；
   - `refresh_allowed()`（127）以 `allowed_presets(state, ...)` 计算本次可公布工具；
   - `SkillSession(identity, refresh_allowed)`（143）与 `register_skill_reader(catalog)`（144）；
   - `CapabilityExecutor(catalog, refresh_allowed, invoke_scoped, host.publish)`（201）；
   - `execute()`（326）在 `names & {"research_workspace","run_research_figure_script"}` 时绑定 `script_budget_scope`（`bind_task_scope`，336），然后 `scheduler(response, identity)`。

### 4.2 工具注册、可发现、可调用

- **注册**：`preset_catalog.py` 的 `_TARGETS` 把工具名映射到模块，`_load()` 用 `getattr(import_module(...), name)` 取实现并调用 `src.tools.registry.register_tool(...)`；`build_preset_catalog()` 只登记 `CapabilityDefinition`，按需导入。`figure_tools` 的两个工具均在此表：`create_research_figure`、`run_research_figure_script`。
- **Skill 工具**：`skill_tools.register_skill_reader(catalog)` 注册 `read_skill`、`load_skill`；两者通过 `_current_session()` 读取 `skill_execution_context`，未绑定上下文时 `PermissionError("skill_execution_context_required")`。`host.py:invoke_scoped()`（193）在 `skill_execution_context(skills)` 内调用 `host.invoke`，因此 Skill 上下文按请求绑定，不在注册期全局绑定。
- **可发现（host_policy）**：`allowed_presets()`（host_policy.py:9）用 `preset_bindings()` 的 `requires` 与 `TOOL_METADATA[name].capabilities` 过滤；`if not state.get("visualization_enabled", True) and "visualization" in caps: continue`（32）。因此：
  - `create_research_figure`：需 `research_tools + session`，且 `visualization_enabled=true`；
  - `run_research_figure_script`：需 `session + research_tools + script_sandbox`，且 `visualization_enabled=true`；
  - `draw_svg`：`requires=()`，但 `TOOL_METADATA["draw_svg"].capabilities = {VISUALIZATION, ARTIFACT}`，`visualization_enabled=false` 时同样不公布；
  - `research_workspace`：需 `session + research_tools`（`RESEARCH`），不受 `visualization` 门控。
- **可调用**：`CapabilityExecutor` 在真正派发前再次调用 `refresh_allowed()`；`host.invoke()` 保留身份、就绪与操作策略；Skill 只提供指导，`skill_adapter.py:allowed()` / `permits_call()` 明确返回宿主授权集合，不缩小权限。
- **Worker 前置**：`run_research_figure_script()`（figure_tools.py:655）先做参数纯校验，再 `_select_script_runner()`；无 runner 时返回 `_tool_outcome.status="not_run"`、`reason_code="script_execution_rejected"`，不保存脚本、不启动 Worker。`script_sandbox_available()`（tool_availability.py:133）按配置探测，`off` 默认不执行脚本。

### 4.3 声明式图（create_research_figure）

调用点：`create_research_figure(spec_json, title, formats_json, output_name)`（figure_tools.py:197）。

1. `_json_arg` → `src.figures.spec.parse_spec()`（spec.py:458）：拒绝未知字段、未知图型、越界尺寸/字号/DPI、超 200 行内联表、非法 `series_key`；`CHART_TYPES = ("bar","line","scatter","errorbar","boxplot","histogram")`。
2. `_persist_attachment_refs()`：把本轮 `attachment` 列引用复制为账号资料库 `account_file` 引用；失败则中止创建，避免“可打开但不可重绘”的项目。
3. `resolve_spec()` → `prepare_source(spec, resolved, source_info)`：`declarative_snapshot.prepare_source()` 生成 `resolved_snapshot`（`spec_sha256/resolved_sha256/sha256`），并立即 `load_snapshot()` 验证；不支持的图型（如无 `scientific_geometry` 的 histogram/boxplot）只保存 spec、不生成可编辑快照。
4. `render_resolved()`（`trusted_render.py`）：服务生成的 Python 在**新的受限子进程**执行，不是不可信脚本沙箱；`confirm_render(source, manifest)` 要求子进程回执 `resolved_data_sha256` 与快照一致。
5. `_publish()`（97）逐格式 `publish_artifact`；`_register_figure()`（115）把 SVG 交给 `src.core.figures.register()` 生成 `[[FIGURE:n]]`。
6. `_record_project()`（618）→ `store.create_project()` → `store.update_project(expected_revision=0, render_version=1)`；任一提交失败会删除未完成项目但保留已发布文件，并返回 `project_persistence_failed`。

### 4.4 自由脚本 output v2 与 preset / emit 冻结路线

调用点：`run_research_figure_script()`（655）→ `script_runtime.execute_script_request()`（101）→ `_run_research_figure_script()`（344）。

1. **请求级持久化**：`execute_script_request()` 先 `script_assets.save_script()` 得到 `script_ref`，再 `normalize_output_contract(outputs_json)`；源与 ref 二选一，`parameters_json` 只作为 `parameters` 注入脚本，不拼代码。
2. **预算 / 幂等**：`assets.begin_budgeted_run()` 以 `logical_task_id + request_key + environment + script_failure_limit` 约束；`running` 状态不重放。
3. **输出契约**：`output_slots(contract)` 生成 v2 槽位；`MAX_V2_FILES=64`，`render_script` 必须是可解析的 Python 且必须声明非空 `parents`。
4. **Worker 执行**：`_run_research_figure_script()` 先 `snapshot_script_inputs()` 冻结输入字节（账号/数据集引用、sha256、内部快照），再构造 `SandboxJob`：
   - v2 且含 `render_script`：`publish_script_outputs()`（script_execution.py:39）→ Worker `_run_outputs()`（worker.py:372）；
   - 旧协议（无 `outputs_json`）：单图 `SandboxJob` + `manifest` 槽位，走 `worker.run()` 的 legacy 单图分支。
5. **preset / emit 冻结路线**：
   - 内置脚本 `relationship_panels` 的 `outputs` 为 v2：`tables=[{"name":"relations_data","parents":["data"]}]`，`figures=[{"name":"relations", ..., "parents":["relations_data"], "render_script": _RELATIONSHIP_RENDERER}]`（`script_presets.py:35-60`）。
   - 主脚本只整理并 `emit.table("relations_data", df, parents=["data"])`；分离的 `render_script` 通过 `data['relations_data']` 取冻结 DataFrame，只构造一个 Figure。
   - Worker `capture_outputs(..., frozen_tables=...)` 对分离渲染计算 `frozen_render_receipts`（`renderer_sha256 / data_sha256 / geometry_status / geometry_sha256`），写入 `result.json` 与 `figure:<name>:manifest`。
   - 宿主 `_accept_frozen_render()`（script_execution.py:10）逐项核对：`receipt.version==1`、`renderer_sha256==sha256(render_script)`、`data_sha256=={table_hashes[parent]}`、`snapshot_script_inputs()` 的 sha 与回执一致；通过后才把 `frozen_render` 写入 `source`（version 3）。
   - 旧混写 v2（`emit.figure()`、无 `render_script`）仍可下载，但 `editable=False`，且提示“没有保真样式编辑凭证”。

### 4.5 宿主发布

- 工具路径：`figure_tools._publish()` → `src.research.artifacts.publish_artifact(owner, session_id, filename, content, mime, producer_tool, request_id)`；`_record_project()` 之后追加 `{"format":"project","figure_id","workspace_url":"/figures?project=..."}`。
- 工作台路径：`projects.publish_rendered()` → `projects._publish_bytes()` → `publish_artifact(producer_tool="figure_workspace")`；发布后再 `store.update_project(expected_revision=revision, render_version=revision+1)`，CAS 失败即 409。
- 重审路径：`current_artifact_api.verify_project()` → `publish_artifact(producer_tool=PRODUCER)` 发布 receipt / bundle；发布后再次 `_read_file()` 比对摘要与 `download_url`，不允许猜链接。

### 4.6 作品重审（verify / bundle）

- API：`figure_router` 的 `POST /api/v2/figures/projects/{figure_id}/verify` 与 `/bundle`（`_current_artifact_operation`）→ `current_artifact_api.verify_project()`。
- 绑定：请求体 `CurrentArtifactRequest` 只允许 `expected_revision` 与 `supersedes_file_id`（`extra="forbid"`）；`_previous_receipt()` 校验前次回执的 `producer_tool/origin/session/ bridge/version/skill/figure_id/render_version/receipt_id`，非法即 400。
- 读取：`_read_file()` 只经账号文件解析器取路径，校验普通文件、大小、sha256；`_capture()` 对每个导出格式核对 `entry["sha256"]` 与 `manifest.render_receipt.checks[fmt].sha256`，不一致即 409。
- 重审：`verify_current_figure(artifacts=..., source=..., backend=..., mode="reaudit"|"initial", supersedes=..., vendor_root=None)`（current_artifact_api.py:254-256）。
- 输出：`capabilities = {"upstream_scripts":"not_run","visual":"not_run","source_execution":"not_run","rendering":"not_run"}`；`overall.execution="partial"`，并追加“未执行上游脚本 / 源 / 渲染”的 claim。

### 4.7 Web 入口

- 页面：`01_src/web/figures.html` 加载 `edit_model.js`、`scene_projection.js`、`hit_geometry.js`、`figures.js`、`guide.js`；主 API 路由 `GET /figures`（main.py:3752）→ `figure_router.serve_figure_page()`，未登录 303 到 `/?next=/figures`，页面带 `CSP default-src 'none'; img-src 'self' data: blob:; script-src 'self'; ...`。
- 预览：`figures.js:showSvg()`（1147）用 `URL.createObjectURL(new Blob([svg], {type:"image/svg+xml"}))` 赋给 `<img>`；`loadSavedScene()` 先显示已保存 SVG，再 `blob.text()` → `new window.FigureScene(svg, manifest)` 做本地对象投影。
- 本地投影：`scene_projection.js` 用 `DOMParser().parseFromString(svg, "image/svg+xml")`，只对白名单属性 `setAttribute` / `textContent`；属性值命中 `javascript:|data:|expression\s*\(` 时拒绝；未把服务器 SVG 作为 `innerHTML` 注入。
- 保存：工作台 `save_overrides()` 通过 `POST /api/v2/figures/projects/{id}/overrides` → `projects.save_overrides()`；脚本来源最终仍由 `_render_script()` 交给隔离沙箱，声明式来源由 `render_resolved()` 子进程完成。

### 4.8 旧兼容路径（不是当前 chat 真路径）

- `01_src/src/prompts/loader.py:assemble_system_prompt()` 会追加 `scene_catalog_prompt()` 与 `figure_scene_guidance()`，其中包含“nature-figure 核心/contract/stance 已在上方实际注入”和 `figure-initial` / `figure-edit` 的强制读取指引；`verify_figure_prompt_loading()` 也只验证该 assembler。
- 该 assembler 的调用点只有 `main.py` 管理员 `GET /api/v2/session/{id}/context-detail`（4888-4895）；`agent_operations.py:47` 仅有 import，未发现调用。因此**它不是 `general_agent_node` 的实际系统提示词**。
- 旧工具注册：`01_src/src/tools/legacy_registry.py` 仍存在并与 `preset_catalog._PRESETS` 保持快照等价测试；生产发现走 `preset_catalog`，不 eager import 旧注册表。
- 独立发布包：`packages/citrus_figures/cli.py` 的 `write_bundle(vendor_root=...)` / `verify_current_figure(vendor_root=...)` 是包级 CLI 路径，不是服务 Worker 路径；服务 Worker 使用 `src/figures/nature_runtime.py` 的固定 `_VENDOR_ROOT`。

## 5. 实际命令

以下命令均在远端同一仓库执行；文件内容读取使用 portal-ssh `remote_read`（只读，不执行代码）。未执行 pytest、未启动服务、未发 HTTP、未读 DB。

基线 / 工作树：
- `cd <candidate-repo> && pwd && git rev-parse --abbrev-ref HEAD && git rev-parse HEAD && git status --short | head -40 && git worktree list`
  - 结果：分支 `feat/invite-auth-nature-figure`；HEAD `efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`；工作树无改动。
- `git status -sb`（审计中段）：仅 `?? docs/`（并行审计新增目录）。
- `git worktree list`：该路径 `efeb7ed [feat/invite-auth-nature-figure]`。

定位 / 规模：
- `find . -maxdepth 3 -type d \( -name figures -o -name citrus_figures -o -name research -o -name prompts -o -name web \) ...`
- `wc -l 01_src/src/agent/{host,host_policy,skill_adapter,skill_tools,research_skill_catalog,preset_catalog}.py 02_config/skills.catalog.json`
- `wc -l 01_src/src/api/figure_router.py 01_src/src/research/{workspace_tools,figure_tools,script_execution,script_runtime}.py 01_src/src/tools/draw_svg.py`
- `wc -l packages/citrus_figures/{spec,bundle,verification,render_policy,nature_runtime}.py`
- `wc -l 01_src/src/figures/{nature_runtime,render_policy,worker,engine_bundle,current_artifact_api,projects,store}.py`

关键检索（`grep -rn`，均在 `01_src/src`、`packages`、`01_src/web` 内）：
- `vendor_root`、`verify_current_figure`、`DEFAULT_BLOCKING`、`CHECKS`
- `assemble_system_prompt`、`scene_catalog_prompt`、`figure_scene_guidance`、`get_system_prompt`
- `figure-initial`、`figure-edit`、`selected_messages`、`directory_prompt`
- `visualization_enabled`、`selected_skills`、`create_research_figure`、`run_research_figure_script`
- `draw_svg`、`script_sandbox_available`、`worker.py`、`--job`、`stage_engine`
- `innerHTML|outerHTML|insertAdjacentHTML|DOMParser|Blob|svg`

区段读取（`sed -n` / `remote_read`）：
- `host.py` 1–285、286–543、544–735
- `figure_tools.py` 1–363、364–630、631–720
- `projects.py` 1–299、300–606、607–666、660–941、942–1026、1020–1135
- `current_artifact_api.py` 1–308、309–368
- `worker.py` 1–363、364–572
- `store.py` 1–332、333–527
- `api/main.py` 545–624、1470–1639、1770–1829、4860–4910
- `prompts/loader.py` 全量、`graph/scene_skills.py` 全量、`graph/general_agent_graph.py` 全量、`graph/tool_policy.py` 全量、`graph/tool_availability.py` 1–240
- `figures.js` 1140–1299、`figures.html` 全量、`scene_projection.js` 关键符号

只读 JSON 解析（无网络、无写盘）：
- `python3 - <<'PY' ... json.load(open('01_src/src/prompts/skills/catalog.json')) ... PY`
- `python3 - <<'PY' ... json.load(open('02_config/skills.catalog.json')) ... PY`
  - 结果：旧 registry 含 `figure-initial (2.1, root=figure-export)`、`figure-edit (1.1)`、`nature-figure (2.8.0)` 等；`02_config` 根发布仅 `candidate-search`、`reference-verification`、`data-audit`、`nature-figure`。

## 6. 事实与未测试原因

### 6.1 已核实的源码事实（静态）

1. **两条 Skill 体系并存**：
   - 产品场景根发布：`src/agent/research_skill_catalog.py` 的 `PINNED` 与 `02_config/skills.catalog.json`，只含 `candidate-search`、`reference-verification`、`data-audit`、`nature-figure`；`skill_adapter.scenario_catalog()` 与 `GET /api/v2/skills` 返回这一组；`load_skill` / `read_skill` 经 `SkillSession` 读取。
   - 旧方法 registry：`src/prompts/skill_registry.py` + `prompts/skills/catalog.json`，含 `figure-initial (2.1)`、`figure-edit (1.1)`、`statistics`、`word-export`、`nature-figure (2.8.0)`；`research_workspace(operation='read_skill'/'read_skill_resource')` 读取这一组。
2. **Skill 不授权**：`skill_adapter.py` 的 `allowed()` / `permits_call()` 只返回宿主集合；`host.py:invoke_scoped()` 仅建立 `skill_execution_context`，真正准入仍是 `refresh_allowed()` + `CapabilityExecutor` + `host.invoke()`。Skill 读取失败为局部 `not_run`，不阻断其他工具。
3. **工具公布条件**：`allowed_presets()` 依据 `preset_bindings()` 的 `requires` 和 `TOOL_METADATA.capabilities`。图件工具均带 `VISUALIZATION` 能力；`ChatRequest.visualization_enabled` 默认 `False`，Web 显式传 `true`，非 Web 客户端默认看不到 `create_research_figure` / `run_research_figure_script` / `draw_svg`。`run_research_figure_script` 还要求 `script_sandbox_available()` 为真。
4. **声明式图**：`parse_spec()` 失败即拒绝，不做猜测修复；`prepare_source()` 只对受支持图型生成 `resolved_snapshot`；`render_resolved()` 在独立受限子进程执行服务生成的代码；`confirm_render()` 要求 `resolved_data_sha256` 完全一致；项目创建使用 `render_version` CAS。
5. **自由脚本 v2**：`outputs_json` 归一化为 `tables/figures` 契约；`render_script` 必须声明非空 `parents`；Worker 先跑主脚本、冻结表，再以冻结表跑独立渲染；`frozen_render_receipts` 记录 renderer/data/geometry 哈希。
6. **preset / emit 冻结路线**：内置 `relationship_panels` 明确走 `emit.table` + `render_script`；宿主 `_accept_frozen_render()` 要求回执与表哈希逐项一致，通过后 `source.version=3`，`projects._frozen_render_state()` 与 `_can_edit()` 仅在 `geometry_status=="supported"`、`geometry_sha256` 有效、`data_sha256` 与冻结快照一致、渲染脚本哈希一致时允许样式编辑。
7. **样式编辑不重算**：`projects._render_script()` 只把 `render_script` 和冻结表交给沙箱（`text="pass"`、`inputs` 来自 `frozen["input_snapshots"]`），并在接收前比对 `manifest.frozen_render_receipt == frozen["receipt"]`；不一致返回 409 `render_data_changed`。声明式路径 `render_resolved()` 也以 `resolved_snapshot` 和 `sources` 校验，再重新渲染样式。
8. **宿主发布**：工具路径与工作台路径最终都走 `publish_artifact`；发布后才更新项目；发布失败不回写项目。工作台保存使用 `expected_revision` CAS，且发布后再次 `_assert_current`/`update_project` 防覆盖。
9. **作品重审不执行源码**：`current_artifact_api` 只读取已存导出与输入，调用 `verify_current_figure(vendor_root=None)`；`_capture()` 校验每个格式的存储 sha 与 `render_receipt.checks[fmt].sha256`；`_previous_receipt()` 绑定 supersedes 的图件、会话、render_version 与 receipt_id。
10. **Web 发布/预览同一字节**：`figure_router` 的 `preview.svg/png` 与 `export/{fmt}` 都读取账号库中 `entry["file_id"]` 的同一文件；`FileResponse` 带 `nosniff` 与 `Content-Security-Policy: default-src 'none'; sandbox`；预览是 `<img>`，不是内联文档。

### 6.2 专项核查

**A. `vendor_root=None`**
- 唯一显式调用点：`01_src/src/figures/current_artifact_api.py:254-256` 的 `verify_current_figure(..., vendor_root=None)`。
- `packages/citrus_figures/verification.py:89-92`：`vendor_root is None` 时 `module()` 抛 `FileNotFoundError("vendor_root not supplied")`；`run()` 将其记为该 check 的 `reason_code="resource_absent"`，不是失败。
- `current_artifact_api._capture()` 随后把 `reason_code=="resource_absent"` 改写为 `"upstream_execution_not_authorized"`，清空 diagnostics；因此 `source_preflight`、`panel_alignment`、`pdf_text`、`pdf_collisions`、`svg_schematic`、`visual` 在 Web 重审中均为“未执行/未授权”，只有 `format_integrity` 实际运行；`overall.conclusion` 会降为 `incomplete`（若原本 pass/warn）。
- 这满足“Web 不执行不可信脚本/上游脚本”，但意味着**Web 重审不能认证 PDF 布局、PDF 文本/碰撞、SVG 示意图规则或视觉语义**。是否接受这一验收口径需主会话决定。
- 服务 Worker 初始生成不使用 `vendor_root` 参数：`src/figures/nature_runtime.py` 固定 `_VENDOR_ROOT = parents[1]/prompts/skills/vendor/nature-skills`，按 `provenance.json` 的 `source/commit` 与逐文件 sha 加载。

**B. PDF / SVG 字节关联**
- 包级路径（`packages/citrus_figures/render_policy.py`）：
  - `export_with_layout()` 在**一次** PDF 导出期间监听 `draw_event`，得到 `layout`，并写入 `{"artifact_sha256": sha256(pdf), "layout": ..., "capture": "pdf_draw_event", "rerendered": False}`；同一 `pdf` 字节放入 `blobs["pdf"]`，不会二次导出。
  - `verify_current_figure(panel_alignment)` 只在 `layout_manifest["artifact_sha256"]` 命中某个 artifact 的 sha 时执行；因此布局证据与**具体 PDF 字节**绑定。
  - `format_integrity` 分别对每个 artifact 做签名检查（PDF `%PDF-`、PNG、TIFF、JPEG、SVG XML），不是跨格式语义关联；PDF 文本/碰撞按每个 PDF blob 的 identity 绑定。
- 服务 Worker 初始路径（`src/figures/nature_runtime.audit_fig`）：
  - `panel_alignment` 用 `module.matplotlib_layout_manifest(figure)` 对**活 Figure** 取布局，然后 `pdf_factory()` 导出 PDF，并把 `pdf_sha256` 写入 receipt；receipt 中没有 `layout_manifest` 结构，也没把布局哈希与 PDF sha 绑定。
  - 因此“PDF 布局与字节绑定”在包级 `render_policy` 存在，在服务 Worker 初始 audit 中**只有 PDF sha、没有布局-字节绑定**。
- 当前作品重审（`current_artifact_api`）不传 `vendor_root`，`panel_alignment` 不执行；只按 `render_receipt.checks[fmt].sha256` 校验存储的 PDF/SVG/PNG/TIFF 各自与渲染回执一致。**PDF 与 SVG 的关联仅为“同一次导出/同一 render receipt 的逐格式哈希”，不存在显式的跨格式配对证明。**

**C. Web 不执行不可信脚本**
- 服务端：
  - `figure_router` 的 preview/export/verify/bundle/import 均不执行任何存储源码；脚本项目重绘走 `projects._render_script()` → `SandboxJob` → `worker.py`。
  - `current_artifact_api` 明确不调用 `write_bundle()` / `render_figure()`，不重建 `FigureSession`，不执行源。
  - `verified` 的 `visual` 检查在未传 `visual_inspector` 时不运行，`visual_inspector` 只能由调用方显式提供，服务端点未提供。
- 客户端：
  - 服务器 SVG 通过 `Blob` + `<img>` 显示；`scene_projection.js` 用 `DOMParser` 解析，只 `setAttribute`/`textContent`，拒绝 `javascript:|data:|expression(`；没有把服务器 SVG 字符串写入 `innerHTML`。
  - `figures.js` 中的 `innerHTML` 仅用于自身静态模板、项目列表、属性面板与 `<option>` 生成，不拼接服务器返回的 SVG/HTML。
  - 页面 CSP 限制 `script-src 'self'`、`default-src 'none'`；预览响应另带 `sandbox`。
- 未运行证明：没有启动浏览器、没有实际加载恶意 SVG、没有检查 CSP 在真实响应头中的落地；以上为静态源码结论。

**D. 未知图型 / 可编辑边界**
- 声明式图只支持 6 种 `CHART_TYPES`；未知图型、未知字段、非法枚举均 `SpecError` 拒绝，不做 fallback。热图等不支持图型只能走脚本路径。
- 脚本路径的“可编辑”不是任意图型都可：
  - 必须有 v3 `source` + `frozen_render`，且 `_frozen_render_state()` 通过；
  - `geometry_status` 必须为 `supported`，`geometry_unsupported` 为空，`geometry_sha256` 为 64 位 hex；
  - `data_sha256` 必须与冻结快照逐项一致；
  - `renderer_sha256` 必须等于 `render_script` 的 sha256；
  - 旧记录若几何检查为 `unsupported artist: Text`，会返回 409 `analysis_not_frozen` 并保留原图下载，不自动改写旧回执。
- 旧混写 v2（单 Figure、无 `render_script`）可下载但不可样式编辑；旧脚本项目若只有 `inputs/sources` 而没有 `input_snapshots`，`_can_edit()` 返回 False。
- 编辑只允许 manifest 白名单属性（颜色、字号、线宽、位置、可见性、字体等），不允许数值、误差、统计量；`_check_worker_overrides()` 比对 worker 接受项与请求项，防止替换值或字体伪造。

**E. `draw_svg` 到 `/figures` 工作台的断点**
- `draw_svg()`（`01_src/src/tools/draw_svg.py:72`）只做 SVG 安全过滤，然后 `src.core.figures.register(safe, label)`；registry 是 `ContextVar` 请求级字典（`core/figures.py`），返回 `[[FIGURE:n]]`。
- `src.core.figures.expand()` 只在终答中把占位符替换成 ```` ```svg ... ``` ```` 文本块；`figures.register()` 不调用 `publish_artifact`，不创建 `figure_projects` 行，不产生 `exports`、`manifest`、`render_version` 或 `workspace_url`。
- `/figures` 只列 `figure_projects` 与账号库文件；`current_artifact_api.verify_project()` 也从 `figure_projects` 读 `source_json/manifest_json/exports_json`。因此 **draw_svg 的产物不会出现在工作台，也不能被 verify/bundle 重审**；这是当前实现的明确断点，不是配置问题。
- 若要把 draw_svg 接入工作台，最小改动应只新增“只读 SVG 项目”来源与发布入口，且不得让 Web 执行任意 SVG/脚本；不要在 `draw_svg` 内直接复用 `create_research_figure` 的 spec 路径（会伪造 `scientific_geometry`）。建议由主会话决定是否属于 F2–F5 范围。

**F. 工具可发现 / 可调用 / Worker**
- 可发现：`refresh_allowed()` → `allowed_presets()`；`create_research_figure` 需 research_tools+session+visualization；`run_research_figure_script` 再需 script_sandbox；`draw_svg` 需 visualization；`research_workspace` 需 session+research_tools。
- 可调用：`CapabilityExecutor` 每次派发前重新计算；`host.invoke()` 负责身份、准备、并发与发布；Skill 上下文按请求绑定。
- Worker 入口：
  - `worker.py:main()`（559）解析 `--job`，`run(job_path)`（171）读取 `job.json`；`_run_outputs()`（372）是 v2 槽位路径；legacy 单图路径也由 `run()` 处理。
  - 沙箱后端：Docker 运行 `python -I /opt/citrusfig/worker.py --job /work/in/job.json`；本地后端复制 `worker.py` 并以 `python -I -X utf8 worker.py --job ...` 运行，`CITRUS_FIGURES_ROOT` 指向服务代码；Apptainer 用 `engine_bundle.stage_engine()` 写入引擎与固定 vendor 后再运行同一入口。
  - `engine_bundle.bundle_files()` 只打包 `ENGINE_FILES`、`METHOD_FILES`、安装的 `citrusscholar-support` wheel（校验 RECORD sha）、`provenance.json` 及其清单文件；不递归复制应用目录或运行数据。
- Worker 内部防护：`_guard_imports()` AST 拒绝 `socket/http/urllib/requests/subprocess/multiprocessing/...`；`_resolve()` 限制路径在 job 目录；`_dataset_loader()` 只允许已声明 Path，用 `O_NOFOLLOW`、普通文件、单链接、大小/时间戳复检；容器无网络、无额外进程。

**G. 输出版本哈希与 QA 七项接线**
- 版本：`store._update_project()` 要求 `expected_revision` 命中、`render_version` 不回退；同一 `render_version` 下 source/manifest/overrides/exports/data_refs 任一变化即拒绝，必须递增 revision。
- 哈希：
  - 项目导出：`render_receipt.checks[fmt].sha256/size_bytes/format`；
  - 脚本源：`script_hash = sha256(text)`，`execution_id`，`run_id`；冻结渲染：`renderer_sha256/data_sha256/geometry_sha256`；
  - 重审：`receipt_id = sha256(receipt without receipt_id)`，`project.state_sha256`，`members[].sha256`，`data/parameters.json` sha；
  - 发布：`publish_artifact` 后再次读回并比对 sha 与 `download_url`。
- QA 七项：`packages/citrus_figures/verification.CHECKS` =
  `format_integrity`、`source_preflight`、`panel_alignment`、`pdf_text`、`pdf_collisions`、`svg_schematic`、`visual`；
  `DEFAULT_BLOCKING` = 除 `source_preflight` 外全部；`source_preflight` 只作 advisory（但 service worker 的 `audit_fig` 把它记为 `review_required` 而非 pass）。
- 实际接线：
  - 包级 `render_policy.export_figure()` / `cli.py`：七项全选（默认 `checks=None`），`vendor_root` 由 CLI 传入；`visual` 仅在显式 `visual_inspector` 时运行。
  - 服务 Worker 初始：`src/figures/nature_runtime.audit_fig()` 只执行 `source_preflight`、`panel_alignment`、`pdf_text`、`pdf_collisions`；`src/figures/render_policy.export_figure()` 另对每个格式做签名检查；**没有 `svg_schematic` 与 `visual`**。
  - 服务 Worker 编辑/预览：`src/figures/render_policy.export_figure()` 在 `mode != "initial"` 时不做 Nature 审计，只检查格式签名并回执 `nature_audit: "not_repeated_manual_edit"`；`projects._validate_render_receipt()` 校验 v2 回执逐格式 sha。
  - Web 重审：`vendor_root=None`，七项中只有 `format_integrity` 执行，其余显式未执行。
- 结论：**“QA 七项”是包级契约；服务初始路径是 4 项 Nature + 格式签名；Web 重审仅格式完整性。三者的验收口径不一致，需要主会话确认。**

### 6.3 未运行 / 未验证（原因）

- 未启动或访问 8031/8042、未跑 pytest、未启动 Uvicorn、未调用 `/api/v2/chat`、未打开 `/figures`。
- 未执行任何 Worker / Docker / Apptainer 作业；`worker.py`、`sandbox.py`、`trusted_render.py` 的实际容器/子进程行为未验证。
- 未读写 `state/` 下的 SQLite、账号库、附件、脚本资产或真实图件；未验证 `figure_projects` 迁移、pin/CAS 在真实并发下的行为。
- 未加载 vendor 脚本、未验证 `provenance.json` 的实际文件哈希、未运行 `validate_figure/audit_*`。
- 未在浏览器中验证 SVG 预览、CSP、`DOMParser` 投影、Blob URL、保存/重审 UI。
- 未验证网络、GPU/CPU 切换、模型服务、真实供应商；本报告只覆盖源码接线。

原因：工单要求只读静态审计，明确禁止启动服务、执行网上脚本、读真实 DB/账号/凭据与部署。故以上全部标为“未运行”，不能把源码路径写成“实际通过”。

### 6.4 需要隔离 fixture 实测的证据

- Worker v2 契约：用合成脚本 + 合成 CSV，验证 `result.json` 的 `slots`、逐格式 sha、`validate_receipt()`、legacy 单图与 v2 分支。
- preset/emit 冻结：用 `relationship_panels` + 合成数据，验证 `dataset_ref`、`frozen_render_receipt`、`_accept_frozen_render()`、`_frozen_render_state()`、样式重放后 `data_sha256` 不变。
- PDF 布局绑定：包级 `export_with_layout()` 断言 `layout["artifact_sha256"] == sha256(pdf)`；服务 `audit_fig()` 断言 receipt 只有 `pdf_sha256` 无布局绑定。
- 七项 QA：用固定 vendor 根与显式 checks 运行 `verify_current_figure()`，分别覆盖 `vendor_root=None` 与真实 vendor；确认 `svg_schematic` 是否需要 `math` shim、`visual` 是否永远不执行。
- 可编辑边界：构造 supported / unsupported geometry、旧混写 v2、legacy `inputs/sources`、未知图型、非法 spec，验证 409/400 与 `editable` 返回。
- Web：静态 fixture 检查 `figures.js`/`scene_projection.js` 不将服务器 SVG 写入 `innerHTML`；浏览器 fixture 加载恶意 SVG，确认 `<img>`/CSP/`DOMParser` 不执行脚本；验证 verify/bundle 只调用服务端点。
- draw_svg：验证 `core.figures.register()` 仅请求级保存，`/figures` 列表与 `figure_projects` 不出现该图；确认这是产品缺口还是设计边界。

## 7. 安全与兼容

### 7.1 已具备的静态安全属性

- **路径**：`research_skill_catalog._path()` 拒绝绝对路径、`..`、反斜杠、冒号、NUL、空段、符号链接/junction；`skill_registry._path()` 拒绝绝对/`..`/符号链接；`engine_bundle` 校验每个 vendor 成员 sha、拒绝绝对/`..`/反斜杠/冒号；`worker._resolve()` 限制在 job 目录；`_dataset_loader()` 用 `O_NOFOLLOW`、单链接、大小/时间戳复检。
- **不可信脚本**：Worker AST 禁用网络/子进程模块；容器无网络、无额外进程；`select_backend()` 未配置或镜像 sha 不符时不提供工具；`run_research_figure_script()` 无 runner 时 `not_run`，不保存源码；预览/编辑也走同一沙箱。
- **Web**：SVG 预览为 `<img>` + `Blob`，不是内联文档；响应 `CSP default-src 'none'; sandbox` + `nosniff`；`scene_projection.js` 只改白名单属性并拒绝危险协议；页面 CSP 限制脚本来源。
- **所有权**：`store` 所有查询带 `owner_user_id`；他人项目返回 404 而非 403；`_write_scope()` 使用 `account_lease` + `reserve_on_lease()`；`publish_artifact` 之后再次读回校验摘要与下载链接。
- **重审**：`CurrentArtifactRequest` `extra="forbid"`；`supersedes` 必须匹配前次回执的图件/会话/版本/receipt_id；`expected_revision` 与 `_assert_current()` 防止并发覆盖；不执行源、不初始化视觉模型、不猜路径。
- **发布**：项目 CAS 后才成为当前版本；发布失败不覆盖旧项目；`_assert_current()` 在保存/重审前后重复检查。

### 7.2 兼容与边界

- **旧兼容路径**：`prompts/loader.py` + `graph/scene_skills.py` 仍可被管理员上下文查看使用，但不是 chat 真路径；`tools/legacy_registry.py` 与 `preset_catalog._PRESETS` 有等价测试，但生产发现不依赖旧注册表。
- **旧图件**：legacy mixed v2 可下载不可编辑；旧几何 `Text` 不支持记录原样保留；旧脚本项目缺 `input_snapshots` 时不可重放；旧 manifest 可 `upgrade_manifest` 只读升级，不写回伪造回执。
- **API 默认值**：`visualization_enabled=False` 是服务端默认；Web 前端显式 `true`。非浏览器客户端必须显式开启才能看到图件工具，这是兼容性/契约风险。
- **两套 Skill 目录**：`/api/v2/skills` 返回根发布 4 项；`research_workspace(skills)` 返回旧 registry 全量。两者 ID/版本/哈希空间不同，不能互相冒充。
- **跨所有权**：`current_artifact_api` 明确不写项目、不执行源、不重建 `FigureSession`；`draw_svg` 不写账号库；工作台只改样式不改数据。这些边界应保留，避免把审计/重审逻辑塞进发布或编辑路径。

## 8. 阻塞（最多五）

1. **真路径的方法注入存在断点**（静态证据，需 fixture 确认影响）
   - `general_agent_node` 的系统提示词来自 `host_policy.system_prompt()`，只附加 `skill_adapter.directory_prompt()`；后者列出根发布 4 项与旧 registry 的 `figure-edit/statistics/word-export`，但**排除了 `figure-initial`（SCENARIO_SKILLS）**。
   - `SkillSession.selected_messages()` 已实现但全仓库未发现调用；`selected_skills` 只作为 metadata 附加到 human message，未作为受保护控制消息注入。
   - `figure_scene_guidance()` 中“nature-figure 核心/contract/stance/Python backend 已实际注入”的文本只存在于 `prompts/loader.py` 的旧 assembler，而该 assembler 只被管理员 `context-detail` 端点调用。
   - 影响：初始 Nature 核心与 `figure-initial` 强制读取指引是否真的进入 chat 真路径，源码上**不能确认**；需隔离 fixture 直接构造 `general_agent_node` 的 prompt 或调用 `system_prompt()` + `directory_prompt()` 核对。
2. **“QA 七项”在三处路径的验收口径不一致**
   - 包级 `verification.CHECKS` 是七项；服务 Worker 初始只跑 4 项 Nature + 格式签名；编辑/预览不重跑 Nature；Web 重审 `vendor_root=None` 只跑 `format_integrity`。
   - 影响：FIG 验收若要求“七项全部实际执行”，当前源码无法同时满足三条路径；必须明确“初始生成 / 样式编辑 / 当前作品重审”各自的必跑项与未执行声明。
3. **PDF 布局与字节绑定未贯通到服务 Worker / Web 重审**
   - 包级 `export_with_layout()` 有 `artifact_sha256 == sha256(pdf)` 的布局-字节绑定；服务 `audit_fig()` 只记 `pdf_sha256`，布局来自活 Figure；Web 重审不执行 `panel_alignment`。
   - 影响：不能声称“当前存储 PDF 的布局已被字节级复核”；PDF↔SVG 也没有显式跨格式配对证明，仅有逐格式哈希。
4. **`draw_svg` 与 `/figures` 工作台没有桥接**
   - `draw_svg` 只写请求级 `ContextVar`，返回 `[[FIGURE:n]]`；终答展开为 fenced SVG；不发布账号文件、不建 `figure_projects`、无 exports/manifest/render_version/workspace_url。
   - 影响：聊天内的示意图无法在工作台查看、下载、编辑或重审。需要主会话决定是“设计边界”还是纳入 F2–F5；若纳入，必须新增只读 SVG 项目来源，不能让 Web 执行 SVG 脚本，也不能伪造 spec 的 `scientific_geometry`。
5. **图件工具发现依赖客户端与运行环境，默认 API 客户端不可见**
   - `ChatRequest.visualization_enabled` 默认 `False`；`create_research_figure`、`run_research_figure_script`、`draw_svg` 都带 `VISUALIZATION` 能力，默认不公布；Web 前端固定 `true`。
   - `run_research_figure_script` 还要求 `script_sandbox_available()` 为真（镜像/后端/sha 就绪）；否则工具不可见或返回 `not_run`。
   - 影响：非 Web 客户端/新接入方会认为“没有绘图能力”；需明确 API 契约（是否要求显式 opt-in、是否返回能力原因）。

## 9. 交主会话

### 9.1 结论摘要

- **真路径**：`build_general_agent_graph → general_agent_node → host_policy.system_prompt + skill_adapter.directory_prompt → CapabilityExecutor → host.invoke`；图件工具注册在 `preset_catalog`，Skill 工具在 `skill_tools`，工作区工具在 `research/workspace_tools`。
- **声明式图**：`parse_spec → resolve_spec → prepared resolved_snapshot → trusted_render 子进程 → publish_artifact → store CAS`；未知图型 fail-closed。
- **自由脚本 v2**：`script_runtime → SandboxJob → worker.py`；`outputs_json` 槽位、`render_script` 冻结表、`frozen_render_receipt`；宿主 `_accept_frozen_render` 逐项校验后才可编辑。
- **preset/emit**：`relationship_panels` 是现有冻结样板：`emit.table` + 独立 `render_script`；样式编辑只重放 renderer，不改数据哈希。
- **发布/重审**：`publish_artifact` 先发布后 CAS；`current_artifact_api` 只读重审，`vendor_root=None` 明确不执行上游，`supersedes` 绑定前次 receipt。
- **Web**：SVG 走 `<img>`/Blob + CSP sandbox，客户端只做属性白名单投影；未发现把服务器 SVG 作为 `innerHTML` 注入。
- **关键缺口**：真路径方法注入、七项 QA 口径、PDF 布局字节绑定、draw_svg 工作台桥接、visualization 默认值。

### 9.2 建议的最小 F2–F5 接缝（不跨所有权）

- **F2（Worker 契约，不启动服务）**：新增/复用隔离 fixture，直接调用 `src.figures.worker.main(["--job", ...])` 或 `run(job_path)`；输入合成 `job.json`（无网络、无真实 DB），断言 `result.json` 的 v2 `slots`、逐格式 sha、`validate_receipt`、legacy 单图分支。只碰 `01_src/src/figures/worker.py` 的测试夹具，不改 Worker 合同。
- **F3（preset/emit 冻结，不碰 API/host）**：用 `script_presets.relationship_panels` + 合成 CSV，在隔离目录跑主脚本/渲染脚本，断言 `emit.table` 产出 `dataset_ref`、`frozen_render_receipt` 的 `renderer_sha256/data_sha256/geometry_*`，以及 `_accept_frozen_render` 拒绝篡改、接受原样；再断言 `projects._frozen_render_state` 的可编辑判定。
- **F4（QA/PDF 字节绑定，不执行真实上游）**：包级 fixture 显式传 `vendor_root`，断言 `export_with_layout` 的 `layout["artifact_sha256"] == sha256(pdf)` 且 `panel_alignment` 绑定该 PDF；另用 `vendor_root=None` 断言 `format_integrity` 执行、其余 `upstream_execution_not_authorized`。产出“七项在三条路径的必跑/可未执行”矩阵，供主会话定验收。
- **F5（Web 非执行，不启动服务）**：静态 DOM/JS fixture 检查 `figures.js`、`scene_projection.js` 不把服务器 SVG 写入 `innerHTML`、只使用 `<img>`/`DOMParser`/属性白名单；再用浏览器 fixture 加载恶意 SVG，断言 `<img>` 与 CSP 不执行脚本；不触碰 `src/api` 与发布链路。
- **所有权边界**：F2 只测 Worker；F3 只测脚本/冻结；F4 只测 QA/导出；F5 只测前端。不要在这些接缝里修改 `store.update_project`、`publish_artifact`、`figure_router` 或 `current_artifact_api` 的语义。

### 9.3 FIG-01–20 建议验收路径（HEAD 中无既有 FIG 编号；以下为建议编号）

> 检索 `FIG-0x/FIG-1x/FIG-20` 在仓库中无命中，故这不是既有常量，而是给主会话的最小验收清单。每项均应产出真实命令/回执；未执行即标 `not_run`。

| 编号 | 验收点 | 建议证据 / 接缝 |
|---|---|---|
| FIG-01 | 基线一致：分支/HEAD/工作树 | `git rev-parse`、`git status` |
| FIG-02 | chat 入口与 `visualization_enabled` 契约 | `POST /api/v2/chat` 请求体；API 默认 false 的说明 |
| FIG-03 | 图件工具可发现 | `allowed_presets()` 输出；create/run/draw 的 requires/caps |
| FIG-04 | 声明式 spec 拒绝未知图型/未知字段 | `parse_spec()` 单测 fixture |
| FIG-05 | 声明式渲染子进程与 resolved 快照 | `render_resolved()` + `confirm_render()` fixture |
| FIG-06 | 附件转 account_file 引用 | `_persist_attachment_refs()` fixture |
| FIG-07 | 声明式发布 + 项目 CAS | `publish_artifact` + `store.update_project` fixture |
| FIG-08 | 无沙箱时脚本工具 not_run | `run_research_figure_script()` 无 runner 分支 |
| FIG-09 | 脚本源码保存与 script_ref | `execute_script_request()` / `save_script()` |
| FIG-10 | output v2 契约归一化与槽位 | `normalize_output_contract` / `output_slots` |
| FIG-11 | Worker legacy 单图 | `worker.run()` legacy 分支 |
| FIG-12 | Worker v2 emit 表/图/report | `worker._run_outputs()` + `result.json` |
| FIG-13 | 分离 render_script 冻结回执 | `frozen_render_receipts` / `_accept_frozen_render` |
| FIG-14 | preset `relationship_panels` 冻结路线 | `script_presets` + 合成数据 |
| FIG-15 | 宿主接收冻结回执与数据引用 | `publish_script_outputs()` |
| FIG-16 | 可编辑边界（supported/unsupported/legacy/未知图型） | `_frozen_render_state` / `_can_edit` |
| FIG-17 | 样式编辑不重算数据 | 重放前后 `data_sha256` 不变 |
| FIG-18 | PDF 布局与字节绑定 | `export_with_layout` 的 `artifact_sha256` |
| FIG-19 | 当前作品重审与 supersedes | `verify_project` / `_previous_receipt` |
| FIG-20 | Web 非执行 + 预览/下载同字节 | 前端 fixture + `FileResponse` 同源 sha |

### 9.4 需要主会话决策

1. **真路径方法注入**：是否要求在 `general_agent_node` 的 prompt 中确定性注入 `figure-initial` + Nature 核心，还是接受“由根发布 `nature-figure` + `research_workspace` 按需读取”？若保留前者，需要把 `scene_skills` 的注入从旧 loader 迁到真路径，但不要复制两套来源。
2. **QA 七项口径**：初始生成、样式编辑、Web 重审各自必须跑哪些项？`vendor_root=None` 的 `upstream_execution_not_authorized` 是否算合规的“未执行”终态？
3. **PDF 布局字节绑定**：是否要求服务 Worker 的初始 receipt 也带 `layout_manifest` 并绑定 `pdf_sha256`？若要求，应在 Worker/渲染策略层补，不要只改 `current_artifact_api`。
4. **`draw_svg` 工作台桥接**：是否纳入本轮？若纳入，只新增只读 SVG 项目来源和发布入口；若不纳入，应在产品/验收中明确“聊天示意图不进入工作台”。
5. **API 默认值**：`visualization_enabled` 是否继续默认 false，或改为服务端按能力公布、客户端无需显式开启？这会直接影响非 Web 客户端的图件可用性。

---

**交主会话一句话**：本次只读审计已新增 `docs/audits/figure-runtime-callchain.md`；源码确认了声明式图、v2 冻结、宿主发布与 Web 重审的接线，但真路径方法注入、七项 QA 口径、PDF 布局字节绑定、`draw_svg` 工作台桥接、`visualization_enabled` 默认值五项需主会话决策；所有运行态结论均未执行，未提交/未推送。
