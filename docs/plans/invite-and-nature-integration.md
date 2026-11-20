# CitrusScholar 邀请审核与 Nature Figure 实施合同

2026-11-20。远程 `feat/invite-auth-nature-figure` 基于 `efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`，树 `25b954d1d4020432c55d12623050b8b7dcfcbb51`。冻结前新增内容只有 R1、R2、R3 审计文件。上一版 8042 独立供试用；以下步骤的数据库、工作根、凭据、镜像改建和执行均在隔离开发环境。审计中建议的 AUTH/FIG 编号仅供定位，验收采用用户原任务书第 6 节编号。

## 已确认的接线

R1 核对 `auth.py` 的 register/login/user_for_token 与 `api/main.py` 的注册 Cookie、Origin/Host、管理员及租约，CLI reset 与 Web 行为分叉。登录校验和 Session 签发须消除并发撤销窗口。R2 核对固定上游 `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`/2.8.0 的 44 文件集合，旧应用 40 文件仍用于历史身份。ZIP 的模板和 SVG 脚本仅加 Windows 输出编码处理，PDF 文本和预筛规则也有其它差异。采用已固定仓库版本。外层两处清单缺件影响全量闭包，目录与 bridge 已可正常读取。R3 核对聊天的当前入口是 `general_agent_node → host_policy + skill_adapter → CapabilityExecutor → HostTools`；旧 loader 主要用于兼容入口。`relationship_panels` 提供 v2 冻结样板。Service 初始四项 QA、编辑格式检查、当前文件重审分别存在，后两者缺完整字节和布局绑定。`draw_svg` 仅有聊天占位，缺项目发布。

## A 账号事务

- 同一 `accounts.sqlite3`。`auth_schema_migrations(version INTEGER PRIMARY KEY, applied_at TEXT)`。`users` 增 `approval_status TEXT NOT NULL DEFAULT 'pending' CHECK (...)`、`approved_at TEXT`、`approved_by INTEGER`、`password_changed_at TEXT`，保留旧密码算法、列值、活跃状态、身份和未过期 Session。A1 `migrate(conn)` 自行开启 `BEGIN IMMEDIATE`，以逐条 execute 完成建表、增列、旧行 approved 回填、marker 与提交；异常回滚，重复调用幂等。主会话接入启动和 CLI，在现有基础表、头像和 admission 就绪后调用。旧可信显式创建路径写 approved，普通申请始终 pending。
- `account_invites(invite_id TEXT PK, code_hash TEXT UNIQUE, created_by INTEGER, created_at TEXT, expires_at TEXT, status TEXT, claimed_by INTEGER, claimed_at TEXT, revoked_at TEXT)`。高熵 `secrets.token_urlsafe(24)`、摘要、UTC ISO，默认 7 天，范围 1–30 整日。ID 由服务端产生。有效期到达自动呈现 expired，消费/revoke 均检查时间。`account_applications(application_id TEXT PK, user_id INTEGER UNIQUE, invite_id TEXT UNIQUE, status TEXT, submitted_at TEXT, reviewed_by INTEGER, reviewed_at TEXT, rejection_reason TEXT)`。申请随账户删除；邀请保留已消耗事实，关联用户可置空。审计表 `account_security_events(id INTEGER PK, actor_user_id INTEGER, target_user_id INTEGER, event_type TEXT, occurred_at TEXT, source_hash TEXT, metadata_safe_json TEXT)` 保存最小结构化安全记录；审计与状态写同事务，禁止明文凭据、原始 IP、密码材料和任意请求正文。`account_recovery_codes(code_hash TEXT PK, user_id INTEGER, created_at TEXT, used_at TEXT)` 专用，账户删除级联，替换和密码变更撤销旧码。
- A1 仓储函数接受已有 conn，调用方开启写事务。`issue_invite(conn, *, actor_user_id, expires_in_days=7, source_hash=None)` → `{invite_id,code,status,created_at,expires_at}`；`list_invites(conn, *, limit=50, offset=0)` → 无摘要/明文的行列表；`revoke_invite(conn, invite_id, *, actor_user_id, source_hash=None)` → 公共邀请码行。`submit_application(conn, *, username, salt, password_hash, avatar_preset, invite_code, source_hash=None)` → `{status:'pending',application_id}`，同事务写 role=user/active=0、消费与审计。`list_applications(conn, *, status, limit=50, offset=0)` → `{application_id,user_id,username,avatar_preset,status,submitted_at,reviewed_by,reviewed_at,rejection_reason}` 行列表；`review_application(conn, application_id, *, actor_user_id, decision, rejection_reason=None, source_hash=None)`，decision 为 approved/rejected。相同决定重试返回同回执，对立决定 `approval_conflict`；只在真实 pending 转移时写一次审计。`record_security_event(conn, *, event_type, actor_user_id=None, target_user_id=None, source_hash=None, metadata=None)`。A1 提供带 `code` 的 `AccountInviteError`；仓储错误 `invalid_invite`（空、未知、已用、撤销、过期、异常输入同码）、`username_unavailable`、`invite_not_found`、`invite_conflict`、`application_not_found`、`approval_conflict`。普通应用层不允许传角色、active、审核字段。使用原 admission 删除屏障。
- 主会话在 `auth.py` 提供 API/CLI facade，原 `_connect()`、密码验证和 Session 体系复用。`register(username,password,avatar_preset='01',*,invite_code,source_hash=None)`；`issue_invite/list_invites/revoke_invite/list_applications/review_application` 不向调用方暴露 conn，非 CLI 管理动作同时带 `actor_user_id`、`actor_token` 并在事务重核有效会话、approved/active、管理员和未删除。登录先做密码核验，签发事务以当前凭据和账号状态再次对账；状态变更和会话清理同事务。公开登录失败统一 401。旧账户会话和删除专用重试入口兼容；pending/rejected/停用不能进入业务 API；enable 只接受 approved。CLI 和 Web 停用都撤销全部 Session。
- `change_password(user_id, current_password, new_password, *, token, source_hash=None)`、`replace_recovery_codes(user_id, current_password, *, token, source_hash=None)`、`recover_password(username, recovery_code, new_password, *, source_hash=None)`。恢复码每批 8 个，高熵、一次显示，重认证后替换；任何成功改密/重置/恢复删除全部 Session 和旧恢复码，不签发新 Session。匿名恢复统一无效响应，不暴露账户状态，拒绝停用或未 approved。`reset_password(user_id,new_password, *, actor_user_id=None, actor_token=None, current_password=None, source_hash=None)`；无 actor 的可信 CLI 路径 only、本地交互 getpass。管理端重输本人密码，可帮其他普通账户，管理员账号走本人改密或 CLI/恢复码。保留重置后的原 active/approval，处理安全并发，记录审计。`set_user_status` 兼容原调用并接受 actor/session，区分删除中和未通过审核。CLI 对已授权管理员显式签发邀请码，经 `create_local_account(username,password,*,role,daily_limit)` 保留可信建号能力，废弃 reset 的命令行明文密码。

