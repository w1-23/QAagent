# CitrusScholar v15 基线与调用链审计

审计日期：2026-11-03
范围：服务源码、运行入口、访问控制、检索、Agent 执行、数据审查与任务交付。论文题库、Gold 数据和评测脚本不在本轮范围。

## 1. 基线与环境

| 位置 | 审计结果 |
|---|---|
| GitHub `w1-23/QAagent` `main` | `def013ac21de593e55e16dc07ad9c2ee82877a4d`，tree `14266cebc9db5a033de4dd5adaabe72cd8ad628d` |
| GitHub 文件基线 | 隔离候选对 GitHub 跟踪树 209/209 个文件内容逐 blob 校验一致；本机没有 GitHub 提交对象，因此候选使用合成基线提交 `f6a07cee44789f9d240447b67d8d6613ae20fead`，内容相同但 Git ancestry 不同 |
| 用户原工作区 | `E:\codex_WORKSPACES\Citrus_QA_Agent` 的本地 `main` 原为 `7eae85c`，含未提交删除和本地资料；本轮未覆盖、清理或移动该工作区 |
| 生产 | `211.69.141.120:8000`，PID `494959`，单 Uvicorn worker，Python `3.12.15`，`/health` 返回 200；部署标记为 `def013ac21de593e55e16dc07ad9c2ee82877a4d` |
| Staging | `127.0.0.1:8011`（远程服务器本机），PID `459295`，单 Uvicorn worker，Python `3.12.15`，`/health` 返回 200；部署标记同生产 |
| 核心代码差异 | 审计开始时，生产与 staging 的 `general_agent_graph.py`、`deepseek_search_fetch.py` 相同；`api/main.py` 不同（生产 SHA-256 `3b8197a20d3fb4d26c75f36848a2183340983772a084a05e5064632bf6b726e0`，staging `064b7c6f2975ce3398cffcbaa6f5f1946f6e61faa75677f9426a3a49313be884`） |
| 语料索引指针 | 生产与 staging 的 `agent/data/active_index.json` SHA-256 均为 `319c3baf29211472358370a768e75ca80c11d5b8e3dfdf6a3136078e7073a415`；当前 generation `g20261028T165637Z_851926a9` 的注册文件 SHA-256 `da0367d6ff96b50bba78b3855d1190824ba2dfdd932303e767e32b6038a323ee` |
| 提示词集合 | 生产聚合指纹 `3e9a4a76…`，staging `5f8f7e45…`；两端提示词并非同一快照 |

生产环境抽样依赖：FastAPI 0.162.0、LangGraph 1.2.17、langchain-core 1.7.0、LanceDB 0.37.1、Qdrant client 1.20.0、onnxruntime-gpu 1.21.1、fastembed 0.8.0、pandas 3.1.0、openpyxl 3.1.5。依赖清单不是锁文件，完整复现仍需统一 requirements 与运行环境。

**基线结论：**健康检查通过不等于部署版本完全一致。生产与 staging 的 API 文件及提示词指纹已有差异；原工作区也不是 GitHub 的干净快照。v15 实施在独立本机候选完成，并按下面的发布门禁先更新 staging；原用户工作区未覆盖。

## 2. 实际调用链

### 语料检索与原文

`/api/v2/chat` → `src/graph/general_agent_graph.py` 的 General Agent / 中央工具注册 → `src/tools/deepseek_search_fetch.py::local_hybrid_search` → `src/retrieval/runtime.py` → `MultiBatchRetriever` 的 BM25、向量检索、RRF 与重排 → `src/core/corpus_source.py` 解析来源 → `/api/v2/corpus/source` 返回原文预览。

语料注册和访问级别由 `src/retrieval/corpus_registry.py` 管理，索引 generation 由 `agent/data/active_index.json` 指向。`cqa_methods` 应声明为公开语料。原代码的来源预览有访问判断，但 RAG 候选检索本身没有把 corpus access level 传入 BM25、向量查询和结果缓存，形成“来源端拦、检索端仍可能召回”的策略断层。

### 私有文件与生成产物

上传与会话文件由 `src/session/files.py` 和 `/api/v2/session/{id}/files/*` 维护；文件检索工具读取当前用户上下文。科研产物由 `src/research/tools.py` 写入研究产物存储，再由 session artifact 下载/预览路由提供。原有附件和数据库隔离保留；审计特别补上生成产物下载路由的会话归属校验。

### Agent 策略与执行

检索意图由 `src/graph/retrieval_router.py` 生成，工具可用性由权限、附件与 `src/graph/tool_policy.py` 共同决定；执行由 General Agent 的多轮工具循环和中央注册表完成。来源工具回执进入工具状态/来源台账，最终 SSE 对外契约保持不变。

原路由会把普通科研关键词建议升级为必需工具组，导致 advisory 建议影响“必须检索”的完成门槛。本轮将 `advisory` 与显式要求的 `required` 分开，并保留 `CITRUS_RETRIEVAL_ROUTER_MODE=enforce` 回退开关。

### 数据审查与任务交付

