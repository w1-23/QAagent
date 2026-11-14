# CitrusSchalor 完整产品演进：实现与验收证据

日期：2026-11-14。本地目录 `E:/codex_WORKSPACES/CitrusQA_Local`，分支 `feat/product-evolution`，继承基线 `e201c8cfc1a319de0be3b156f026d2a16b48483f`。本报告对应当前未提交工作区；不以 GitHub 旧源码代替本地实现。

**当前状态：科研、资源、任务、绘图与前端的本地实现已集成，最终八组工程回归 1,603 项通过、4 项跳过、0 失败；独立渲染进程接入后的完整应用 ASGI 交付检查 19/19 通过。S6 真实服务器发布与 GPU/容器验收仍被审批阻塞，因此不能把整份计划标记为全部完成。**

## 阶段与真实完成门槛

| 原计划 | 当前实现 | 已有证据与限制 |
|---|---|---|
| S0 基线与隔离开发 | Local 分支与原有 dirty 工作保留，未提交/推送/合并 | 本地工作区；未操作 embedding 或其他实验仓库 |
| S1 正确性与安全根因 | 请求图件隔离、账号 epoch/reset、排队/附件取消、X/Y 行身份及真实坐标、全精度统计/公式安全、样式白名单与坐标变换、有界输出与 SVG 图像安全；声明式生产渲染在一次性受限子进程执行 | 纳入最终 figures/research/contracts/frontend 门禁；真实容器边界另验 |
| S2 资源与交付 | ResourceRef、实际工作租约、持久来源 pins、CAS、统一 publisher 与 publication_outbox 恢复、账号停用/排空/清理、资料库容量及图件/Word 配额 | 真实登记失败/哈希/跨账号/清理/并发/异常连接关闭测试；跨库不是全局原子事务，crash orphan audit 只报告不自动认领 |
| S3 科研增强 | DatasetSnapshot/AnalysisResult；Welch/明确配对/回归/交互/区组/重复测量/随机截距/功效/随机化；数值复用、表格汇总一致性、图像复用与局部观察；统一 Finding 和人工复核 | 诚信专项最终 201 Python + 4 JS 通过；统计设计含独立公式及 SciPy 对照；夹具不是科学灵敏度/特异度研究 |
| S4 脚本绘图与编辑 | Matplotlib/Seaborn、固定来源的 nature-figure 工作流适配、语义文字/系列/图例联动、分组选择、撤销/重做、未保存预览/最近保存、CAS、多 panel/shared legend、固定输入重放、QA 回执、三格式导出 | 最终 figures 444、API 142 通过（另有平台/夹具 skips）；包含真实 Windows 子进程创建/编辑/导入/取消/超时/清理验证；LocalBackend 工程验证不等于 Linux SIF 验收 |
| S5 Agent 与产品交互 | 持久有序事件/游标、唯一终态、版本任务更新、显式批准/拒绝、安全操作恢复、实际结果查询、共享模型预算和迟到发布防护；真实前端 transport/state/cards/controls 模块 | 后端专项 230 通过；最终前端专项 121 通过；只恢复服务器明确标记的安全操作，不重开整个中断 Agent，不自动重做外部副作用 |
| S6 回归与发布 | 统一离线入口、独立证据目录、六章指南和架构/使用/部署文档、当前文件系统源码包、备份/隔离恢复/兼容性回滚门禁、严格 GPU 启动 | 本地发布工具 22 合成测试通过；真实 8030 停写备份/恢复、源码更新、嵌套 Apptainer、模型 GPU 前向与 readiness 尚未执行，S6 未完成 |

## 已核实的运行层次

