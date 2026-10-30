# 发布门禁报告（Release Gate）· 2026-10-30

> 用途：在把本地改动推到生产 8000 之前，给出可核对的基线、变更清单、兼容性检查、
> 真实浏览器验收证据、缺口与回滚办法。**本文件不代表已部署**：生产仍为 `ff58b1f`。

## 1. 门禁结论

| 门禁项 | 状态 | 依据 |
| --- | --- | --- |
| 生产未被改动 | 通过 | `01_源码_src/src/api/main.py` sha256 = `5f5f9e3f25ecc2e4b72fc11fa4376840f358128f0232c57ac390e3a8c2e9ab88`（与 S0 基线一致）；pid 3495578；`/` 200 |
| 本地可启动 | 通过 | 8010 HTTP 200，真实模型问答可用 |
| 自动化测试 | 通过 | `pytest 01_源码_src/src` = **307 passed**（基线 267） |
| 数据库兼容 | 通过（见 §4） | 仅新增 `messages.execution_json` 列，ALTER 幂等 |
| 配置/密钥未被覆盖 | 通过（见 §5） | `.env` 未入仓；联网 key 与 `state/web_search_config.json` 为运行期文件 |
| 真实浏览器验收 | 部分通过（见 §6） | PDF 真渲染、CSV 表格、文件跳转、停止任务均有截图 |
| 未验证项 | 存在（见 §7） | xlsx / HTML 报告库内无样本；PDF 仅 1 页探针 |
| 用户授权 | **未获得** | 部署与 push 需用户明确同意 |

## 2. 基线快照

| 环境 | 位置 | 版本 | 端口 | 备注 |
| --- | --- | --- | --- | --- |
| 本地开发 | `E:\codex_WORKSPACES\Citrus_QA_Agent` | HEAD `c9de207`，领先 `ff58b1f` **34** 提交 | 8010 | 新代码 + 新语料 |
| 生产 | `/home/WZQ/CitrusQA` | `ff58b1f`（按文件字节核对，非 git 仓库） | 8000 | pid 3495578，未动 |
| 隔离暂存 | `/home/WZQ/staging_citrus` | **落后于本地**（见下方说明） | 8011（本机转发口 8020） | 独立 venv 与语料，用于发布前演练 |
| 隧道口 | 本机 8000 | 指向生产 | 8000 | **测试必须避开** |

本机关键文件 sha256：

```
639a353fe7ded530b54b4c9208f2359840b2e825c8a32128ddedfb80f0cdfa26  01_源码_src/src/api/main.py
4f9faa3b9b5de79ed10fc628bab62b55b79320e4d1a32b82a3501ebd7c30691d  01_源码_src/src/graph/general_agent_graph.py
3f3baf2871f89d4d0e507be8dc134667cdc4faf60b83f83a32074303d5e6b839  01_源码_src/web/assets/app.js
35d0c5c50df2f41dcfaccd5515f7b6c32d853b5bb6c69a3eecd8ea027f4b64a1  01_源码_src/web/index.html
```

暂存实例核对（2026-10-30）：

```
639a353fe7ded530b54b4c9208f2359840b2e825c8a32128ddedfb80f0cdfa26  01_源码_src/src/api/main.py   （与本地一致）
b33f13be4bde350d33b55bc5008d552f8760f34adb11e16b14e7efa26c3b090b  web/assets/app.js             （与本地不一致）
MISSING                                                            web/assets/turn_rail.js
```

结论：暂存实例仍是 Round 4 的副本，`main.py` 相同但前端资产落后，**发布演练前必须重新同步源码**。
它的监听端口是 8011（本机 8020 只是 SSH 转发口）。

语料：本机 `agent/data` = 5 批 / 126,943 块（`corpus_generations`、`cqa_literature`、`cqa_methods`、
`cqa_ucr_varieties`、`cqa_translated`、`cqa_deng_book`）；旧语料归档 `data_legacy_20261030`，未删除。

## 3. 变更清单（`ff58b1f..c9de207`）

102 个文件，+13429 / −379 行。按主题：

- **执行轨迹与状态**：`turn_status.py`、`progress_bus.py`、`messages.execution_json`、`notices.py`
- **取消生命周期**：`src/core/cancel.py`、`jobs.py` 状态机、断线 detach、产物原子写
- **公开进度**：`src/core/public_progress.py`（独立 `assistant_progress` 通道）
- **历史与导航**：`/messages` 游标分页、`turn_request_id` 暴露、`jumpToMessage`、`turn_rail.js`
- **预览诚实化**：`_canonical_mime`、PDF 魔数校验、Office 回退
- **工具架构**：`tool_policy.py`、`tool_availability.py`、`deliverable_check.py`
- **前端重做**：六页指南、上下文概览、右侧导航条
- **审计与文档**：`experiments/trace/reports/*.md`、`STARTUP.md`、启动/停止脚本

## 4. 数据库兼容性

- 新增列：`messages.execution_json`（`ALTER TABLE ... ADD COLUMN`，重复执行安全）。
- 未删除、未重命名任何列；`jobs` 表新增状态值 `cancel_requested`/`cancelled`，旧行不受影响。
- 5 个分库（accounts / sessions / research_artifacts / jobs / admin_audit）均未做破坏性迁移。
- 迁移幂等由 `pytest` 中迁移用例覆盖；本轮未在生产库上执行任何 DDL。

