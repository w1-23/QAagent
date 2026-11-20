# CitrusScholar 邀请审核与 Nature Figure：候选验收

日期：2026-11-20。仅在远程 Linux 隔离候选上实施。基线 `efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`（tree `25b954d1d4020432c55d12623050b8b7dcfcbb51`）；候选分支 `feat/invite-auth-nature-figure`。这里的“放行”指工单之间的工程验收，不是开放公网或切换生产。只有运行过的检查才能标为通过；完整生产放行当前为 **no**。

## 工单

路径中 `src/`、`web/`、`scripts/` 均相对于 `01_src/`。实际改动的完整清单由 Git tree 和源码清单提供。

| 工单 | 关键文件 | 实际改动 | 定向测试或证据 | 结果 | 剩余风险 | 放行 |
|---|---|---|---|---|---|---|
| R1 | [auth-current.md](auth-current.md) | 只读核对迁移、cookie、角色、会话撤销、CLI 与审计 | Git/文件和函数调用链只读核对 | pass（调查） | 无线上 DB 证据，按要求不取 | yes |
| R2 | [nature-inventory.md](nature-inventory.md) | 只读核对固定上游 2.8.0、44/40 文件清单和范围 | 逐文件清单、固定提交及摘要 | pass（调查） | R 和外部生成未启用 | yes |
| R3 | [figure-runtime-callchain.md](figure-runtime-callchain.md) | 只读追踪 Browser/API/Host/Worker/保存/重审 | 正向调用链和直接执行边界审阅 | pass（调查） | 静态证据不等于整链运行 | yes |
| A1 | `auth.py`, `auth_invites.py`, `auth_migrations.py` | 事务迁移与审批；单次邀请码；会话签发 fence；独立恢复码 | 真实 SQLite 两连接竞争、老库重复运行和注入回滚，见 AUTH-01–08、24–25 | pass | 生产演练未做 | yes |
| A2 | `src/api/main.py`, `invite_auth_http_test.py` | 原 Cookie/账号准入叠加审批，保护敏感端点 | 真实路由 202/401/403/409/429、来源约束、会话和越权拒绝 | pass | 生产 TLS/反代未实测 | yes |
| A3 | `web/index.html`, `admin.html`, `app.js`, `admin.js`, CSS | 申请/审批/邀请码、重新验证、一次值呈现与退出清理 | Chromium + 15 项专属 Node 断言；附件竞争在真实页面独立复验 | pass（定向） | 扩展旧夹具差分见下 | yes |
| A4 | `scripts/manage_account.py`, `manage_account_test.py` | 列表/建号/重置/启停/签发，关闭命令行明文密码与回显退化 | CLI 测试：停用状态保留、旧 Token 失效、只显示一次、拒绝无交互回显 | pass | CLI 必须作为受控宿主操作者使用 | yes |
| F1 | `02_config/skills.catalog.json`, `02_config/skill-history/`, `skills/nature-figure/`, `skill_adapter.py` | 扩充方法资源；外层 2.1.0 新身份，保留 2.0.0 精确旧身份；不覆盖上游 | 精确 SHA、分页重组、读回旧身份、资源不扩大能力 | pass | 保留历史 vendor 不等于启用可选外部功能 | yes |
| F2 | `src/research/script_presets.py`, `presets/nature_*` | 五个 version-2 冻结分析模板，默认单 SVG，不补值 | 五 CSV 模板实际运行；统计有效性与错误输入由独立 fixture 核验 | pass（既有 CSV Worker 样本） | 最终 wheel 的文件引用路径不能用旧运行替代 | yes（实现） |
| F3 | `host_policy.py`, `figure-methods.md`, `nature_composition_test.py` | General Agent 指导、六种基础图、多面板和自由 Matplotlib；不建平行 Agent | 六图与两/三面板、混合布局，检查真实文件、标签、解冻边界 | partial | 外部真实模型自主选用方法及修图未运行 | no（自主性） |
| F4 | `src/figures/{worker,trusted_render,projects,current_artifact_api,nature_receipts}.py`, `packages/citrus_figures/` | 同次 PDF 布局和产物身份、编辑与再审、旧回执兼容；相同重复 draw 几何可绑定 | 非 editable wheel、140 个 stage 文件；真实小字/错位/碰撞、分类柱图、byte/SHA 和失配拒绝 | pass（定向） | 自动审查不认证投稿；无视觉检查器 | yes |
| F5 | `draw_svg.py`, `svg_projects.py`, `figure_router.py`, `web/assets/figures.js` | 安全 SVG 真实项目/CAS，受控文字样式编辑、预览、独立下载/再审 | Chromium 29 项，三种示意、两次保存、重开、逐字节下载、409/404；真实 StructuredTool | pass（定向） | 脚本化模型工具运行不等于生产 chat 全链 | yes |
| T1 | 本报告；AUTH/FIG 定向测试及隔离证据 | 隔离旧库、真实 Worker/浏览器，基线对照、缺失夹具显式记录 | 下列命令与证据 | partial | 全量套件仍非全绿；真实模型未运行 | no（生产） |