1. **当前完整应用 ASGI：19 项通过。** `results/integrated-research-asgi-process-final-20261114/checks.json`（接入一次性渲染子进程后重新运行）。真实临时账号 Cookie、完整 middleware/router、源 CSV 登记、统计持久快照、数值复用复核、analysis_ref 导出 XLSX/DOCX、多 panel 项目、SVG 预览/下载相同字节、PNG/PDF 实际下载、跨账号拒绝。启动模型 warmup 明确禁用，`/ready` 返回 503；不是自然语言模型调度或浏览器/TCP 运行。
2. **六章页内指南：25 项真实浏览器检查通过。** `results/20261113T172405Z-guide-browser/checks.json`。研究任务/资料/数据/绘图/成果/账号六章；绘图是同一章五个连续子节；窄屏、Tab/Shift-Tab、Escape、焦点恢复和 URL 不变。使用已有隔离预览；不代表该预览加载了后来更新的全部 Python 后端。
3. **S5 浏览器交互：15 项通过，但任务端点是明确标识的模拟夹具。** `results/20261113T172239Z-task-ui-browser/endpoint-sources.json` 区分真实登录/会话/静态页面与 simulated_task_fixture 的 SSE/replay/update/approval/recovery。禁止把这些截图说成新后端/真实模型验收。
4. **历史图件编辑浏览器：44 项通过。** 见 `FIGURE_EDITOR_ACCEPTANCE.md` 及 `results/20261113T162747Z-editor-browser/`。该报告的“12 页指南/尚未实现多 panel”描述属于早先快照，已由当前六章指南和 S4 实现取代；历史记录不覆盖、不冒充本轮新后端浏览器验收。
5. **真实 Linux/容器/GPU：未完成最终验收。** 先前的 CUDA tensor/宿主探测不能代替最终服务中的 embedding/reranker 前向、GPU kernel/provider 和 worker 创建→修改→保存→重放。
6. **Microsoft Word 原生打开→编辑→另存→重开：环境阻塞，未通过。** `word_office_acceptance.py` 对合成 DOCX 的首次尝试在窗口句柄查询处失败，尚未打开文档；检查注册表后确认 `Word.Application` 实际绑定 `wps.exe`，不是 Microsoft `WINWORD.EXE`。仅凭 COM 注册与 win32com 可用而作出的“本机已安装 Word”判断已更正。加上原生应用检查后，`results/word-native-preflight-20261114/checks.json` 记录 `status=BLOCKED`、`native_word_available=false`，exit=2，未再次启动替代应用。原始 DOCX 哈希保持 `639cbe21a5067b7217657e59fa1e6f96de0dd88eab9c9cf309136f6b15393f07`。已有 Word 40 项回归及 ASGI 字节/表格/来源/精度检查通过，不能替代原生 Word 操作和首页/表格页/末页视觉验收；没有生成或声称已检查 Word 另存 DOCX/PDF。

全部研究输入为明确标识的合成工程材料。ASGI 产物中的故意重复是阳性夹具，不是对真实科研材料的结论。

## 方法和能力边界

- 主要绘图路径为安全 runner 内脚本 + nature-figure 工作流 + Matplotlib Artist 编辑；Seaborn 已接同一语义契约。没有把其他绘图库列为已集成。未复制 Tavotto 实现或视觉资产。
- nature-figure 参考固定 commit `cb233215d6d9dbb25956c27c19b86377c1e9353e`；许可和派生见 `01_src/src/prompts/skills/figure-export/NATURE_FIGURE_ATTRIBUTION.md`。profile/QA 是已测尺寸/字体/线宽/坐标等项目，不是 Nature 认证或科学论断核验。
- 科研结果保留全精度、来源摘要、行身份、设计、单位、排除与覆盖。Word/Excel/图件读取同版结果，不让模型重抄数值；不完整/不支持设计明确拒绝。
- 图像检测包含精确/近似跨图匹配、单图局部重复、轴向长边界/背景差异、灰度端点观察、声明元数据清单；数值含精确/重排序/偏移/比例/仿射/共享小数尾。每项带有界搜索、反例与人工复核，不证明动机或科研不端。
- 元数据缺失不视为可疑，声明字段不等于认证采集历史；GPS/序列号不收集。未经可信绑定的正文/OCR/p 值、任意接缝/非刚性修改不作穷尽检测承诺。
- Word 不提供在线编辑器；完整报告 PDF、CRDT、永久用户容器、任意代码平台、微服务/React 全面迁移、自动改原始数据和“造假概率排行榜”仍按原计划排除。

## 配额与清理