## 5. 配置与密钥

- `.env`、`state/web_search_config.json`、`state/deepseek_web_api_key` 均为运行期文件，**不在仓库内**。
- 本轮改动只**读取**这些配置，没有写入或删除；`config.py` 仍只从 `state/web_search_config.json`
  读取联网配置（`.env` 里的 `WEB_SEARCH_PROVIDER` 是死配置，未改）。
- 8010 联网 `reason=auth` 已修复：装入可用的 `deepseek_web_api_key` 并写入 `web_search_config.json`，
  三层验证（HTTP 200 / `configured:true` / 工具返回 3 条真实结果）。

## 6. 真实浏览器验收（本轮新增证据）

手段：本机无 dsh 浏览器 provider，改用 **Chrome + CDP 直连 8010**；PDF 需内置阅读器，
因此 PDF 一项用 **headful Chrome**（真实渲染），其余用 headless。截图在 `_scratch/`。

| 项目 | 结果 | 证据 |
| --- | --- | --- |
| PDF 预览真渲染 | 通过：显示 1/1 页、缩略图、缩放控件、正文 “CitrusQA PDF probe” | `_scratch/pdf_headful.png` |
| 表格预览 | 通过：CSV 渲染为 2 行 4 列表格 | `_scratch/table_headful.png` |
| 文件 → 原轮次跳转 | 通过：`jumpToFileTurn` 返回 true，切换会话并高亮 1 个锚点 | `_scratch/jump_headful.png` |
| 停止任务 | 通过（修复后）：气泡保留执行轨迹与“已停止”，按钮回到可发送 | `_scratch/stop_ok.png` |
| 六页指南 | 通过：桌面/手机/浅色主题 | `_scratch/guide_live_desktop2.png`、`guide_live_mobile.png`、`guide_live_light.png` |
| 上下文概览 | 通过：真实用量/估算区分、六类细分可展开 | `_scratch/ctx_drawer3.png`、`ctx_drawer_narrow.png` |
| 右侧导航条 | 通过：刻度/悬停预览/当前位高亮/手机隐藏 | `_scratch/rail_tip2.png`、`rail_mobile.png` |

## 7. 本轮新发现并已修复的两个缺陷

1. **刷新会话只显示第一条消息**（严重）
   - 现象：含工具调用的会话重开后只渲染 1 个气泡。
   - 根因：`toolActivityLabel` / `toolStatusText` 声明在 `sendMsg()` 内，`renderHistoryMessages()`
     调用时抛 `ReferenceError`，循环中断。
   - 证据：修复前 `1/35` 气泡、`1` 个锚点；修复后 `35/35`。
   - 提交：`781d838`；回归测试 `src/api/test_web_script_scope.py`。

2. **停止任务后卡在“运行中”且气泡为空**
   - 现象：点停止后停止按钮常亮、发送按钮不回来；气泡是空框。
   - 根因：停止回调里新增的回填逻辑引用了未定义变量，抛错导致 `setSendDisabled(false)` 未执行。
   - 修法：改用文件作用域的 `fetchHistoryPage`，把回填放到按钮恢复之后并整体 try/catch；
     服务端已持久化的收尾文本回填进空气泡。
   - 提交：`c9de207`；回归测试 `src/api/test_stop_turn_contract.py`。

## 8. 未验证 / 缺口（必须如实标注）

- **xlsx 与 HTML 报告预览**：本机文件库内无 xlsx / .html 样本（现有 9 个文件：5 table、3 pdf、1 text），
  未做真实渲染验收；`Office` 分支的“不可渲染时回退”只有代码与单测覆盖。
- **PDF 仅 1 页探针文件**：多页滚动、缩放交互未逐项验收。
- **动态公开工作说明对下一轮语义的影响**：未做对照实验（只验证了事件通道不污染最终文本）。
- **上下文概览的“实际发送给模型的消息”**：面板展示的是按角色重建的会话细分，**不是**请求级
  实际发送快照；这是已知缺口（D-A1），未记录真实 payload。
- **视觉验收为 Chrome CDP 截图**，非 dsh 内置浏览器；触屏手势与真实移动端未测。

## 9. 回滚办法

1. 生产代码：`rsync -a --delete` 回 `ff58b1f` 备份目录（`/home/WZQ/CitrusQA_backup_*`），重启 uvicorn。
2. 数据库：本轮无破坏性 DDL；如需回退，删除 `messages.execution_json` 列即可（旧代码忽略该列）。
3. 语料：旧语料 `data_legacy_20261030` 与服务器 `agent_data_legacy_*` 均保留，可切回。
4. 配置：联网 key 与 `web_search_config.json` 为独立文件，回退不影响账号数据。

## 10. 待用户决策

- D-A1 上下文概览是否增加“请求级实际发送快照”。
- D-A2 账号记忆是否支持单条删除。
- D-A3 导航条超长会话（>80 轮）是否改为全量 + 可滚动。
- D-A4 是否批准部署到生产 8000 / push 到远端。