## HTTP 与 Web 合同

| 方法和路径 | 请求与响应 |
|---|---|
| POST `/api/v2/auth/register` | `{username,password,avatar_preset,invite_code}`，202 `{status:'pending',application_id}`，无 `Set-Cookie`。拒绝额外字段、限制长度，邀请失败泛化 400、用户名不可用 409。无匿名状态查询。 |
| POST `/api/v2/auth/login` | 原字段与成功结构，正确密码且 approved/active 才设 Cookie。失败 401 统一消息。 |
| GET `/api/v2/admin/applications` | `status=pending|approved|rejected,limit=1..100,offset>=0`，`{applications,limit,offset,next_offset}`。 |
| POST `/api/v2/admin/applications/{id}/approve`、`/reject` | 批准空对象，拒绝可选 `{reason}`（≤300 字符，只管理员可读），200 `{application}`；不存在 404，冲突 409。 |
| GET/POST `/api/v2/admin/invites` | GET 分页 `{invites,limit,offset,next_offset}`；POST 可选 `{expires_in_days}`，201 `{invite}`，仅此次包含 `code`，no-store。 |
| POST `/api/v2/admin/invites/{id}/revoke` | 200 `{invite}`，已撤销重试幂等，已被使用或过期 409，不存在 404。 |
| POST `/api/v2/auth/password/change` | `{current_password,new_password}`，200 `{status:'login_required'}`，清 Cookie。不正确的重新认证 403。 |
| POST `/api/v2/auth/password/recovery-codes` | `{current_password}`，200 `{codes}`，no-store。 |
| POST `/api/v2/auth/password/recover` | `{username,recovery_code,new_password}`，无登录要求，200 `{status:'login_required'}`，无新 Cookie。无效凭证统一 401。 |
| POST `/api/v2/admin/users/{id}/reset-password` | `{current_password}` 验证管理员本人，既有随机临时密码一回显示，no-store，保留旧用户状态；禁止重置管理员目标。 |