- 聊天在既有 FIFO 容量门禁内增加服务端 owner 身份；默认全局 10 活动/100 排队、每账号 2 活动/10 排队，保留取消清理与附件等待字节上限。
- 资料库默认每账号 10 GiB / 10,000 条，SQLite 写事务内检查，超额旧资料可读/幂等重试而不能增长。
- 图件 API 默认每账号 2 个实际操作、单 worker 共 4 个；超额 429，不无界排队。HTTP 观察者取消后，实际线程仍占名额，直至保留 lease 释放。
- 声明式生产渲染改为可信一次性子进程，默认 2 执行/6 等待、30 秒总时限、2 GiB 内存，清理等待最多额外 5 秒。JSON 输入 16 MiB，单格式 24 MiB、渲染字节 48 MiB、收集总量 64 MiB；stdout/stderr 不累积。Windows 挂起创建→绑定 JobObject→运行，实际超时/取消/错误输出测试已覆盖；POSIX 实现 RLIMIT 与自有进程组清理（包括 leader 已退出的情形），真实 Linux 行为仍待现场验证。该资源边界不替代不可信脚本的 Apptainer 隔离。
- 图件项目默认每账号 500 个，事务内计数；已有项目仍可编辑。只保留最近正式保存元数据与临时预览；旧字节按已有 pins/TTL/GC 处理，不宣称立刻全部删除。
- Word 默认每草稿 100 个版本（含初始快照），在 CAS/幂等检查后、pin/渲染前检查。无自动修剪，历史来源保护不变；到限仍能读取、发布与成功批次重试。
- 单 worker 的容量与准入内存状态不适用于不加共享协调的多 worker 部署。

## 发布与阻塞

只更新 8030 的 `citrusqa-next.service`，不触碰 8000，不用旧服务快照覆盖新服务数据，不重建或停止现有 SSH 隧道。

发布包来自当前 dirty 文件系统，排除生产状态、模型/embedding、密钥、缓存、结果和 `.git`。没有真实当前数据库清单与兼容性审阅时，manifest 为 `deployment_status=BLOCKED`、`blocked_apply=true`，配置切换/启动工具拒绝应用。包的 SHA 与文件清单仅证明本地源码完整性，不代表服务器发布成功。

自动审批已拒绝：

- `Production Reads`：读取当前 8030 的数据库结构/缓存元数据，尚无真实兼容性输入。
- `Remote Shell Writes`：远程 staging/更新，尚未执行源码发布。
- `Interfere With Workloads`：停止旧的本地预览以释放第五个服务器名额，新后端浏览器实例未启动。

这些拒绝没有通过其他工具、账号或进程绕过。实际备份、恢复演练、切换和 GPU/worker 现场验收仍待具备执行条件。未提交、推送、合并 main。未运行 Case A/B/C、FW20/40 或 240 题路由基准，未改 gold/scorer、历史结果、外部 harness 或专利材料。

## 最终整体门禁

统一命令：`HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -X utf8 engineering/product_evolution/run_checks.py`。

首次整体目录：`results/20261113T175248Z-9e2c1318/`。figures 425 通过/2 跳过，figure_api 142 通过/1 跳过，research 426 通过，engine 38 通过，word 40 通过，frontend 121 通过，release 22 通过。contracts 首轮 369 通过/1 跳过/1 失败；失败的测试替身 `slow_cleanup()` 不接受新 `owner_user_id` 参数，已改为验证真实 owner 并转发。修正后独立重跑目录 `results/20261113T175901Z-d76377cb/`，exit=0。

最终完整门禁目录：`results/20261113T182318Z-51207561/`。`summary.json` 记录八组 exit=0；`counts.json` 由实际 JUnit 与已完成日志统计。

| 组 | 通过 | 跳过 | 失败 |
|---|---:|---:|---:|
| figures | 444 | 2 | 0 |
| figure_api | 142 | 1 | 0 |
| research | 426 | 0 | 0 |
| contracts | 370 | 1 | 0 |
| engine（离线 GPU 策略/就绪测试） | 38 | 0 | 0 |
| word | 40 | 0 | 0 |
| frontend | 121 | 0 | 0 |
| release（合成备份/恢复/门禁） | 22 | 0 | 0 |
| 合计 | 1,603 | 4 | 0 |

跳过原因：两项 Windows 符号链接权限、一项实际 Linux SIF 路径/权限、一项既有无图例夹具。真实容器测试没有被 mock 通过替代。307 个 `01_src/src` Python 文件与 14 个前端 JS 脚本另通过纯语法检查，证据为 `results/source-syntax-final.json`、`results/javascript-syntax-final.json`。`git diff --check` 通过，只有既有 LF/CRLF 提示。此前失败日志全部保留。

## 本地源码包

发布 ID：`citrus-product-20261114-final-66150b3bdebe457f955823d6adf84cb6`。

包及清单位于 `results/releases/citrus-product-20261114-final-66150b3bdebe457f955823d6adf84cb6/`：`source.tar.gz`、`source.tar.gz.manifest.json`、`source.tar.gz.sha256.json`。实际 SHA256/文件数量以同时生成的回执为准，避免在被归档的本报告内嵌归档自身摘要。源包仍是 BLOCKED 的本地审阅材料，不能据此宣称服务器已更新。
