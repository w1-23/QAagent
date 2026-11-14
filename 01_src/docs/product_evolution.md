# CitrusSchalor 产品演进契约

日期：2026-11-14。依据为 `feat/product-evolution` 当前本地源码；本文件不代表服务器已更新。生产读写审批、真实容器与 GPU 模型验收、离线工程测试分别记录。

## 运行与职责

保留 FastAPI、LangGraph 单协调 Agent、原生前端、SQLite 和 Matplotlib。公开工具名由 `src/tools/__init__.py` 唯一注册表声明，当前 27 个；可见性依据来源、运行能力与授权，不靠主题关键词强制流水线。既有 `RequestPlan`、`ExecutionLedger`、`ToolOutcome`、`TurnEnvelope` 继续承担任务事实与终态，新增协议不另造成功真相。

前端实际抽离 `task-state.js`、`task-transport.js`、`task-cards.js`、`task-controls.js`，依次在 `app.js` 前加载。实时 SSE 与补发事件共用 reducer；领域卡读取适配器给出的 facts/result_refs/artifacts，不解析散文猜数字或工具成功。既有背景和三主题 token 保留。

## 资源、分析与交付

`session/files.py` 保存账号文件和依赖引用；`core/resource_refs.py` 提供 ResourceRef 与任务输入租约。`AccountLease` 的保留子任务共享引用计数，最后一个实际执行单元结束才释放任务 pins；取消一个协程不等同于底层线程已退出。

账号资料库新增登记在 SQLite 写事务内检查字节和数量。默认上限为 10 GiB / 10,000 条登记，可通过 `CITRUS_ACCOUNT_STORAGE_BYTES` / `CITRUS_ACCOUNT_STORAGE_FILES` 设置正整数。已有超额账户可幂等读取/重试或缩小，不能继续增长。配额不自动删除旧内容；生成文件登记失败仍由 publisher 保管真实字节和待恢复事实。

图件 API 的写入/重绘默认每账号同时 2 个、单 worker 总共 4 个，超额返回 429 / Retry-After，不建立无界等待队列；保留的后台线程结束后才释放名额。配置为 `CITRUS_FIGURE_ACTIVE_PER_ACCOUNT` / `CITRUS_FIGURE_ACTIVE_TOTAL`。项目创建在 SQLite 写事务内检查 `CITRUS_FIGURE_PROJECTS_PER_ACCOUNT`（默认 500）；达到上限仍可读取和修改现有项目，不自动删除旧图。图件只保留最近正式保存状态及未保存预览。Word 草稿版本由 `CITRUS_WORD_MAX_VERSIONS` 限制（默认每草稿 100，初始快照计入），在既有事务内检查；达到上限仍可读取、发布和幂等重试，拒绝新增版本但不删除历史或解除其来源保护。多 worker 部署需要共享容量状态。

`research/analysis_results.py` 将 DatasetSnapshot / AnalysisResult 存入 `state/research_artifacts.sqlite3` 的 `scientific_snapshots`。引用包含 kind/id/version/sha256，每次读取检查实际账号、准入状态、数据摘要和关联快照。保留源文件、sheet/header、原始行 ID、单位、设计、排除、覆盖和实现版本。统计计算不提前舍入；展示说明放在 presentation。

`analyze_research_data` 支持当前请求表格、已持久化 dataset_ref，以及没有观测文件的前瞻 t 功效参数。后者明确为零观测行的设计参数快照。含糊或不支持的设计拒绝计算。方法覆盖 Welch、明确配对、相关/列联及经验证的 HC3 回归、因子交互、完整区组、平衡重复测量、随机截距混合模型、t 功效和平衡随机化；具体适用限制随结果返回。

`analysis_delivery.result_table` 直接读取已验证 AnalysisResult。Word 的 `analysis_table` 和 `export_table` 的 analysis_result_ref 不接受模型覆盖 rows/columns；图件脚本获得同版 analysis_result JSON 与 analysis_data CSV。Word 每个保留版本的图片与数据来源 pins 在提交前登记，失败补偿只释放该次独立引用；账号清理先释放 Word/图件/分析/复核引用，再清理文件。

## 任务协议

既有聊天容量门禁同时限制全局与账号活动/排队数：默认单 worker 10 个活动、100 个排队，每账号 2 个活动、10 个排队；配置为 `CITRUSQA_MAX_ACTIVE_CHATS` / `CITRUSQA_MAX_PENDING_CHATS` / `CITRUSQA_MAX_ACTIVE_CHATS_PER_OWNER` / `CITRUSQA_MAX_PENDING_CHATS_PER_OWNER`。账号身份由服务端传入，保留 FIFO、取消清理和附件等待字节上限。

持久任务协议为 `citrus-task/1`。`GET /jobs/{id}/events?after=n&limit=200` 返回有序 seq、next_cursor、has_more、status 和 terminal_snapshot；SSE numeric id 与 data.seq 一致。前端缓冲缺口和去重，不直接跳到服务器宣告的游标。

任务补充要求通过 `POST /api/v2/chat/jobs/{id}/updates` 的 expected_version/instruction 做 CAS，并在安全边界应用。批准/拒绝通过 approvals 的显式决定提交；兼容旧 permission/grant 与 permission/reject。

