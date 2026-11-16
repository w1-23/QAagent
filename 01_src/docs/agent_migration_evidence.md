# General Agent 迁移证据与接口边界

日期：2026-11-15。本文记录基线、已执行检查和未通过的迁移门槛，不把静态源码、离线探针或版本号当作上线验收。目标契约见 [General Agent 架构](general_agent_architecture.md) 和 [上下文生命周期](context_lifecycle.md)。

## 1. 改造前基线

| 项目 | 证据 |
|---|---|
| 仓库 | https://github.com/w1-23/QAagent |
| 当前分支 | `feat/research-workspace` |
| 基线提交 | `a888779e17eea04ec57525fa2f7c02d53841726b` |
| 本地留存引用 | `refs/heads/baseline/research-workspace-20261115`，已核对与提交一致 |
| 提交内容 | 135个文件，11370行新增、2507行删除；已有产品源码、产品文档、必要测试及此前获准删除的三个旧控制模块 |
| 忽略测试 | 逐项纳入24个核心/会话/恢复等回归文件；没有全局解除test_*.py忽略，也没有删除其他本地测试 |
| 其余工程材料 | `engineering/product_evolution`仍有工作树状态标记，但对HEAD的内容diff为空，未另行纳入或改写 |
| 检查 | 暂存差异check通过；暂存路径无秘密/运行数据库文件，常见凭据串模式检查未发现候选；这些检查不宣称完整安全审计 |
| 推送/部署 | 未推送；未重启、停止或部署服务 |
| 回归记录 | 此前组合回归557 passed、1条已有Matplotlib警告；基线提交时未重跑该套件，不把它记为本轮新测试结果 |

本次基线明确允许在完整改造验收之前创建。它保留改造前状态，不包括账号数据库、用户文件、模型或运行数据。恢复使用新分支/隔离检出，不对用户工作树hard reset；数据恢复另行按备份和schema兼容性处理，先对账未决操作。

文件清单可用 `git show --name-status a888779e17eea04ec57525fa2f7c02d53841726b` 获取。当前产品依赖仍来自原有 `requirements-base.txt`、CPU/GPU入口和worker依赖文件；它们不是完整可复现环境锁。本文之后的文档更新不属于该基线提交。

## 2. 运行身份与依赖证据

当前可见8010 preview的来源曾指向原始检出目录，不能证明运行本工作树。服务commit、worker image digest、前端构建hash、实际管理员覆盖值及供应商有效窗口均未在此轮核实，状态为unknown。保留已有CPU/GPU切换和默认GPU要求；P2未启动服务，后续P3仅启动独立8029静态验证页（见第6节），没有切换完整应用。

按用户要求新建独立 **Conda 环境 `CitrusSchalor`**，本机路径 `E:/anaconda/envs/CitrusSchalor`。原 Anaconda base 与正在运行的服务未切换。直接通过包元数据核实：

| 包/解释器 | 原检查环境 | 新 CitrusSchalor 环境 |
|---|---|---|
| Python | 3.13.9 | 3.13.18 |
| langchain | 1.3.10 | 1.5.0 |
| langchain-core | 1.4.8 | 1.8.0 |
| langchain-openai | 1.3.2 | 1.8.0 |
| langgraph | 1.2.6 | 1.2.22 |
| langgraph-checkpoint | 4.1.1 | 4.3.2 |
| langgraph-checkpoint-sqlite | 3.1.0 | 3.1.0 |
| openai | 2.43.0 | 3.51.0 |
| pytest | 8.4.2 | 8.4.2 |
| deepagents | 未安装 | 0.7.38 |
| pydantic | 2.12.4 | 2.14.3 |

`pip check` 通过：`No broken requirements found.` 环境定义见 [environment-harness.yml](../environment-harness.yml)，已解析验证依赖的固定版本见 [requirements-harness.lock.txt](../requirements-harness.lock.txt)。该版本清单没有发行文件 hash，已验证平台为 Windows x86_64/Python 3.13.18；尚未验证 Linux 重建，不能宣称跨平台位级复现。

Conda 使用清华镜像；环境内 `pip.ini` 设置清华 pip index；Conda 环境变量只设置镜像域名的 `NO_PROXY` 与 `PYTHONUTF8=1`，未修改系统代理。激活方式为 `conda activate CitrusSchalor`。这套环境目前安装 Harness/协议测试依赖，**尚未安装完整 GPU、科研绘图与 RAG 服务栈**，不是生产部署环境；CPU/GPU依赖仍按原入口分开。

## 3. 成熟组件兼容门槛

源码调查固定到：