## AUTH-01–25

编号沿任务书，不沿历史测试函数名前缀。

| 编号 | 结果 | 证据 |
|---|---|---|
| AUTH-01 | pass | 合成旧活跃账户真实 PBKDF2 密码，迁移后登录且用户 ID 不变。 |
| AUTH-02 | pass | 合成旧停用账户迁移后仍停用，同密码登录拒绝。 |
| AUTH-03 | pass | 合成旧管理员角色保留，原有效 cookie token 反查成功。 |
| AUTH-04 | pass | 缺邀请码不能提交有效申请。 |
| AUTH-05 | pass | 未知、过期、撤销、已用码统一无效；不返回码内容。 |
| AUTH-06 | pass | SQLite 两连接、同一邀请同时申请，只有一个提交成功，同一 claimed/pending 身份。 |
| AUTH-07 | pass | 重复用户名与非法输入不消耗邀请码；合法重试仍能使用。 |
| AUTH-08 | pass | 真实注册路由及浏览器 202/pending；无 `Set-Cookie`，不进入应用。 |
| AUTH-09 | pass | pending＋正确密码返回 401。 |
| AUTH-10 | pass | 用真实 `main.app` 与合成旧 token，pending/rejected 访问会话历史、附件、工作区、job、后台均拒绝。 |
| AUTH-11 | pass | 普通成员批准申请得到 403。 |
| AUTH-12 | pass | 管理员批准后申请人正常登录；浏览器实际 cookie。 |
| AUTH-13 | pass | 重复批准幂等；同一账户、同一有效决策和审核事件。 |
| AUTH-14 | pass | 拒绝后不发会话，邀请码保持消耗。 |
| AUTH-15 | pass | 额外 `role=admin` 在注册入口拒绝。 |
| AUTH-16 | pass | 停用已通过用户后 token 无效，SQLite browser_sessions 对应条目归零。 |
| AUTH-17 | pass | pending/rejected 的 enable 为 409 `account_not_approved`。 |
| AUTH-18 | pass | 管理员 Web 重置须本人密码且撤销旧会话，随机临时密码仅一次显示、no-store。 |
| AUTH-19 | pass | 交互式 CLI 重置撤销会话，保留 active/approval；不支持明文 argv 或终端回显退化。 |
| AUTH-20 | pass | 改密后旧密码、另一浏览器的旧会话均失效；并发登录签发 fence 由定向回归覆盖。 |
| AUTH-21 | pass | 八个独立恢复码；一次性，拒绝跨账号和把邀请码当恢复码；成功恢复无 Cookie。 |
| AUTH-22 | pass（隔离） | 同源与畸形 Origin、Host 与伪 forwarded 不一致、匿名公共范围、默认 Cookie 属性、非法用户名仍按数据处理、用户名重复与被拒值不回显。参数化 SQL 经源码核对；非完整生产渗透测试。 |
| AUTH-23 | pass（隔离） | 注册 8/小时、登录 12/15 分钟命中 429；已存在/不存在用户名在错误密码下返回同样 401/429 JSON、均无 Cookie；重认证过密可拒绝。 |
| AUTH-24 | pass | 真实竞争事务：相反两分支只提交一个决定、一条有效事件，另一分支冲突。 |
| AUTH-25 | pass | 迁移两次无损；故障注入导致 DDL/回填/marker 完整回滚；显式旧审核值不覆盖。 |