`POST .../{id}/resume` 仅接受服务器声明可恢复的 interrupted 操作，模式限 local_read/pure_computation。原轮次终态保持不变，恢复操作保存自己的实际结果；`GET /jobs/{id}/operations/{operation_id}/result` 重新检查 owner。外部副作用或不明提交不会自动重放。

所有接入的模型物理尝试记录 attempt 与 usage，重试消耗同一个绝对截止时间、调用次数和 token 预算；翻译与 thinking fallback 也计入，SDK 隐式重试关闭。持久事件和通知队列具有独立职责，队列有界而事件可按游标重取。

## 科研图件

声明式创建、编辑和资料库导入先在父进程授权并解析数据，再通过 `figures/trusted_render.py` / `trusted_worker.py` 把解析后的数值、样式和编辑 JSON 交给一次性子进程。默认同时 2 个子进程、最多 6 个等待，默认 30 秒总时限（含等待），2 GiB 内存；输入、输出、文件数量及错误回执均限额，stdout/stderr 不累积。Windows 先挂起启动、绑定有资源限制的 JobObject 再运行；POSIX 在引擎导入前设置资源限制。取消/超时清理自有进程组或 JobObject，资源回收后才归还名额。子进程不接收 Python 脚本、账号路径、数据库或凭据，不传输 Figure pickle，也无 Web 进程内渲染回退。它是可信声明式渲染的资源边界，任意脚本仍须使用下面的隔离 runner；实际 Linux 进程限制仍待现场验收。

主要路线为 Matplotlib/Seaborn 脚本、figure contract、适配的 nature-figure 工作流、Artist manifest、白名单 patch 与同状态 SVG/PNG/PDF。安全 runner 不可用时关闭脚本；LocalBackend 仅限显式开发测试，不作为部署回退。

声明式 panels/layout/shared_legend 保存稳定 panel/series 身份；每个 panel 独立绑定数据。共享图例用显式 source links 同步系列与图例；冲突编辑拒绝。任意脚本的 figure-level 图例无法确定源 handle 时限制联动，不根据标签猜测对象身份。

脚本输入复制为 owner 绑定的不可变文件快照，worker 在复制后校验 SHA256。修改或删除原文件不改变已保存图件；快照损坏/跨账号/引用校验失败会阻止重放。Web 进程不运行不可信脚本、不反序列化 Figure。

当前预览与最近正式保存分开。编辑后真实重绘；保存使用 expected_revision CAS。导出保留可编辑 SVG 文本，导出上下文不改变全局 rcParams。QA receipt 测量最终尺寸比例、可测字体/线宽、有限坐标、图注和画布边界，并列出不支持项，不称为 Nature 认证。

## 诚信证据

版本化 Finding 保存源摘要、区域、算法依据、适用条件、反例与覆盖；`state/integrity_reviews.sqlite3` 保存不可变报告和追加的人工复核历史。`/integrity-review` 支持原始区域对照、说明与 CAS 冲突；无“证明造假”状态。

实际 audit 工具连接数值复用、绑定表格汇总一致性和图像匹配检测，并返回真实 review_url。表格 n/均值/样本 SD/SE 核对要求明确原始行、报告单元格、单位与精度；没有可信绑定的正文/OCR/p 值不会自动判定。图像近似配准记录版本、几何和光度证据，阴性或无命中必须带覆盖说明。已实现有界的单图局部重复 ORB/RANSAC、轴向长边界及背景阶跃观察、灰度端点累积观察、非认证的来源元数据清单，以及精确有理数的小数尾共享核查。图像工具接受 1–8 个账号文件；信息性来源清单不算异常线索。每项保留正常拼接、共享对照、单位换算、曝光/导出等反例，不据此认定科研不端；任意接缝、非刚性处理、未经绑定的正文和真实采集历史不在穷尽检测承诺内。

## 部署和恢复边界

`/health` 是进程存活；`/ready` 检查存储、检索/模型和已缓存的实际 worker 探测。readiness 请求不初始化/下载模型，不每次启动容器。`CITRUS_REQUIRE_GPU=1` 要求两个模型已真实前向计算，GPU session provider 不得静默变成 CPU；重排器还要求观察到 GPU kernel profile。FastEmbed 不暴露 profiling 选项，该限制如实保留。

Apptainer 使用固定绝对 SIF 路径和真实 SIF SHA256；CPU 绘图 worker 与 GPU 应用分开。worker 无网络、只读最小 engine/input、单独 output，由 systemd 用户 transient service 验证并约束 cgroup CPU/内存/PID/时限，取消清理整个 cgroup。镜像、mount 和环境不允许由模型任意指定。

发布工具位于 `engineering/product_evolution/release/`。源码发布排除 state/embedding/模型缓存/密钥；完整备份必须覆盖所有 SQLite 与引用字节，在统一停写边界验证和恢复演练。仅本地合成恢复测试不等于服务器恢复通过；未知 schema/cache/nested-runtime 配置保持阻塞。只更新 8030，8000 及其数据保持不动，不用旧快照覆盖新数据。