`audit_uploaded_data` 位于 `src/research/tools.py`；表格发现通过 `src/research/data_audit_rules.py` 规则清单产生，再由 `src/core/data_audit_xlsx.py::annotate_workbook_multi` 输出独立的标注 XLSX。原始上传不回写。产物包括问题单元格着色、批注、问题清单与概览；审计新增来源哈希与规则说明，并确保审查附表不覆盖同名用户工作表。

PaperConan 仓库内适配器未接入默认审查入口。本轮删除其未使用的 vendored 运行代码和适配器，保留方法来源说明；默认运行产物继续为标注 Excel。

复杂任务状态由 `src/core/task_result.py` 根据实际工具状态、缺失要求及通过校验的产物数聚合；不从 Agent 自然语言中猜测任务成功。

## 3. 数据与兼容边界

- 本轮不改数据库 schema，不删表，不迁移或清理任何线上数据。
- 保留账号、会话消息、附件、科研产物、会话证据、工具记录、来源台账、反馈与索引。
- 本轮未触碰生产/staging 的数据库、密钥、缓存、语料文件、生成文件、备份或用户会话。
- 公共语料对已登录用户开放；管理员专用语料仍按 registry 标记限制。私有上传、项目记录与生成产物仍按 owner 限制；管理员操作单独要求 admin。
- 用户明确本轮不编写论文实验包、题库、Gold、专家评分和统计脚本。

## 4. 本轮代码变更审查项

1. 新增统一资源访问分类，并将 session、文件、产物、语料预览与管理员操作接入统一决策。
2. 将 corpus access level 传播到 BM25 候选、LanceDB/Qdrant 向量过滤与检索缓存键，避免未授权语料被取回或跨授权范围复用缓存。
3. 让普通检索建议保持 advisory；用户明确要求则有必需工具组；明确禁止检索则过滤对应工具。`enforce` 保留旧门槛。
4. 新增内部 `TaskResult`，根据执行和验证记录汇总 complete/partial/failed/cancelled。
5. 数据审查使用带适用范围、触发条件、样本要求、定位方式和误报风险的规则描述，输出多工作表标注 XLSX；原输入 SHA 和生成产物 SHA 可追踪。
6. 对 `.xls`、`.xlsb`、CSV/TSV 及提取的多表格数据保留各工作表转换结果，避免只审第一张表。
7. README、用户指南、运维说明、数据审查说明和 PaperConan 归属说明已同步更新。

## 5. 验证记录

- 本轮新增契约测试：9 项通过，包括资源分类、公开方法语料、检索三态、兼容 enforce、任务状态、XLSX 原始工作表保护、多表转换、授权范围缓存隔离与向量查询 corpus_id 过滤。
- `python -m compileall -q 01_源码_src/src` 通过。
- `git diff --check` 通过；仅有 Git 对 LF/CRLF 的提醒。
- 本机原测试目录含旧分支测试。复制到候选目录后，测试收集有 5 个模块因 GitHub `def013a` 基线不存在其旧模块（如 `src.core.agent_loop`、`src.tools.search`、`src.core.write_pipeline`）而失败；可运行的挑选项为 13 passed。旧套件不能作为此 GitHub 基线的直接全量验收。
- 候选隔离目录完整测试本身为 9 passed；生产和 staging 目前只做了只读健康检查与指纹审计，未部署新代码。

## 6. 发布门禁

部署前必须先在 staging 完成新版本启动、登录、公开语料检索与来源阅读、私有附件隔离、标注 Excel 下载与 SHA 校验、显式禁止/要求检索、工具失败状态检查。通过后再备份生产服务源码并同步允许的服务文件，确认不覆盖生产配置、会话、数据库、语料、密钥、缓存或备份。

部署记录需要使用实际发布树 SHA 与文件哈希；不能只复制 `DEPLOYED_COMMIT` 标记来宣称版本一致。GitHub 推送如果不可用可以保留本机提交和服务器发布记录，由用户后续推送。

### Staging 首轮发布记录

- 候选 SHA：`bf5809b1953d88ccee97f073b47d098088b07b8c`；归档 SHA-256：`5599ba387bb54dba993818d07c69d13ef7baf87256cafd2fa6456342dc952b95`；归档共 186 个条目，只含 README 与服务源码。
- 更新前 staging 源码已备份至 `/home/WZQ/staging_citrus/backups/v15-pre-20261103/source.tar.gz`，SHA-256 `0aa1907e2ddf98b2694ad309c28954fde86d811dda41420d4cfcde6fec6aa198`；旧源码树另保存在同目录。生产尚未更改。
- staging 已以单 worker 在 `127.0.0.1:8011` 启动，`/health` 与 `/` 为 200；未登录访问 `/api/v2/auth/me`、`/api/v2/auth/preferences` 均为预期 401。
- 首次预热日志提示缺少 CUDA 12/cuDNN 9 运行库，并显示 CPU provider 回退；进程仍正常启动。模型缓存文件大小警告需在后续检索验收时确认是否影响索引读取，本轮未删除或重建缓存。
- 当前限制：没有使用有效账号登录，也未在 staging 执行真实本地检索、跨账号附件隔离或 Excel 端到端下载。因此这不是完整功能验收记录；生产发布前仍需补齐这些项目。