## FIG-01–20

| 编号 | 结果 | 证据 |
|---|---|---|
| FIG-01 | pass | 六种基础图实际产品工具输出：bar/line/scatter/errorbar/boxplot/histogram，每张一份非空 SVG；授权导出与保存/审计字节身份一致。 |
| FIG-02 | pass | 两种基础图同一画布，独立 ID 和两处几何面板；组合测试及真实工作台展示。 |
| FIG-03 | pass（组合夹具） | 三面板非对称 GridSpec，使用合成输入、面板标签、图例/来源、PDF 布局绑定。不是实时模型自行选图证据。 |
| FIG-04 | pass（CSV Worker） | Volcano、ROC、dotplot、marginal、paired 都实际执行；按测试分别核阈值与分类、ROC AUC、顺序/编码、统一边界和配对差值/排除表。 |
| FIG-05 | pass | 缺列、非法值和不合法范围走失败，不登记假图。 |
| FIG-06 | pass | 配对缺失/重复默认拒绝；获批准的排除显式列出。 |
| FIG-07 | pass | ROC 对照独立手算 AUC，保留 tie threshold。 |
| FIG-08 | pass | 对照冻结表、原输入及数值；使用完整有效精度，缺失不静默填补。 |
| FIG-09 | partial | 自由 Matplotlib/Seaborn 和非对称组合实际执行；未对外请求真实模型，无证据声称生产 General Agent 自主规划、写代码与改错已通过。 |
| FIG-10 | pass（边界） | 自由脚本以隔离 Worker 执行；Web 只解析声明式合同/接收回执，不调用自由脚本执行。无生产隔离开关变更。 |
| FIG-11 | pass | 初始 PDF 源预筛、对齐、文字、碰撞均有独立回执；单面板对齐为 not_applicable。 |
| FIG-12 | pass | 真实 PDF 小字、错位、碰撞分别触发预期结论，另有不能审计的显式回执。散点夹具越界被实际 Worker 拦截。 |
| FIG-13 | pass | 检查用于 QA 的 PDF/其布局身份/实际交付 PDF 哈希一致；测试变动合同和产物拒绝。仅 SVG 时审计 PDF 不对外发布。 |
| FIG-14 | pass（冻结样本） | 实际样式编辑后核冻结数据身份保持，保留 initial QA 并绑定新导出；saved-byte re-audit 只认证实际保存字节。无冻结数据的旧脚本图只读可下载。 |
| FIG-15 | pass | 安全 SVG 可建真实项目；三种浏览器用例编辑、两次保存、重开、下载和校验。 |
| FIG-16 | pass | 拒绝危险脚本/外链/foreignObject 等结构；跨账号隐藏项目，过期 revision 409；保留合法同文件引用。 |
| FIG-17 | pass（诚实报告） | `visual.executed=not_run`、结论 undetermined；未省略、未标 pass。 |
| FIG-18 | pass（本轮路径） | 无额外图像 API、OpenRouter 凭据或计费；本次离线 Worker 不联网，上游可选文档不表示功能接入。 |
| FIG-19 | pass | 旧图件/旧格式和 `nature-figure` 2.0.0 精确身份回读，未改旧回执。 |
| FIG-20 | pass（代码与夹具边界） | `render_figure(source=...)` 不在 Web 路径接收不可信模型代码；生产 Worker 路径才运行脚本，声明式有界子进程只接受已解析合同。 |

## 实际运行与证据边界