保留 `/api/v2/admin` 集中及每路由鉴权；旧 Cookie flags/TTL/Host/Origin 规则及账号租约继续有效。匿名注册/登录/恢复接受兼容无 Cookie 客户端，带 Cookie 的写入要求可信同源 Origin；不信任客户端自报 forwarded host。校验失败的响应不可回显密码、邀请码、恢复码或原始请求，敏感模型采用白名单且脱敏 422。复用现有限流，保留注册 8/小时、登录 12/15 分钟，恢复分来源和规范化用户限流，重认证单独限流返回 429。浏览器区分 pending 与登录，清理秘密输入和临时展示；保留头像、聊天/任务和原有下载。无短信、邮箱或人口身份验证。

## F 绘图与 QA 合同

- F1 保持固定 44/40 vendor 原字节与既有历史收据，外层适配出现资源集合变化时分配新可识别版本和 manifest 身份，并保留原身份记录。修闭包而非提升上游版本。方法面与代码入口不暴露 R 或 AI 生图。现有非 editable 支持包、bundle SHA 和隔离运行复用。
- F2 五模板通过 `research_workspace` 既有 preset 发现、`run_research_figure_script` 的 `builtin_script`（id/version/sha）和 v2 产物槽执行，使用显式字段/单位/顺序/阈值、数据与统计回执。必要时薄适配借用固定上游计算，捕获 Figure 以便现有 emit/Worker 统一导出。固定输入权限/哈希，使用 `emit.table` + 独立 renderer 形成样式重放；对应数据不擅自截断或补值。`volcano` 原始全点加阈值计数；`roc` 排序＋梯形 AUC，重复 FPR 保留垂直段且按 TPR 升序；`dotplot` 稳定类别、大小与色标含义；`marginal` 联合及边缘使用同源同单位；`paired` 恰两条件，重复/缺失默认拒绝，显式允许排除时提供清单。非法列/NaN/inf 等明确拒绝或按公开声明保留排除信息。无 Web Python 执行。
- F3 在当前 General Agent 提示接线提供按需访问的科学方法。声明式六种基本图、最多 12 panels 和自由 Matplotlib/Seaborn 均保留，模板是可组合选项。记录科学问题、实验单位、原值、n、缺失/误差/组别/检验来源、面板证据角色、图注和物理排版；无需统计时忠实描述。真实运行验证两基本图、模板混合、三 panel 非对称组合。保留客户端 `visualization_enabled` 与真实 sandbox readiness 准入语义。
- F4 复用 `citrus_figures.verification.verify_current_figure`。初始须有 format_integrity、source_preflight、panel_alignment、pdf_text、pdf_collisions，适用 SVG 执行 svg_schematic。编辑提交和显式重审使用当前渲染字节、可信对应布局和可用检查；source_preflight 仅在本次可信源码可用并适用时执行。布局 `artifact_sha256` 必须是当次唯一导出的 PDF 字节 SHA，禁止替代重导出；按格式分别绑定当前文件。每项记录 `execution_status`（沿用现有执行状态字段）与 `conclusion`、覆盖和 bytes hash，查不到证据记 `not_run`/`undetermined` 或真实 `not_applicable`；无视觉检查器时 `visual=not_run`。`initial_nature_skill_receipt` 原样保留，`current_qa_receipt` 随 revision 更新，显式重审的 supersedes 指真实回执。只有校验过的 vendor/隔离 Worker 或受限可信字节检查器可以执行。No-visual 不阻止发布；格式、安全、哈希/publish CAS 校验失败阻止接收，审美警告保留在可见回执。
- F5 `draw_svg` 复用现有 SVG sanitizer、owner/session 绑定、现有账号文件/项目发布。规范保留明确元素和编辑白名单，文本与样式在合法 SVG 的受支持对象上编辑，不假装有科研几何快照；重审调用 F4 已验证 SVG 字节入口。项目须有 revision、exports、真实下载和工作台链接。架构图、研究流程、证据支持的机制各真跑；外链、脚本、foreignObject 拒绝，浏览器仍用 img/Blob 安全呈现。失败不得返回虚构链接。

## 文件所有权与依赖