- [Deep Agents a93aed4](https://github.com/langchain-ai/deepagents/tree/a93aed4bca3a5e4d45bff393e16092222767e6c3)：MIT；读取工厂、backend protocol、Skill middleware/工具披露及摘要相关片段。
- [LangGraph 08a2016](https://github.com/langchain-ai/langgraph/tree/08a2016953789a4f579b4d36bd1967941ec62012)：MIT；读取checkpoint协议、pending writes、版本/祖先恢复边界。
- [Pydantic AI f93bcff](https://github.com/pydantic/pydantic-ai/tree/f93bcff0c72caa0d5b0e2ead8b550d9674c8c9c4)：MIT；比较capability、history processor和durable operation；未采用为第二生产内核。
- [OpenAI Agents 64fbde5](https://github.com/openai/openai-agents-python/tree/64fbde5d02b42ae5f9db668eb0184cdb3fc9d775)：MIT；读取runner/session相关片段；Responses专用行为不直接套用当前DeepSeek。

Deep Agents调查commit的 `libs/deepagents/pyproject.toml` 声明版本 **0.7.38**，Python>=3.11，依赖包括 `langchain>=1.5,<2`、`langchain-core>=1.8,<2`、`langchain-anthropic>=1.7.10,<2`、`langchain-google-genai>=4.4.3,<5`、`langsmith>=0.14.17`、`wcmatch>=11`。原环境langchain/core不足要求；现在已从镜像安装发布包0.7.38及兼容依赖，但未接入生产进程。

**依赖访问问题已定位并解除。** 早先官方索引与镜像请求均受本机失效代理 `http://127.0.0.1:7897` 影响，出现连接拒绝；此前的 `No matching distribution found` 不能用来判断包不存在。对镜像域名设置进程级 `NO_PROXY` 后取得版本列表并安装成功。随后按用户要求改用新Conda环境，原临时venv安装已停止，不作为交付环境。未修改全局代理、安全设置或旧服务依赖。

### 已执行的有限离线探针

在现有安装版本上使用公开 `create_agent`、`AgentMiddleware.awrap_tool_call` 与 `AsyncSqliteSaver.from_conn_string`，配合合成模型和只读内存夹具工具：

1. 模型发出原生call ID `probe-call-1`；工具经middleware调用并返回观察。
2. 在tools节点后中断，SQLite保存状态。
3. 关闭saver，重新建立saver连接和graph，以同thread和 `ainvoke(None, config)` 接续。
4. 验证最终回答引用已保存观察，工具累计调用一次，call ID保持，HumanMessage仍只有原始一条，最终无待执行节点。

**结果：通过。** 使用临时数据库，没有加载产品账号数据，没有真实provider请求，没有停止任何服务。

该早期探针只代表旧依赖环境。新环境和真实provider的后续结果如下；均不能单独证明生产HostTools适配或完整恢复已完成。

### 新环境回归与真实供应商验证

通过应用既有加载函数只读使用后台所选供应商：`opencode_go / deepseek-v4.1-flash`。凭据仅在进程内使用，未输出、复制或另存；未读取dotenv。关闭外部 tracing，测试使用合成小夹具与临时SQLite，不发送用户文档。探针独立HTTP客户端使用 `trust_env=False` 避开失效本机代理，没有改变生产连接配置。

| 验证 | 实际结果与边界 |
|---|---|
| 新环境基础回归 | `harness_compatibility_test.py`最初2项 + `provider_protocol_test.py`64项，共66 passed（3.26s）。包括原生call ID、公开拦截器和SQLite重建接续；协议测试使用MockTransport |
| 补充Skill回归 | Harness文件扩充为5项后，单独5 passed（0.62s）；新增全文/单行读取披露及无权限拦截动态工具。累计覆盖69个不同测试，未把两次结果冒称一次69项组合运行 |
| 真实原生工具与流式回答 | 最终探针2次物理请求，1次夹具工具执行；原生call/result ID保持，strict标记真实进入请求，结束原因依次为tool_calls/stop；流式结果使用了工具返回后才生成的随机marker与数值173 |
| 该成功探针usage | 供应商返回两次total_tokens分别452、497，合计949；仅此成功探针，不含前两次诊断运行，不宣称总成本 |
| 自然语言Skill发现 | 未显式指定工具/Skill入口；模型通过短目录自主read_file读取方法全文，首次请求只有ls/read_file |
| 读取后动态披露 | 安装版公开SkillsMiddleware经普通schema绑定向当前供应商添加read_calibration；实际顺序read_file→read_calibration→答案，3次物理请求，专用工具执行1次 |
| Skill与SQLite接续 | 方法读取后关闭saver、重建graph，再以ainvoke(None)接续；工具不重复，所有call/result配对，HumanMessage始终1条。是图/连接重建，**不是杀进程恢复演练** |
| 公共组合方式 | create_agent + StateBackend + FilesystemMiddleware + SkillsMiddleware + AsyncSqliteSaver；仅虚拟文件读工具，没有宿主shell、子代理或摘要middleware |

诊断中有两项**探针写法问题**，已修正且未修改生产代码：

- `bind_tools([已包装function schema], strict=True)`会被langchain-core原样保留，strict并未入请求。首次运行只记录了断言失败；增加安全诊断后的重跑确认原生调用和流式观察正确，但strict断言失败。改为对function定义调用`convert_to_openai_function(..., strict=True)`后通过；这是本产品现有交互工具已使用的转换方式。
- 安装版Skill的`metadata.include_tools`需要空格分隔字符串；探针最初用YAML列表，方法虽被读取却没有披露工具。改成受支持格式后通过。生产注册适配应校验元数据，避免静默遗漏。

上述真实诊断和成功运行共发出12次物理请求；无自动SDK重试。成功native探针3.57s、成功Skill探针9.47s仅为单次观测，不是基准测试。

**已实测的上游缺口：**读取SKILL.md一行和读取全文均会披露工具，因此上游read记录不能作为完整加载凭证。公开`awrap_tool_call`可在工具已经披露后阻止未授权执行，测试确认夹具执行次数为0。产品仍需将必需方法/资源完整交付、版本/hash和授权共同作为派发条件；当前新增测试记录依赖行为，并未上线完整性适配器。

### P2补充验证：公开扩展点与真实组合链路

本节为随后按用户要求补齐的P2验证。新增的是隔离契约测试及测试专用适配器，**未切换生产执行器**。没有实现被拒绝的通用命令worker、默认子代理或第二套Agent循环。

| 门槛 | 实际验证 | 结论与生产边界 |
|---|---|---|
| 完整方法加载 | 读取1行、正文读完但漏必需资源时，模型schema不含专用工具，强行调用也返回not_run；通用read_file保持可用 | 公开model/tool hooks可以同时控制披露和派发。测试解析固定版本的read展示格式；生产复用skill_registry的结构化范围/hash回执，不能照搬展示文本解析器 |
| 恢复后的方法与权限 | SQLite重建后重新校验必需资源；版本变化、权限撤销、owner不匹配均不执行；活动发送投影丢失方法时在模型调用前停止 | 历史loaded标记不能独立授权，方法在状态中存在不代表仍在实际发送视图中 |
| 用户问题暂停/接续 | interrupt→关闭saver→重建→Command(resume)；原生call ID不变，真实HumanMessage仍1条 | 模拟输入只回答合成夹具问题，不代表真实用户批准任何业务动作 |
| 同批交互屏障 | 模型同批提出问题与写操作；公开tool hook将兄弟写操作标记not_run；回答问题后也不偷偷重放该写操作 | 产品接线仍须保留现有request_user_input/操作账本的状态语义 |
| 取消 | 分别在公开模型hook和异步工具等待中取消，取消到达实际await，后续动作不执行 | 不把协程取消等同于外部进程或线程已经退出 |
| 持久预算和429 | 通过公开model hook调用现有task_events.reserve_attempt/finish_attempt；MockTransport返回429后关闭图并重建；未知usage保留100个预留token，成功重试实际10个token，两次调用共110个token；第三次在网络前被拒绝 | 初始化不会刷新原预算；max_retries=0，无隐藏SDK重试。固定100仅为测试估算；生产仍须接入run_model_attempt的完整请求估算/上下文快照/trace |
| 模型取消计账 | 被取消的等待关闭attempt，未知usage保留预留值，in_flight归零 | 取消不算零成本；不创建新用户轮或新预算 |
| 实际终止子进程 | 子进程完成工具并提交SQLite后被kill；新进程图读取结果，工具不重复；另在效果标记已写但回执未存时kill，恢复guard拒绝unknown重放 | 仅操作新建的固定测试子进程和临时文件。unknown标记为合成操作回执，生产应由HostTools现有持久账本提供，不能据此宣称外部exactly-once |
| checkpoint保存失败 | AsyncSqliteSaver公开aput模拟真实OSError；durability="sync"下工具结果checkpoint失败后不再调用模型 | 同步checkpoint边界是推荐接入条件；不默认依赖异步保存时序 |
| 私有协议负向检查 | 普通SQLite保存会丢掉DeepSeek消息中exclude字段；单纯加密也不会恢复已丢字段 | **禁止直接把默认checkpoint用于官方DeepSeek当前工具轮恢复**；缺状态应拒绝，不能静默重发不完整协议 |
| 私有协议正向扩展 | 测试用SerializerProtocol显式提取/恢复私有字段，EncryptedSerializer+既有cryptography加密，绑定owner/session/turn/provider/model；SQLite重开后现有DeepSeek adapter生成正确同轮payload | 合成私有值及空字符串均通过；错作用域/错key/篡改/明文/缺state拒绝，公开dump与repr无私有值。生产密钥存储/轮换、完整历史及stream chunk支持尚未实现；SQLite metadata不自动加密 |
| 摘要身份 | 同步/异步公开summary hook只给本次内部生成的摘要加可信internal_control标签；输入自带lc_source不被提升权限 | 实际create_agent调用后仍只有1条保留的真实用户消息；保持当前工具协议，新真实用户轮仍终止旧协议 |
| 摘要前原文存储 | 临时SQLite原文先提交；另一连接确认可读后才调用summarizer；INSERT触发器制造存储失败时，摘要和模型均不执行 | 证明公开hook能实施先存后压缩；生产权威原文/协议存储、CAS水位与失败恢复仍需P3接线 |

**本轮真实组合探针通过：**后台当前`opencode_go / deepseek-v4.1-flash`，自然语言→读取方法正文→读取必需资源→披露专用工具→问题暂停→模拟回答→原工具完成→最终答案。实际4次物理请求、账本4次model attempt、供应商报告6297 tokens、unknown预留0；4次graph重建、问题恢复1次、工具执行1次、HumanMessage始终1条，所有原生call/result配对完整。前两次请求工具仅ls/read_file，资源齐备后才加入read_fixture。答案匹配工具执行时才返回的随机marker与数值。耗时10.72s仅是单次观察；不是benchmark。

当前真实provider新增测试仅4次请求，未修改后台选择、账号、凭据或服务。429、取消、进程中断及私有DeepSeek协议均使用明确标识的离线/合成夹具，**未对真实供应商施加限流、故障或生产kill测试**。官方DeepSeek真实端点不在本轮真实网络覆盖内。

### P2出口与P3接线条件

公开`create_agent` + Deep Agents filesystem/skills + LangGraph SQLite的接入路径已获得上述正负向证据，不需要fork上游或重写循环。默认工厂的子代理/摘要行为没有直接启用。P2的**接口兼容与隔离探针**和P3的**生产接线验收**分开：测试适配器不直接进入生产。

**最终组合回归：113 passed in 7.31s。** 本轮在同一次运行中执行5个Harness测试文件与既有64项provider协议测试；没有沿用此前557项结果。Harness部分共49项：

- [基础公开接口](../src/agent/harness_compatibility_test.py)：5项。
- [Skill完整加载与恢复校验](../src/agent/harness_skill_gate_test.py)：7项。
- [取消、问题、进程中断与存储边界](../src/agent/harness_lifecycle_test.py)：7项。
- [持久预算与失败计账](../src/agent/harness_budget_test.py)：2项。
- [私有协议与摘要存储边界](../src/agent/harness_protocol_checkpoint_test.py)：28项。

P2 当时的出口：**兼容性探针通过，可以采用已验证的公开组件组合开展P3；当时生产接线保持基线。** 后续 P3 实施证据见本文第 6 节。 这不是宣布完整General Agent或部署验收完成。

P3实施必须保留以下硬条件：真实owner/session/turn/run及更新水位；完整ModelDriver计账与请求估算；HostTools/registry的授权、全文留存、幂等/租约及unknown对账；结构化Skill加载凭证与活动投影校验；先存原文后摘要；官方DeepSeek受保护协议存储和密钥管理；同步checkpoint提交与失败暂停。生产服务和真实用户数据上的重启、并发、取消迟到及跨库恢复尚未验收。strict入线也不证明供应商对全部非法参数都强制拒绝。

## 4. 共同接口契约（目标，未新增运行时schema）

以下固定字段语义，实施时复用现有 `ExecutionIdentity`、`op_receipt.make_receipt`、`tool_results`、jobs/operations及各领域对象，不另建重复权威状态。缺字段的旧记录应标明legacy/unknown，不补造执行事实；迁移格式带schema_version，未知新版本不得静默作为旧格式执行。

| 记录 | 必需语义 | 现状/边界 |
|---|---|---|
| 执行身份 | owner_user_id、session_id、user_turn_id、run_id、model_attempt及operation/call关联 | 复用现有身份；模型步/内部控制不是用户轮 |
| 完整工具结果 | operation、provider call ID、工具/schema版本、真实status/reason、全文引用与hash、来源/资产引用 | 全文保存成功才首次交付；不能只保存截断预览 |
| 执行checkpoint | 引擎/schema版本、身份、真实消息协议、步骤/累计预算/截止、pending操作、补充指令水位、结果/方法引用 | P3 已保存真实图状态与 host 状态，预算仍由原账本管理；跨引擎迁移兼容门槛仍须另行完成，导航 task_state 不是执行恢复 |
| Skill加载 | ID/version/hash、加载状态、资源/读取范围、turn/run、实际交付记录 | discovered/partially_loaded/loaded/failed；不等同审核执行 |
| 数值规则 | rule ID/version、输入版本/hash、executed/skipped/invalid_input/truncated/failed、实际范围、阈值/方法、位置/原因 | 复用rule_execution并补齐真实gate结果，不能预标整族已执行 |
| 文献审查 | audit_id、source/parser/rule版本、原始条目ID/定位、字段状态、provider/候选/时间/证据、进度水位 | 当前complete-only/latest-only不足；需逐条持久和完整audit导出 |
| 分析/图件 | dataset/analysis/script/run/figure版本与hash、参数/环境/来源、override revision、实际审核/导出引用 | 图件样式编辑不新算分析；初版审核不认证新版字节 |

公开事件只提供安全元数据与可授权回读引用；不包含私有推理、秘密或工具原始正文。Skill内容与模型建议不能注册任意函数、扩大授权或自行标记宿主完成。

## 5. 阶段状态与当前出口

- P0：基线提交与本地引用已完成，无远端推送或部署。
- P1：身份/四状态/结果及领域回执目标已整理；运行commit、镜像、前端hash和安全有效配置仍明确unknown，不虚报全量运行证据已核实。
- P2：兼容性探针通过；Conda环境/固定版本、113项组合回归、真实provider的完整方法读取/动态披露/问题接续/原账本计账已验证。明确记录默认框架缺口和测试适配边界；生产接线不是此状态的含义。
- P3：生产源码接线、加密持久循环、受控上下文投影及显式恢复已实施；本地临时数据、实际供应商和浏览器验证范围见第 6 节。既有运行服务尚未切换。
- P4–P8：通用工作区/完整按需 Skill 改造、绘图/数值/文献补缺与最终部署组合验收仍未完成。

通用命令worker的既有“Create RCE Surface”拒绝、停止8010的“Interfere With Workloads”拒绝仍有效；P3 未实现或改名接入被拒绝能力。文档、离线回归和当前供应商的小型真实探针均不等于完整产品验收。环境定义、固定版本清单、测试与证据文档尚未另行提交或推送；既有服务未重启或部署。

## 6. P3 生产接线与验证（2026-11-15）

### 实际执行路径

正常请求经过既有认证/job/租约和 session-load/prefetch，进入 `host.py → HarnessRuntime → create_agent`。LangGraph 执行真实 model/tools 节点并以 `durability="sync"` 保存。ModelDriver、CapabilityExecutor、HostTools/registry 分别保留实际请求计账、受控批次调度、持久操作及完整结果发布；生产 host 不再实例化旧 AgentRuntime。最终仍经过 session-save 和真实 done。

加密 SQLite checkpoint 绑定 owner/session/user turn/run/provider/model，显式保存公开消息及供应商私有字段，限制 metadata 内容。批次 journal 在派发前登记意图，完成后保存已发布观察；重启读取已完成批次，不重执行或覆盖全文。未知结果和未完成意图拒绝重放。恢复前重新校验结果完整性、原始 hash、归属和发布记录。

补充指令水位先随 host 状态同步落库，下一次计账模型请求前才确认 applied。取消/超时保留当前 host 观察；不会用旧快照抹去已经派发的结果。主进程执行锁、原账号租约和恢复 CAS 共同限制并发。

`POST /api/v2/chat/jobs/{job_id}/recover-agent` 仅接受空对象。原始输入只从受保护 checkpoint 取出，客户端不能替换 query、身份或预算。只恢复服务启动时标记 interrupted 的同 owner 任务；复用原 job/turn/run、原配额、原预算与截止时间。未知操作、待决 journal、取消和缺失内存候选池阻止继续。已生成完整答案的 checkpoint 可仅完成会话保存，不再请求模型。`agent_recovery` 事件开启新终态代次；旧事件不改写，前端明确解除旧终态并恢复获准的待应用调整。

### 活动上下文

`WorkingContextProjection` 先加密归档并以另一连接验证原文，再调用公开 SummarizationMiddleware。原始图消息保持完整，仅缩减发送视图；真实用户/系统消息、必要方法和私有协议组保留。摘要作为可信内部 SystemMessage，计入原任务预算/截止时间；相同前缀同运行缓存，重启后重建重新计账。`read_context_evidence` 按服务器身份提供精确公开消息 JSON 的分页/hash 回读；不公开私有推理。请求可设 `context_compression=false`。无法安全缩减时如实暂停。

### 已执行验证

| 验证 | 实际结果与覆盖 |
|---|---|
| 后端最终组合回归 | 品牌变更后同一次运行 **392 passed，29.20s**；`src/agent` 全部已收集测试、provider protocol、task protocol、jobs idempotency、admin dashboard。1 条 anyio 已加载导致的 pytest assert-rewrite 警告，无失败/跳过；临时数据，无真实账号 |
| 实际进程中断 | HarnessRuntime + 加密 saver + BatchJournal + execution_lock；工具已完成/journal 已存而图未提交时杀测试子进程，新进程接续，效果仅一次；效果发生/回执未存时恢复明确要求对账 |
| 实际 HTTP/SSE 生命周期 | FastAPI/httpx ASGI 调用真实恢复路由；验证未登录 401、缺 origin 403、替换 query 422、重复恢复 409、save-only 不调用模型、history_saved=true；旧事件前缀完整，新代次一个 done |
| 存储/授权故障 | checkpoint 写失败不确认更新；全文缺失/hash 损坏阻止恢复；跨 owner/session/provider、明文/篡改/key 不匹配拒绝；多进程 key 创建与重复大归档测试通过 |
| 真实供应商生产 host | `opencode_go / deepseek-v4.1-flash`；真实 ModelDriver、CapabilityExecutor、HostTools 和加密 saver，合成只读工具执行 **1 次**、模型步/计账请求 **2 次**；完整结果 hash、随机 marker 回答和 history_saved 均通过；5.34s 为单次观察 |
| 真实供应商上下文 | 26 条合成原始消息缩减为 7 条发送消息；原始消息不变、真实用户消息保持 1 条、摘要为内部控制、精确原文回读通过；1 次 working_context_summary 请求计账，估算从 10193 降至 2139 tokens；9.59s 为单次观察 |
| 浏览器恢复状态 | 隔离 8029 验证页运行实际 task-state.js：failed → 显式恢复后 running/1 条待应用 → completed/0 条；没有连接真实用户数据 |
| 品牌浏览器检查 | 实际 index.html/assets 的标题、助手和吉祥物说明均为 CitrusScholar，snapshot 与截图核实；静态夹具未提供会话/config API（404/501），外部字体受本机代理影响，不能据此声明完整应用运行通过 |
| 依赖检查 | 新增 HTTP/SSE、轻量文档导入依赖后 pip check：No broken requirements found；固定版本清单已更新 |
| 前端专项 | task-protocol **16/16**、frontend-isolation **24/24**；图件品牌单项通过。修正测试 DOM 节点移动语义、真实模块加载、动画帧及保存回执夹具，没有放宽生产校验 |
| 前端扩大回归 | 同一次完整运行 **150 passed / 10 failed，160 项**；figure editor 54/2、chat turn UI 24/8。相同最终夹具对 git-show HEAD 产品代码运行为 148/12，当前 10 个失败全部在 HEAD 复现；HEAD 多出的两项失败对应新恢复行为及品牌更正。全套前端未通过 |

后端复核命令（从本会话 scratch 目录执行，runner 将所有产品存储重定向临时目录）：

```bash
E:/anaconda/envs/CitrusSchalor/python.exe -X utf8 -I -B run-p3-tests.py src/agent src/core/provider_protocol_test.py src/core/task_protocol_test.py src/core/jobs_idempotency_test.py src/api/test_admin_dashboard.py
```

`run-p3-tests.py` 和真实供应商探针为本地 scratch 验证辅助，不纳入产品源码；产品内的对应测试文件是可审查的契约证据。真实探针只使用后台既有加载器取得内存凭据，关闭外部 tracing，不发送用户文档。初次真实请求曾因失效本机代理失败；仅对探针进程配置 provider 域名的 NO_PROXY 后通过，未修改生产连接配置。上述成功请求次数不包含前期诊断尝试，不作为累计费用或性能基准。

新增依赖：fastapi 0.163.0、sse-starlette 3.7.0、uvicorn 0.54.0、starlette 1.10.0、python-multipart 0.0.33、python-docx 1.2.0、numpy 2.5.4、Pillow 13.0.0、lxml 6.1.3（及锁文件记录的辅助包）。该 Conda 环境仍不是完整 GPU/RAG/科研部署；Linux 重建未验证。

### 前端未通过项

扩大回归留下 10 个基线可复现失败，并未删除或跳过：7 个旧上下文测试的 logical-call/attempt 身份、model_window 等 payload 与当前契约不一致；2 个 SVG 预览夹具缺浏览器 DOMParser/XMLSerializer/Blob URL 能力，仍期待 data URI，其中一项旧断言期待接受带 script 的 SVG，与当前校验冲突；1 个真实基线问题是 `task-cards.js` 会渲染合成 facts 的 script 字段及 visibility=internal 的结果引用。后者不等于已经发现真实凭据或供应商私有推理泄露，但公开卡片过滤存在缺口，本轮未修改其生产实现，已单独提出修复任务。后续附件断言因前一断言失败而未执行，不能记为通过。

只读 HEAD 对照通过 git-show 读取产品文件，没有 checkout/reset 或改动基线。完整日志、对照 runner 保留在本地 scratch 的 `fixture-full-final.log`、`fixture-current-tests-head-product.log`、`fixture-head-runner.cjs`，不作为产品入库材料。当前结果只支持 P3 专项和已列明的品牌验证，不支持全量前端验收完成声明。

### 品牌与未验证边界

可见品牌按用户更正统一为 **CitrusScholar**，覆盖页面、系统提示、生成物归属和产品文档。仓库仍为 QAagent；已有 Conda 环境名/路径 CitrusSchalor 保留可用。固定 Nature vendor 原始字节及历史证据不为拼写更新而改写。

现有 8010 服务、远程服务及 worker 镜像未切换。没有对真实用户数据做杀进程、恢复或账号修改；官方 DeepSeek 私有协议仅离线验证，真实网络测试使用配置网关。不同引擎/schema 版本间的迁移、部署 key 轮换/ACL、完整 GPU/RAG 和科研闭环仍须部署阶段单独验收。P3 证据不代表 P4–P8 完成，也不声称通用 exactly-once。此次没有新提交或推送。

## 7. P4 文件能力、按需 Skill 与显式选择（2026-11-15）

### 当前落地范围

- Deep Agents 0.7.38 的公开 SkillsMiddleware 负责简短目录；既有 skill_registry 仍负责可信资源、固定版本及包 hash。typed `load_skill` 全量加载根方法与依赖，`read_skill` 提供精确分页。真实身份、必需资源完整范围和实际交付字节共同决定加载状态；摘要、名称、旧 loaded_methods 或部分入口读取不授予专用能力。
- 专用工具在完整方法后披露并在派发前复核权限/版本；通用文件工具保持独立。初版科研图仍要求固定 Nature；后续 figure_edit 只要求编辑方法。方法在实际发送视图中丢失时暂停；检查点保存回执，原始资源变化不继承旧状态。一次全量加载的编码结果上限 240000 字符，超限失败，无裁剪成功；可分页读取。
- `GET /api/v2/skills` 认证后只返回目录元数据。`selected_skills` 最多 8 项精确 `{id,version,sha256}`；新请求在配额准入前拒绝旧版本和当前不可用选择，host 再核对实际附件/权限并加载。原始 query 不变，选择进入原始请求、Human metadata、恢复和用户问题续接。“＋”组件处理多选、刷新、失败、账号/会话切换和迟到响应。
- 六个 `workspace_*` 工具复用公开 StoreBackend 和原有 session_files。`/input/<file_id>/<filename>` 为只读输入，`/workspace/...` 为会话持久文本；版本索引/pin 同库原子发布，写入要求当前版本与 hash，旧版本继续可读。跨轮恢复文件不用新建内容资产库。全局注册工具不捕获请求级 SkillSession；ContextVar 绑定/清理经过并发隔离测试。
- 正常生产图为 session_load → supervisor → session_save，恢复直接进入 supervisor。已去掉正常入口的隐式 candidate_prefetch；模型按用户目标和来源授权选择小型 local/web 工具批次。旧候选兼容函数仍保留。local 搜索 memo 增加 owner/session/job 作用域；无完整身份不缓存。

### 实际验证结果

| 范围 | 证据与结果 |
|---|---|
| 最终后端组合 | **454 passed，1 skipped，1 warning，66.95s**；覆盖 src/agent、provider/task protocol、jobs idempotency、admin dashboard、core model context 和 candidate graph。真实临时 SQLite/文件库与合成身份；唯一跳过是 Windows WinError 1314 阻止创建真实符号链接，硬链接拒绝及符号链接 guard 单独通过。warning 为既有 anyio pytest 预加载 |
| Skill/接口 | 目录认证、metadata-only、错版本/hash/重复/extra字段、当前无附件却选择数据审查时配额前拒绝；完整资源/依赖、分页缺口、权限撤销、活动视图丢失、恢复和并发 ContextVar。原始请求文字独立传递 |
| 文件/操作账本 | 真实版本持久、历史回读、并发 CAS、跨 owner/session/job 拒绝、输入只读、路径越界/存储篡改、精确 Unicode/CRLF、大小/计数界限；主 host 中 create → 已知 CAS 冲突 not_run → 正确版本 edit，三项操作与完整结果都保存，未误记未知副作用 |
| 真实供应商自然语言 | 后台当前 `opencode_go / deepseek-v4.1-flash`，生产 host/model/operation/tools。第一轮未预选方法，模型自主执行 load_skill → workspace_write → workspace_read；**4 次模型步/实际计账请求，34.56s**，history_saved=true，全文 hash 通过 |
| 真实供应商跨轮/多选择 | 同一临时会话第二轮预选 statistics + word-export；host 先加载两项方法，模型实际执行 read → edit → read → read；**5 次模型步/实际请求，29.60s**。旧版本不含标记，新版本完整保留旧文字并增加标记；两轮均 completed、history_saved=true。没有执行统计或 Word 生成，这些方法的加载不等于专业操作通过 |
| 前端专项 | **46/46 passed**：skill-picker 6 项、task-protocol 16 项、frontend-isolation 24 项。实际 app.sendMsg 的 payload 将选择和 query 分开，账号迟到回包等隔离保持 |
| 浏览器组件 | 隔离 8031 页面使用实际 index 菜单片段、app/skill-picker CSS 和 picker JS，真实 registry 目录；浏览器多选并发送验证请求，接收体保留原 query 及两项 id/version/hash，确认后清选择。375×812 布局检查、浅/深主题计算样式与截图通过；组件无 console error/失败网络请求。该页使用合成接收器，不能证明完整聊天服务已部署 |
| 依赖与静态检查 | requirements-base 固定 deepagents 0.7.38；补装既有上下文回归需要的 pandas 3.1.1、python-dateutil 4.0.0、tzdata 2026.6，精确版本清单更新。pip check 无冲突；git diff --check 无空白错误，仅现有 CRLF 提示 |

最终后端范围在既有 scratch runner 中以隔离存储执行：

```bash
E:/anaconda/envs/CitrusSchalor/python.exe -X utf8 -I -B run-p3-tests.py src/agent src/core/provider_protocol_test.py src/core/task_protocol_test.py src/core/jobs_idempotency_test.py src/api/test_admin_dashboard.py src/core/test_model_context.py src/graph/test_candidate_graph.py
```

本地证据为 scratch 下 `p4-backend-final.log`、`p4-frontend.log`、`p4-live-host.log` 及 `live-p4-host-probe.py`、`p4-skill-preview.py`；辅助脚本/运行材料不入产品库。真实探针使用合成小文字和临时会话，没有真实用户文档；凭据仅由既有后台加载器使用，不打印。只给探针/依赖安装进程设置必要域名的 NO_PROXY，没有修改生产网络配置。前期诊断曾遇临时文件清理错误、无附件选择 data-audit 被拒绝；后者促成配额前可用性校验。成功两轮的 9 次请求不包含前期诊断请求，不是累计费用或性能基准。

### 完整 P4 门槛仍未通过的边界

通用 execute 仍受此前自动审批拒绝 **Create RCE Surface** 限制；没有实现、改名或委托绕过。当前只能证明持久文件读写/修改，不能宣布“写→执行→诊断→再执行→交付”完整链路通过。文件当前仅支持受限 UTF-8 文本后缀和逻辑路径（ASCII 文件名），64 KiB/文件、1 MiB/视图、128 文件及每路径 128 版本；版本固定，无删除工具，也没有通用输出发布/下载能力。中断 staging 可遗留未引用内部条目供现有清理处理；敌对本机进程并发替换目录的防护未验证。

真实 local+web 联合检索尚未在完整 GPU/RAG 运行环境验收；当前证据仅证明没有旧隐式预取、主模型提示和工具策略、缓存隔离，不能以提示词证明已读取两类真实来源。P3 记录的全量前端 10 个基线失败仍未在本轮修复或重新宣告通过；结果卡片修复独立会话启动失败，没有产生修改。现有 8010、远程服务和 worker 均未切换。此次未提交、推送或部署。P5–P8 仍按完整计划继续，当前结果不代表完整 General Agent 或科学闭环验收。

## 8. P5 开放绘图与统一工作台（2026-11-15）

### 已接通的产品机制

四个用户场景为科研检索、文献核验、数据审查和科研绘图。`GET /api/v2/skills` 与“＋”仅展示这四项；内部 Nature、统计、文档交付和图件编辑方法仍按需组合，方法数量不等于场景数量。生产主提示、兼容提示与 README 统一说明：图片编辑属于工作台，自然语言与画布共用 `figure_inspect/figure_edit → projects.save_overrides`。

脚本 v2 输出契约支持冻结 typed tables 和独立 `render_script`。宿主保存私有冻结快照并核对 worker 的 renderer/data/geometry 摘要；纯样式任务只送冻结结果与 renderer，原分析源码和原始输入不进入重绘。支持范围之外的数值几何允许生成，但不标为可保真的样式编辑。失败预算默认继承任务模型调用上限，并支持配置和旧运行预算继承，不再固定最多两次修复。

正式保存校验 `expected_revision` 后替换当前图件，并固定其依赖 pin。按 2026-11-15 用户收敛要求，图件历史面板、历史 API、`figure_history`/`figure_restore` 与历史持久化已移除；项目只保存原始脚本、必要冻结数据、当前 overrides/manifest 与当前导出，`render_version` 仅用于并发冲突检查。旧库 `figure_revisions` 由迁移先建立当前依赖再释放并删表，不删除真实文件。项目支持 SVG/PNG/PDF/TIFF，以当前实际导出记录启用下载。新增 grid/errorbar/colorbar 语义以及柱填充/边框/透明度；可编辑属性由 manifest 给出，不开放任意 SVG 路径。

工作台保留未保存草稿/当前保存状态边界，过期请求和 CAS 冲突不得覆盖当前项目。辅助网格和毫米标尺默认开启，仅作编辑定位，不进入导出；导入表单按数据文件、变量/单位和出图操作整理，展开区可滚动。

### 本轮实际验证

| 范围 | 结果及限制 |
|---|---|
| 主 Agent/上下文兼容 | 本轮重跑 454 passed、1 skipped、1 warning（45.11s），不是沿用 P4 数字；Windows 符号链接权限跳过及 anyio 警告仍在 |
| 科研发布与生命周期 | 72 passed，覆盖冻结发布、脚本运行/修复、pin 保留、旧混写脚本拒绝样式重放与四场景 API |
| 冻结引用与提示 | 后续 29 passed，包含畸形冻结引用明确拒绝，以及现役兼容提示修复预算更新；与前述范围有重叠，不合计成唯一测试总数 |
| 科学库兼容 | S4 14 passed，包含全精度分析结果快照；独立 worker 50 passed/1 skipped，审核/历史/生命周期/output 61 passed/1 skipped。离线 worker 测试不证明容器隔离 |
| 前端 | 工作台 67 项与场景选择 6 项，共 73 passed；mock Scene 仅用于 Node 控制器 fixture，不作为真实浏览器解析证明 |
| 实际受控容器 | WSL Docker 测试镜像生成合成 ECDF（现有声明式模板外），实际保存 SVG/PNG/PDF/TIFF；样式重绘时断言 script=pass 且仅有冻结 ecdf 输入，receipt 不变；恢复旧版后 SHA 与字节相等 |
| Nature 真实检查 | 上述容器初版的 PDF 字号/碰撞等渲染检查通过，source preflight 为 review_required；没有把整项报告写成全部通过。用户编辑标记未重新审核 |
| 浏览器工作台 | 隔离 8033：线宽保存为第2版，恢复第1版为第3版，SVG SHA 相同；打开主水平网格后保存第4版，冻结 receipt 与第1版一致。脏草稿禁用恢复；TIFF HTTP 200、image/tiff、有效文件头，3224674 bytes |
| 四场景组件 | 隔离 8034 使用当前菜单/选择器与真实目录，恰好4项；提交 figure-initial 2.1 的真实 id/version/hash，原 query 独立保留；不是完整聊天服务验收 |
| 表单/辅助显示 | 浏览器确认 grid/ruler 默认勾选、单位字段高36px且不与变量重叠，窄屏无水平溢出；选择 time/value 后按钮可用。console/server 无错误；后台窗口导致早期截图超时，之后成功截图并核对当前布局 |

上表中涉及“恢复第N版”“历史 SHA 相等”的条目记录的是已退役的历史恢复功能在当时的验证结果。2026-11-15 用户要求取消图件历史后，历史/恢复接口、前端面板与持久化已从当前源码移除；这些条目只作为当时行为的留存证据，不代表当前版本仍提供恢复能力，也不作为当前保存路径的通过证明。当前只保留原始脚本、冻结数据、当前 overrides/manifest 与当前导出，重新验证见统一回归记录。

早期镜像 `citrusqa/research-sandbox:p5-20261115` 的构建 ID 为 `sha256:5bb8fb00b41f59da1232c8fbe6139b762ffa26ab0d40e5d7aee7430d34181781`。后续用当前 worker 源码白名单另建 `citrusqa/research-sandbox:p5-final-20261115`，ID 为 `sha256:df82902dccd2c3e64c89a90d6a8fc9bb655fa9550670fef65b8f5b4b9df00cde`，各复制文件摘要留在本地验证记录。新镜像实际复验通过：四格式发布、仅冻结表/renderer重绘、恢复第1版为第3版及旧字节核对；初始 Nature 仍为 review_required，不改写为全通过。两次构建均基于既有科学依赖镜像，不是全新 Linux 依赖可复现构建。包内 worker 和 frozen_render 打包路径已补齐；原镜像标签及现有服务未切换。

验证使用隔离账号身份、临时 SQLite 和明确标记的合成数据，不接触真实账号或研究材料。科学验证依赖安装于 CitrusSchalor，已记录 Windows 版本；镜像源临时不可达后由官方 PyPI 安装 statsmodels，未修改系统代理。`pip check` 通过。辅助探针与日志保留于 scratch，不入产品库。

### 计划核对与限定清理

对照用户指定完整计划第7、10、11节，本轮仍执行 P5 的开放生成、冻结分析与统一编辑；清理属于贯穿各阶段的 P8。四个前台场景按用户后续明确要求收敛，内部方法仍可组合；没有新增科研路由器、第二个主 Agent、Origin/R 或完整表格平台，也没有提前实施 P6/P7。

引用审计后删除 `graph/agent_operations.py` 中五个已无调用的旧控制片段：`_FINAL_INSTRUCTION`、`_MAX_SOURCE_ENFORCEMENT_ATTEMPTS`、`_contract_repair`、`_control_message`、`_question_context_for`。现役共享辅助函数和旧记录读取兼容继续保留；这不是全仓死代码清理完成声明。主 host、边界、Harness兼容、来源发现与候选图的针对性回归为 **41 passed，1 warning（9.63s）**。

### 真实供应商与剩余边界

真实供应商调用发现并修复两处接线问题：无旧 task_state 快照时应返回真实持久任务/操作而非失败；已知只读分支失败不能升级为未知写。输出契约在 run 预留前的纯校验拒绝改为 not_run，保留已确认 script_ref，使主循环能纠正；存储、worker 和发布的不确定失败仍保守停止。对应回归验证了只读失败/超时/取消、写失败未知保护及同循环纠正。

真实供应商探针先后暴露输出契约错误、单次代码输出截断，以及 worker 已返回 SCRIPT_ERROR 却被执行层提升为 unknown 的问题。后者对应模型向只读当前目录自行 savefig，并尝试不存在的上游审核模块；没有为此放宽文件系统或降低审核。已在本地方法适配说明中明确 worker 统一导出/审核；方法注册、加载与工具披露专项 **34 passed，1 warning（5.54s）**。单次输出上限 16384 仅用于隔离探针，生产管理员配置不变；已保存失败观察不会被新的独立探针覆盖或冒充恢复。

已加入限定的失败对账：仅受控 worker 已确认退出、没有发布尝试、失败 run 成功落库，且账号、持久 job 的 session、源码引用、返回观察摘要及冻结输入字节全部吻合时保留 failed，让同一循环按原预算修复。证据由宿主调用作用域保存，模型参数和 worker JSON 不能自报授权；超时、未知退出、local 后端、丢失落库确认、发布中断或引用篡改仍为 unknown。相关组合回归 **160 passed，1 skipped，1 warning（46.98s）**，包含21项新对账用例；这是替身 worker + 真实 host/操作存储测试，不是实际容器失败修复的证明。旧沙箱测试补齐合成账号/会话并断言当前 JSON 回执，保留无后端拒绝和非法源码不执行的不变量，**28 passed，1 warning（8.44s）**。

对账修复后的真实模型探针已实际生成图件，并因网格0.4pt低于0.5pt的本地 QA 门槛主动改成0.6pt继续调用。第二次调用在输入快照登记时遇到 `cannot replace a resource-referenced file`，因此仍为 partial/unknown；没有把这次结果记为通过。根因是同内容 dedupe 试图更换已 pin 文件路径。文件层现对相同字节核验原路径、大小和摘要后保留原文件，清理新建的重复副本；更改字节、损坏引用和显式换路径继续拒绝。长 dedupe key 在查找与刷新时统一64字符。存储引用、配额、账号/轮次与科研快照组合 **70 passed，1 warning（6.70s）**；新增集成测试验证多次运行读取同一已固定快照。

修复快照后的探针在同一轮中完成一次输出 JSON 拒绝后的纠正，并成功执行三次生成，但最终以 resource_or_execution_conflict 停止；当时累计预算为350000 tokens，账本已有9次计费请求/296933 tokens，不能把已有图件当作两轮验收通过。该观察还发现精确 `/figures?project=<32位ID>` 被通用路径脱敏误删；已仅为此精确公开路由保留链接，附加路径/查询和私有路径仍脱敏。脚本资产、运行和对账组合 **101 passed，1 warning（24.73s）**。

后续独立探针把累计 token 上限改为后台配置值（实际1000000），单轮仍最多18次模型调用、600秒，单次输出16384；这些都是测试记录，未变更生产管理员配置，也未给已停止的旧任务重置预算。该次探针在第4个模型步收到 invalid_tool_call，宿主未执行非法调用，首次生成未完成；仍不计通过。

此前真实模型保存的项目 `835a348912ed46ca8d7885cbf7a37a39` 经文件/冻结表回读核验：七值完整保留，重复值2仍在，排序位置1…7与累计比例1/7…1一致，SVG/PNG/PDF字节与保存摘要一致。本地可测 QA 通过，Nature 源码 advisory 仍为 review_required。后续真实模型验证使用同一会话的新用户轮，只改已有项目；不是恢复或重放旧轮未决操作。历史存储、API预览和图件生命周期 **45 passed，1 warning（88.39s）**。真实供应商跨轮接续现已通过：前一轮 partial 记录不改写，新用户轮自主加载编辑方法、读取对象并正式保存，同一项目由第1版到第2版；7个模型步，completed、history_saved=true。数据线1.8pt、主水平网格、冻结receipt及真实三格式导出均核对；没有新增分析运行或项目。这证明跨轮接续，不等于两个连续 completed 轮次；后者仍单独验证。共享文件层改动后，会话工作区回归25 passed、1 skipped（Windows符号链接权限）、1 warning。

本次同项目浏览器验证遇到预览工具每工作树最多5个实例的上限；关闭本轮临时8034测试页也被自动审批以 Interfere With Workloads 拒绝，因此未关闭或换工具终止任何实例。此前8033的浏览器验证仍有效，但不能冒充对真实模型当前项目的浏览器验证。合成 ECDF 不能证明真实科研材料的分析有效性；完整 GPU/RAG、Linux 重建、真实账号整站、全部前端基线和远程部署尚未验收。没有执行被拒绝的通用 execute，没有停止8010，也没有提交、推送或部署。

## 9. P5–P8 收敛与统一回归（2026-11-15）

本节按用户后续修订执行：先完成 P5、P6、P7 的实现和接线，再统一运行必要基本测试；服务由用户自行启动实测。此前各节中的历史版本恢复记录仅描述当时功能，不适用于当前已退役接口。

- P5 移除图件历史存储、浏览、恢复和历史下载；只保存原始脚本、必要冻结数据、当前 overrides/manifest 与当前导出。内部 render_version/CAS 用于并发保护。新依赖先固定再提交，提交后释放旧依赖；旧 snapshot 表迁移不批量删除用户文件。普通 Text 标签的受支持几何纳入冻结指纹，旧 unsupported receipt 不自动升级；并发或只读拒绝作为 not_run 观察返回同一 Agent。
- P6 共享有界表格加载器与来源坐标；逐规则 executed/skipped/invalid_input/truncated/failed 回执，精确范围/关系比较，人工或模型注释独立保存。原始 token 尾数、顺序等差/滑窗、重复模式、复合键与实验结构均按显式配置适用；补充材料的 sheet/表解析、归档限制和总体报告截断独立留证。无 OCR，Excel 不重算公式，XLS/XLSB 需要 calamine；启发式信号不证明不端。
- P7 聊天、PDF 面板与 API 共用持久 ReferenceAudit。保留原条目、重复/无 DOI、解析范围和逐字段差异；错误 DOI 可独立标题检索，存在性与身份/字段分开。每条完成后落库，取消/租约/截止时间防迟到覆盖，resume 不补请求预算。按明确 audit_id 全量导出 JSON/XLSX/RIS/BibTeX；拒绝 last_verification，发布失败仍保留可续查结果。JSON/XLSX 保存完整审查证据，RIS/BibTeX 为含状态注记的书目投影。fallback 解析明确 partial，未接入 GROBID/OCR/额外 LLM 裁判。
- P8 删除无引用的 latest 文献发布辅助函数，同步 Skill、宿主政策、提示词与 25 项工具 schema。自然语言与工作台继续共用既有 operation runner，没有新增第二个 Agent Loop。

统一回归结果（组合运行与修复后定向复测，不重复累计同一用例）：领域/API/交付范围最终 618 项通过、6 项跳过；主 Agent/边界/Harness 34 项通过。前端图件编辑 57 passed，文献面板/会话隔离 12 passed；25 项工具 schema 漂移检查、接线模块 AST 语法、pip check 及 git diff --check 通过。6 项跳过为图像近似匹配的 OpenCV 可选依赖缺失，本轮没有扩装图像运行栈。

完整组合首次为 594 passed、24 failed、6 skipped（108.38s），随后修复并定向复测，没有把失败计入通过。主要迁移旧测试的默认自动发布/人工注释替代扫描假设，保留显式导出、权限、来源坐标、三类注释严重程度与发布失败不伪造链接等不变量。数值相关复测为 148 passed、2 failed，最后两份断言修订后 42 passed（1.84s），覆盖剩余失败。主循环专项 34 passed（9.87s）。各 Python 进程有一条 anyio 已预导入导致 pytest 无法 assertion rewrite 的测试启动警告，不是业务规则警告。

回归修复包括：历史页移除后的键盘导航按实际标签数循环；普通 Text 已支持而 Annotation 仍保守拒绝的冻结测试更新；Excel 合成夹具在写入 XML 时保留真实大整数，避免把写入前已舍入的数据当作读取丢精度；Windows 离线网络防护允许 asyncio 的本地唤醒 socket；注释错误保留未知工作表及可用目标，注释覆盖独立于确定性扫描覆盖；IQR 值显示保留原数值表示，阈值仍精确计算；文献文件读取统一 20MB 且实际读取有界。隔离 CitrusSchalor 环境补齐已声明的 pdfplumber 依赖（0.11.10），未切换运行实例。

验证使用临时数据库、小型合成契约夹具及替身登记库，不是真实科研结论或联网供应商验收。本轮未启动/停止服务，未切换现有实例，未部署，未提交或推送；用户实测后的日志检查仍待真实运行记录。完整通用 execute 的既有审批阻塞、GPU/RAG 联合检索及 Linux 重建不在本轮通过范围内。