- 环境：Linux Apptainer；应用 SIF SHA-256 `e7ae08db8557e755d50e44e4e5821345cdb41cdb8a245fabfbc44db6776448aa`，绘图 SIF `251e26855bb8d65c4c600f777605a694c5505795fff43631e126e829003996fa`。无额外在线安装。`citrusscholar_support-2.0.0` 非 editable wheel SHA-256 `1d9397f49dbd5264e0d2026f202884c5d382a85f6a738b7cd51128847842539a`。离线安装后以只读 site-packages 及 stage 清单运行。
- 认证/CLI 定向 pytest：`auth_invites_test.py`, `auth_session_fence_test.py`, `invite_auth_http_test.py`, `manage_account_test.py`，89 pass，exit 0；49 个 Starlette TestClient 按请求附 cookie 的弃用提示。`node --test 01_src/web/tests/invite-auth.test.cjs` 15 pass，exit 0。`update_tool_manifest.py --check` 校验 26 个 schema。
- 绘图定向 pytest：在已安装 wheel 上执行 `nature_current_qa_test.py`, `svg_projects_test.py`, `nature_composition_test.py`, `nature_presets_test.py`，53 pass、13 subtests，exit 0。分类柱图在最终 wheel 直接通过。较早六个只交付 SVG 的产品样本均通过文件和审计身份核对；未提供图注时质量契约正确返回 partial；仅补充合成图注重跑后六图均 complete，未改变 SVG 字节或降低 QA。
- 实际 staged Worker：140 个文件逐件校验，真实 SIF、只读输入和无网络。初次样本因横轴文字越过页面拒绝；只调整 fixture 页边界，不改数据和 QA 门，再运行 exit 0。PDF 9,952 bytes / `e8c869cbd7008426234c228b45c86ec172b6fa09ce0940bfa0519adcd85db0e2`，SVG 17,736 bytes / `b69e8c7b8eef9bb82f5951acc23499cf493ca6f3f992e041d0517a9b85946f8d`，当前回执 `8df58e6d65c2bc101aa378a1eb173fd3113acce05ba558f4ddfeda3ea39997fe`。两格式 integrity pass；源预筛 fail（建议项，并由统一 exporter 管理字体/格式）；alignment not_applicable；PDF 字号/碰撞 pass；SVG schematic warn；visual not_run。两份产物 hash 与 receipt，PDF hash 与初始 Nature 四项回执及布局快照，分别核对。
- 真实 Chromium `159.0.8154.6`：真实邀请管理、注册/审批、登录、改密、恢复、一次性秘密清除验收；另用两合成账号延迟附件编码，实际退出换文档，旧 chat POST=0，新 chat POST=1 且不含旧附件/引用，JS 异常=0。第二条测试在浏览器拦截新 chat 响应，**不**发模型请求。示意图 29 项检查，跨账号 404、匿名 401、复核与下载实际 SHA；以脚本化 BaseChatModel 调用了真实 `draw_svg` 工具，并非完整生产 Host/chat 轨迹。
- Python 扩展差分（最终 wheel、临时恢复的三个历史夹具）：候选 217 pass、13 subtests、2 fail，exit 1，无 deselected；基线 45 pass、2 fail。共同失败为 `test_running_duplicate_returns_pointer_without_cancelling_or_faking_done` 和 `test_quota_rejection_is_terminal_and_replay_does_not_reserve_again`。历史夹具和占位文件未纳入发布。
- Node 扩展差分（同一临时恢复 helper 集合）：基线 60 pass/15 fail；候选 74 pass/16 fail。共同失败为历史读取、聊天重放和图件选择/保存/删除中的旧夹具。候选独有失败是模拟器向**已经退役**的文档写入第二个账号并要求发请求；产品真实浏览器必须换文档，已实测实际行为正确。未修改旧断言刷绿，保留失败事实。

- 最终 wheel 的原生 Apptainer 产品工具和 Worker 验收通过 110 项检查，exit 0。覆盖自由组合、真实 PDF 布局绑定、通过 `dataset_ref` 执行无标签火山图、冻结表样式编辑、过期修订及篡改产物拒绝。独立的 `label_top=4` 样本因两项文字与笔画相交被 `pdf_collisions` 拒绝发布，当前审查快照保留该失败。带自定义或混合坐标变换的脚本仍可渲染，相关对象可能无法支持冻结样式编辑。