| 工单 | 独占文件（下列 src/web/scripts 均在 01_src） | 依赖 |
|---|---|---|
| A1 | `src/auth_migrations.py`, `src/auth_invites.py`, `src/auth_invites_test.py` | R1、合同 |
| A2 | `src/api/main.py`, `src/api/invite_auth_http_test.py` | A1 和主会话 auth facade |
| A3 | `web/index.html`, `web/admin.html`, `web/assets/app.js`, `web/assets/admin.js`, `web/assets/admin.css`, `web/tests/invite-auth.test.cjs`，需要时只修改首页已用 CSS | A2 HTTP 合同，验收依赖真实 A2 |
| A4 | `scripts/manage_account.py`, `scripts/manage_account_test.py` | A1/主会话 auth facade，与 A3 并行 |
| F1 | `02_config/skills.catalog.json`、`skills/nature-figure/{SKILL.md,provenance.json,provenance.extra.json,references/bridge.md}`，资源验证用 `src/agent/nature_skill_resources_test.py` | R2、合同 |
| F2 | `src/research/script_presets.py`, `src/research/presets/nature_*`, `src/research/nature_presets_test.py` | F1、v2/共享 bundle 合同 |
| F3 | `src/agent/skill_adapter.py`, `skills/nature-figure/references/figure-methods.md`, `src/research/nature_composition_test.py` | F1、F2 接口；新增资源由主会话串行登记 |
| F4 | `src/figures/{nature_runtime,render_policy,current_artifact_api,worker,script_outputs}.py`, `packages/citrus_figures/{verification,bundle,nature_runtime}.py`, `src/figures/nature_current_qa_test.py` | F1，完成模板/组合接线后校验 |
| F5 | `src/tools/draw_svg.py`, `src/figures/svg_projects.py`, `src/figures/svg_projects_test.py` | F4 字节审核入口，项目/路由由主会话接线 |
| T1 | `docs/audits/invite-nature-acceptance.md`, 独立跨层测试及缺失基线夹具 | 所有接口与实现合并 |
| 主会话 | `src/auth.py`, `src/auth_session_fence_test.py`, `src/agent/{host_policy,preset_catalog,preset_tool_schemas.json}`, `src/research/{figure_tools,script_execution,script_runtime}.py`, `src/figures/{engine_bundle,projects,store}.py`, `src/api/figure_router.py`, 工作台前端（如接线需要） | 按各组交付依序核对共享接口 |

A1 与 F1 先并行。A1 → auth 集成 → A2 → A3/A4，F1 → F2/F3 → F4 → F5。阶段放行要核 `git diff --check`（既有 CRLF 用 `core.whitespace=cr-at-eol`）、唯一文件归属和真实定向测试。子任务只编辑授权文件并报告命令、结果、接口和剩余问题。主会话按依赖集成与分段提交，按用户授权仅推开发分支。

## 分阶段运行、完成门与回滚

- A1/A2/A3/A4 使用同级 `../test-runtime/run-pytest` 定向选择 auth、HTTP、CLI 和 account admission 回归，Node 运行 `01_src/web/tests/invite-auth.test.cjs`。临时数据库覆核 AUTH-01–25，从旧库快照建匿名夹具，覆盖 DDL 回滚、并发申请/审核、会话撤销竞态和代理 Host/Origin。检查浏览器申请、管理员审批、登录后改密和旧会话拒绝。
- F1 定向校验 manifest SHA、所有资源闭包、旧身份查询及活跃目录。F2/F3 校验五模板的真实科学数值和失败拒绝，跑真实固定镜像、非 editable wheel、离线 v2 Worker；F4/F5 核对初始/编辑/重审 QA 逐项、真实字节、hash、负例和项目鉴权。浏览器看图、改样式、重开和独立下载；记录 FIG-01–20。可脚本化的 provider 仅证实真实工具链，真实模型项按外部 API 禁止实际记 not_run。
- T1 在 Linux 临时 App/Worker 环境跑所有可用 pytest、`node --test 01_src/web/tests/*.test.cjs`、`python 01_src/scripts/update_tool_manifest.py --check`，处理或明确登记基线缺夹具和不能运行项。只报告真实命令、环境和通过数，Git 仅收产品源码、必要资源、测试和脱敏文档。验收报告逐工单与 AUTH/FIG 编号出结果，回答任务书十问。
- 后续部署计划以候选 commit 和镜像 hash 为单位，先停新写入并确认在途完成，用 SQLite backup API 留一致性备份，在临时镜像中演练 migration/rollback，验权限、旧会话、项目列表/下载、GPU 默认和 Worker readiness。认知回滚边界：启用新注册后绝不让旧代码重新暴露自助注册或处理待审账号，先关闭所有旧认证写入口，保留新增表和有效审核记录，部署经验证的前向修复或以备份恢复到隔离根并核对切换差异。禁止直接用生产 DB 验证；目前只测试和推送候选代码。