## 部署预检与回滚

应用 SIF 内置旧版支持包。候选 Web 和 Worker 部署须安装经校验的最终非 editable wheel，或以只读方式挂载其完整安装内容及发行元数据。正式生产切换未执行。

1. 从审查通过的 commit 建隔离版本目录，逐项核对源码清单和离线建包、SIF SHA。使用单独 runtime/SQLite/账号、独立 Worker 和临时 loopback 入口，不复用线上 state 或密钥。保留默认 GPU、管理员 CPU 切换；普通绘图沙箱保持 CPU、离线。HTTPS 入口显式启用 Secure cookie，核对反代传递的真实 Host/Origin。禁止将测试身份或浏览器脚本当真实用户。
2. 用合成旧 SQLite 做两次升级和备份恢复演练。正式切换须单独授权：核对在途任务和唯一写者、由授权人员做一致 SQLite 快照及受限备份，先验证读写和迁移再开放注册；确认有效旧会话、后台审核、Worker readiness、文件权限和实际下载字节。第一枚管理员邀请码不得记录明文；一次秘密仅通过受控方式传递。
3. 回滚前先停止新流量和新写者，保存当前数据以便对账，按授权恢复经过验证的同代二进制与一致备份。**不要**让旧程序直接接管含 pending/rejected 的新库：旧程序不理解新审核契约。不能直接回灌覆盖有新用户操作的原库，也不能把切换后消费/审核/密码变更前的凭据无条件恢复；根据差异处置，必要时保持维护模式。不得删除旧用户文件、旧图件或线上单元。

## 任务书十问

1. **旧数据有没有变？** 合成旧库中 ID、密码哈希、active、角色、有效 Cookie 与旧数据引用保留；迁移只回填历史审核元数据，不改既有内容。未触碰或验证实际生产数据。
2. **新用户能否在批准前使用？** 有效提交把邀请标为已用，账户 pending/inactive，无 Cookie。真实路由测试中 pending/rejected 连含合成旧 token 都不能读业务端点。
3. **管理和恢复是否鉴权、撤销、审计？** 审批/启停需有效管理员会话，Web 密码重置再验管理员本人，恢复码是个人一次性凭据，CLI 依赖本机管理授权。停用/改密/重置/恢复撤销会话；批准不凭空发会话；敏感变更事务记最小审计。
4. **五模板真的执行了吗？** 五个都通过既有 Worker 的 CSV 工程样本并输出实际图件；模板统计另有独立夹具。不得用该样本声称所有平台、各种业务数据或模型调用方式都已验。
5. **是否只会固定模板？** 六图可组合；实际有混合分析结果和非对称 Matplotlib 面板。真实模型通过产品聊天自主写脚本这项仍未运行。
6. **方法是否真正接入？** 主 Host prompt 引导按需读取正式登记的 `figure-methods.md`，包括问题、实验单位、n、误差定义、图注、面板职责；输出与 QA 合同真实约束。真实模型采纳程度未测，不以注册文件推断。
7. **QA 是否绑定真实文件？** 初始和编辑均记录格式/适用的 PDF 检查/一致布局与 SHA，可信源可用时有源预筛；SVG 有适用检查。再审只认证保存字节。只交付 SVG 而无保存 PDF 的后续重审不能重用旧 PDF 的检查；原收据依旧保留。视觉缺失明确 not_run。
8. **是否引入手机号/短信/OpenRouter？** 未引入，也未部署 R 或外部生图。
9. **剩余和部署硬门？** 完整生产 Host/真人模型自主性、真实生产反代/DB 演练未验证；扩展套件非全绿；视觉没有能力。部署须单独授权并完成备份、隔离候选健康和凭据/代理验证。合成测试不替代正式评测。
10. **碰了线上或 main 吗？** 本次候选没有读写线上 DB、没有部署或重启 8031/8042、没有改持久 unit。推送授权只限开发分支，不变更 `main`。
