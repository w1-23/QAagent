# S0 基线核对与 S1 取消生命周期最小改造清单

生成时间：2026-10-30 11:5x（Asia/Shanghai）
本文件只做两件事：**（1）S0 基线核对；（2）S1 的函数级最小修改清单与测试设计。**
本轮**没有**改动任何生产代码，没有重新部署，没有 push。所有行号均以 **本地 HEAD `ed7c89d`** 为准；生产侧行号见 §2.2 的差异说明。

---

## 0. 一句话结论

1. **生产跑的就是提交 `ff58b1f` 的字节**：16 个关键文件在“服务器 / `ff58b1f` / `ed7c89d`”三列哈希比对中，服务器与 `ff58b1f` **逐字节一致**，与本地的差异全部来自 `ff58b1f` 之后的 7 个提交。**不存在"服务器被手工改过、无法用 git 解释"的文件。**
2. 但 **`DEPLOYED_COMMIT` 写的是 `9955c79`（9 月 28 日，47 天前）**，与实际运行的 `ff58b1f` 不一致 —— 这个标记文件已经不可信，本轮已记录在案（不改动它，因为改它属于"伪造部署记录"）。
3. **生产进程不在 systemd 管理下**（`citrusqa.service` 为 `enabled / inactive`，在跑的 PID 3495578 属于 SSH 会话 scope `session-15667.scope`），因此现有部署脚本里的 `systemctl --user stop/start` 对本进程**无效**。这是 S1 部署前必须先解决的唯一硬阻塞。
4. 取消相关的现状**不是"参数没调好"，而是设计缺口**：断线即取消、取消端点按会话扫表、无协作式取消令牌、写产物不判取消、迟到结果直接落库、前端把气泡整条删掉。这些都在 §4 给出可复现的行号证据。
5. **S1 的最小面**：新增 1 个文件（`src/core/cancel.py`）+ 修改 6 个文件（`jobs.py` / `main.py` / `registry.py` / `research/tools.py` / `app.js` / `app.css`），预计 +330 / −90 行；测试新增 6 个文件、约 52 个用例。**所有写产物都经过 `research/tools.py:304 _save_artifact` 这一个收口点**，所以"原子提交 + 取消闸门"只需要改一处函数体。
6. **一个必须纠正的认知**（影响回滚与测试隔离）：运行期状态**不是单库**，而是 `agent/state/` 下 **5 个 SQLite 库 + 2 个目录**（会话/消息/任务、账号/登录、会话文件、管理审计、研究产物各自独立）。冷备与回滚必须整组处理，且 `accounts.sqlite3`、`session_files.sqlite3` 各带非空 `-wal`，只能"停进程后复制"。详见 §2.6 / §2.9。

---

## 1. S0 交付物清单

| 交付物 | 位置 | 状态 |
|---|---|---|
| 生产源码回滚包 | 服务器 `backups/source-before-ff58b1f-20261030-114701.tar.gz`（5.3 MB） | ✅ 已生成并校验 |
| 回滚包校验和与运行态记录 | 服务器 `backups/BACKUP_INDEX.txt`（sha256、PID、启动时间、命令、DEPLOYED_COMMIT） | ✅ 已追加 |
| 基线核对证据（本文 §2） | 本文件 | ✅ |
| 数据库与迁移状态（本文 §2.6） | 本文件 | ✅ |
| 测试基线（本文 §2.8） | 本地 179 passed | ✅ |
| 回滚方案（命令级，本文 §2.9） | 本文件 | ✅ |
| 测试副本实例配方（本文 §2.10） | 本文件（**未启动**，等 S1 代码就绪再起） | 📝 配方就绪 |

回滚包 sha256：

```
66eb9983c9a8b68be9a956e9a5ef6775f49b1e6a46138d9c4b9164657e3168aa  backups/source-before-ff58b1f-20261030-114701.tar.gz
```

> 该包只含源码（排除了 `.hf_cache` / `.cache` / `__pycache__` / `*.pyc`），因此 5.3 MB 而不是 44 MB。

---

## 2. 生产基线（全部只读实测）

### 2.1 进程与端口

| 项 | 值 |
|---|---|
| 监听 | `127.0.0.1:8000`（`curl /` = 200，响应 1,525 B） |
| PID | 3495578，启动于 `Fri Oct 30 09:22:12 2026` |
| 命令 | `.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 --workers 1` |
| 工作目录 | `/home/WZQ/CitrusQA/01_源码_src` |
| cgroup | `session-15667.scope`（**SSH 会话**，不是 systemd 服务） |
| systemd 单元 | `enabled / inactive`；`ExecStart=/home/WZQ/CitrusQA/01_源码_src/.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --no-access-log`（与实跑命令不同） |

### 2.2 源码识别：三列哈希比对（LF 归一，取 sha256 前 16）

| 文件 | 服务器（实跑） | `ff58b1f` | 本地 `ed7c89d` | 判定 |
|---|---|---|---|---|
| `src/api/main.py` | `5f5f9e3f25ecc2e4` | `5f5f9e3f25ecc2e4` | `fe9513957c4a19f0` | 服务器=ff58b1f |
| `src/graph/general_agent_graph.py` | `361c6685ee35e6a9` | `361c6685ee35e6a9` | `1658b41328330c59` | 服务器=ff58b1f |
| `src/graph/tool_availability.py` | **不存在** | **不存在** | `e6820d080f378061` | Phase 1/2A 未部署 |
| `src/graph/tool_policy.py` | `b671d826b1dde4ec` | `b671d826b1dde4ec` | `b671d826b1dde4ec` | 三方一致 |
| `src/core/progress_bus.py` | `4963b31b8388029d` | `4963b31b8388029d` | `4963b31b8388029d` | 三方一致 |
| `src/core/turn_status.py` | `2c99bac92dad9e30` | `2c99bac92dad9e30` | `2c99bac92dad9e30` | 三方一致 |
| `src/core/tracing.py` | `836cb400543230ac` | `836cb400543230ac` | `b429181a9f965b69` | 服务器=ff58b1f |
| `src/session/files.py` | `591a9a898109c015` | `591a9a898109c015` | `b751a898554d706c` | 服务器=ff58b1f |
| `src/session/manager.py` | `0c7e92866077ec0d` | `0c7e92866077ec0d` | `0c7e92866077ec0d` | 三方一致 |
| `src/tools/__init__.py` | `28ab6b2a23098f62` | `28ab6b2a23098f62` | `77ada941f9f30322` | 服务器=ff58b1f |
| `src/tools/registry.py` | `c8df9ba4754a85eb` | `c8df9ba4754a85eb` | `4119c6b017098286` | 服务器=ff58b1f |
| `src/research/tools.py` | `1310fb59908d3826` | `1310fb59908d3826` | `2256589186fd4355` | 服务器=ff58b1f |
| `web/assets/app.js` | `acad1c1154fd7d74` | `acad1c1154fd7d74` | `acad1c1154fd7d74` | 三方一致 |
| `web/assets/app.css` | `0036c0872b9150c6` | `0036c0872b9150c6` | `0036c0872b9150c6` | 三方一致 |
| `web/index.html` | `50718149b3290528` | `50718149b3290528` | `50718149b3290528` | 三方一致 |
| `src/graph/test_general_agent_routing.py` | 存在 | 存在 | 存在 | 三方一致（仅测试） |

**结论**：生产 = `ff58b1f` 源码树，可以精确回滚到这个提交的字节；S1 的实施基线必须从 `ff58b1f` 的现实出发，而不是从 `ed7c89d`。

### 2.3 `ff58b1f` 之后未部署的 7 个提交

| 提交 | 主题 | 触及的生产文件 |
|---|---|---|
| `2c83d76` | Phase 1 工具绑定到真实环境前置条件 | `api/main.py`、`core/tracing.py`、`graph/tool_availability.py`(新)、`research/tools.py`、`session/files.py`、`tools/__init__.py`、`tools/registry.py` |
| `f51afbd` | 修两个过期测试 + 停止清理计数撒谎 | `api/main.py`、`session/files.py` |
| `46849c6` | 轮次文件引用只在文件存在时存活 | `session/files.py` |
| `80bab4b` | Phase 2A 工具可见性按环境决定（去掉关键词） | `graph/general_agent_graph.py`、`graph/tool_availability.py` |
| `61a8f5b` | 五份专项审计文档 | 仅 `experiments/trace/reports/` |
| `ee23a42` | 合并审计 + 公开文本探针与断言 | 仅报告 + `core/test_progress_payload_safety.py` |
| `ed7c89d` | 审计第二编（前端/预览/生命周期） | 仅报告 |

`git diff --shortstat ff58b1f..HEAD` = **23 文件 / +2425 / −132**（含报告与测试）。

### 2.4 部署机制与三个风险（S1 部署前必须处理）

| 风险 | 证据 | 影响 |
|---|---|---|
| **R-1 进程不受 systemd 管理** | `systemctl --user is-active citrusqa.service` = inactive；实跑 PID 在 `session-15667.scope` | 部署脚本的 `systemctl --user stop/start` 停不掉、也起不来当前进程 → 部署后**仍在跑旧代码**，且没有任何报错 |
| **R-2 `DEPLOYED_COMMIT` 过期** | 文件为 `9955c79`（47 天前），实际运行 `ff58b1f` | 依靠该文件判断线上版本会误判 |
| **R-3 两套 `run_server.sh`** | `04_脚本_scripts/run_server.sh`（systemd 用）与 `01_源码_src/run_server.sh`（手工用），环境变量不同 | 手工重启与 systemd 启动行为不一致 |

**S1 前的处置建议（需你拍板，见 §10 D3）**：二选一 ——
（a）**纳管**：`kill` 掉 session scope 里的手工进程，`systemctl --user restart citrusqa.service`，此后部署/回滚走 systemd；
（b）**保持手工**：新增 `04_脚本_scripts/deploy.sh`，用 pidfile 精确 kill + 同一命令重启 + 健康检查，systemd 单元继续 disable 并在文件里写明"不要启用"。

本文件默认按（a）走，但不在本轮执行。

### 2.5 数据与配置隔离面（部署时绝不能被覆盖）

| 路径 | 大小 | 说明 |
|---|---|---|
| `agent/data/` | 1.5 GB | 当前语料 3 批 / 126,943 块，**不能覆盖** |
| `agent/state/` | 2.3 MB + 分库 | 见 §2.6（**不是一个库，是 5 个文件 + 2 个目录**） |
| `agent/.hf_cache/` | 4.4 GB | 嵌入模型缓存 |
| `02_配置_config/`（`config.yaml` 等） | — | 运行配置 |
| `agent/state/*.json`、`deepseek_web_api_key`、`agent/.env` | — | **API key 与模型选择落点**（`deepseek_web_api_key` 35 B、`provider_api_keys.json` 143 B、`model_config.json` 191 B、`configured_model_providers.json` 22 B） |
| `node_modules`、`.venv` | — | 部署包一律不含 |

S1 的部署清单只允许写 `01_源码_src/`。**部署脚本必须显式跳过 `agent/` 与 `02_配置_config/`**，否则会连带覆盖 key 文件与语料（历史上"改一次就丢 key"就是这个方向的风险）。

### 2.6 数据库与迁移状态（`agent/state/`）

**重要修正**：运行期状态不是单一 `sessions.db`，而是 **5 个库 + 2 个目录**（按用途分库；`PROJECT_ROOT = <repo>/agent`，由 `src/config.py:19` 从 `__file__` 推导，`_REPO = parents[2]`）。

| 文件 | 大小 | 表（行数） |
|---|---|---|
| `sessions.db` | 2.3 MB | `sessions` 44 · `messages` 410 · `task_jobs` 86 · `session_evidence` 28 · `feedback` 1 · `memory_store` 2 · `account_memory_migrations` 2 · 其余 6 张记忆类表为空 |
| `accounts.sqlite3` | 90 KB | `users` 2 · `browser_sessions` 22 · `auth_events` 104 · `daily_usage` 2 · `workspace_files` 0 |
| `session_files.sqlite3` | 53 KB | `session_files` 12 · `session_file_turn_refs` 6 · `session_activity` 6 |
| `admin_audit.sqlite3` | 180 KB | `request_audit` 86 |
| `research_artifacts.sqlite3` | 24 KB | `artifacts` 9 · `research_notes` 0 |
| `session_files/`（目录） | — | 上传件实体 |
| `artifacts/`（目录） | — | 生成件实体（与 `artifacts` 表一一对应） |
| `sessions.sqlite3` | **0 B** | 空的遗留文件，无人读写（低风险清理项，本轮不动） |

与 S1 直接相关的只有 `sessions.db / task_jobs`：

| 项 | 值 |
|---|---|
| 现有列 | `job_id, session_id, request_id, job_type, status, current_step, progress_summary, result_path, error, created_at, updated_at, finished_at` |
| 缺列 | **无任何取消/断线时间戳列** → S1 需幂等迁移（`cancel_requested_at` / `detached_at` / `stop_reason`） |
| `messages.execution_json` | ✅ 已存在（前序修复已部署，历史行能还原 status/summary/elapsed） |
| 终态分布 | 86 个 job 中 `completed` 为主；`cancelled` 存在的行即可作为 S1 迁移的回归样本 |

### 2.7 语料状态

`agent/data/` = 3 批：`citrus_corpus_main`(112,890) / `citrus_corpus_zh2en`(13,137) / `special`(467)，合计 **126,943 块 / 4,906 篇**，另有 **62 个** `lancedb` 表。索引与检索契约见 `experiments/reembed/04_verify_corpus.py`。**S1 不触及语料。**

### 2.8 测试基线

| 项 | 值 |
|---|---|
| 本地全量 | `python -m pytest -q src` → **179 passed**（14.71 s，0 failed） |
| 服务器 | `.venv` **无 pytest**（`ModuleNotFoundError`）→ 服务器上无法直接跑测试 |
| 既有测试文件 | 27 个 `src/**/test_*.py` |

### 2.9 回滚方案（命令级）

```bash
# 1) 停当前进程（无论走到哪条路径都执行）
pkill -TERM -f "uvicorn src.api.main:app" ; sleep 3

# 2) 恢复源码（删除现有树再解包，避免残留新文件）
cd /home/WZQ/CitrusQA
mv 01_源码_src 01_源码_src.broken.$(date +%s)
tar -xzf backups/source-before-ff58b1f-20261030-114701.tar.gz

# 3) 校验完整性（与备份时的 sha256 树对比）
cd 01_源码_src && git --no-pager status 2>/dev/null || true   # 生产无 .git，改用哈希抽查
sha256sum src/api/main.py src/graph/general_agent_graph.py | cut -c1-16
# 期望：5f5f9e3f25ecc2e4 / 361c6685ee35e6a9

# 4) 重启并健康检查
nohup bash 04_脚本_scripts/run_server.sh > agent/logs/server_$(date +%Y%m%d_%H%M%S).log 2>&1 &
sleep 17 ; curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/   # 期望 200

# 5) 数据库：S1 的迁移只做 ADD COLUMN（不删不改列），旧代码读新库安全；
#    如需彻底回退，用下面的冷备还原（注意 WAL：必须停进程后再拷，或用 sqlite3 的 .backup）
mkdir -p agent/state_backup_pre_s1
for f in sessions.db accounts.sqlite3 session_files.sqlite3 admin_audit.sqlite3 research_artifacts.sqlite3; do
  cp -a "agent/state/$f" "agent/state_backup_pre_s1/$f"
done
```

冷备细节（S1 开工第一步执行，且**必须在停进程后**做，否则 WAL 未合并）：

```bash
cd /home/WZQ/CitrusQA
pkill -TERM -f "uvicorn src.api.main:app" ; sleep 3
STAMP=$(date +%Y%m%d-%H%M%S)
mkdir -p agent/state_backup_pre_s1-$STAMP
for f in sessions.db accounts.sqlite3 session_files.sqlite3 admin_audit.sqlite3 research_artifacts.sqlite3; do
  cp -a "agent/state/$f" "agent/state_backup_pre_s1-$STAMP/$f"
done
sha256sum agent/state_backup_pre_s1-$STAMP/* | tee agent/state_backup_pre_s1-$STAMP/SHA256SUMS
# 还原：停进程 → cp 回去 → 删除对应 -wal/-shm → 启动
```

> 现网 `accounts.sqlite3` / `session_files.sqlite3` 各带一个非空 `-wal`（12 KB / 78 KB），复制时必须连 `-wal`/`-shm` 一起按"停止后复制"处理，不能只拷主文件。

### 2.10 测试副本实例配方（隔离，不复用生产数据）

```
~/CitrusQA-staging/
├── 01_源码_src/                 ← 待测代码（cp -a，不含 .hf_cache）
├── 02_配置_config/              ← cp -a（同一份配置，端口用环境变量覆盖）
├── agent/
│   ├── state/                   ← cp -a 生产 state（≈2.7 MB：5 个库 + artifacts/ + session_files/，会话/文件/任务/审计都有真实数据）
│   ├── data -> /home/WZQ/CitrusQA/agent/data        （只读符号链接，避免 1.5 GB 拷贝）
│   └── .hf_cache -> /home/WZQ/CitrusQA/agent/.hf_cache （只读符号链接）
└── logs/
```

启动：`cd ~/CitrusQA-staging/01_源码_src && HF_HUB_OFFLINE=1 NO_PROXY='*' /home/WZQ/CitrusQA/.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8010 --workers 1`

> 注意：`PROJECT_ROOT` 由 `src/config.py:19` 的 `Path(__file__).parents[2] / "agent"` 推导 —— 目录层级必须保持 `<root>/01_源码_src/src/...`，否则运行期数据根会漂到一个空目录（`config.py` 的 `_assert_managed_tree` 会 fail-fast，这正是"改一次就丢 key"那类事故的防线）。
> venv 复用生产的 `/home/WZQ/CitrusQA/.venv`（只读）；端口 8010 = 天然隔离。

---

## 3. S1 的目标与非目标

**目标（只做这一件事）**：让"用户点停止"成为**真实、可核查、可解释**的状态迁移 —— 停止指令能到达正在执行的工具、能阻止迟到结果落库、能在刷新后仍看到"已停止"而不是消失的气泡，并且**客户端断线不等于用户取消**。

**非目标（本轮明确不做）**：
- 不做 S4 跳转、S5 分页指南、PDF 预览修复（已审计，见 `AUDIT_REPORT_20261030.md` 第二编）；
- 不引入任务队列、优先级、暂停/恢复；
- 不重建 SSE、不改 17 个工具的函数签名、不改 `_scoped_toolset`；
- 不做多 Agent。

---

## 4. 现状事实（逐跳证据，行号 @ `ed7c89d`）

### 4.1 取消链路全图

```
用户点"停止" (app.js:1142 stopCurrentTask)
  ├─ activeTurnCancel() → removeInterruptedReply (app.js:1871)
  │     ├─ requestAbortController.abort()          ← 断 SSE
  │     ├─ botMessage.remove()                     ← 气泡整条删除（用户可见"消失"）
  │     └─ setSendDisabled(false)                  ← 立刻解锁发送
  └─ POST /api/v2/chat/cancel {session_id}         ← 会话级，不是 job 级
        └─ cancel_chat (main.py:1561)
              ├─ list_for_session(limit=20) 逐个看 status=="running"
              ├─ 有 task → t.cancel() + 立刻置 status="cancelled"
              └─ 无 task → 也直接置 status="cancelled"   ← 伪称已终止

服务端 SSE 生成器 (main.py:1504-1519)
  ├─ 客户端断开 → asyncio.CancelledError
  │     ├─ write job → adopt 保活（observer_connected=False）
  │     └─ 其它      → graph_task.cancel()          ← 断线=取消（用户明确禁止）
  └─ graph 任务收到 CancelledError (main.py:1275)
        └─ jobs.update_job(status="cancelled")      ← 无"取消已请求/已确认"两段
```

### 4.2 已证实缺陷

| ID | 缺陷 | 证据（文件:行） | 用户可见后果 |
|---|---|---|---|
| **C1** | 客户端断线被当成取消 | `main.py:1504-1519`：`except asyncio.CancelledError` 中非 write job 一律 `graph_task.cancel()` | 关页面/切网 = 任务消失；刷新后无记录 |
| **C2** | 取消是"会话级扫描"，不是"job 级指令" | `main.py:1567` `list_for_session(..., limit=20)` | 同一会话多个任务时误杀；超过 20 个任务漏杀 |
| **C3** | 无 task 时直接写 `cancelled` | `main.py:1584-1588` | 服务器重启后残留任务、或任务在别的进程，被伪称"已终止" |
| **C4** | 取消无"已请求"中间态 | `jobs.py:23` `VALID_STATUS` 只有 `running/completed/failed/cancelled` | 无法区分"已发信号"和"已停下"，UI 只能二值 |
| **C5** | 终态可被覆盖 | `main.py:1327-1331` finally 里 `update_job(status="completed")` 无守卫；`jobs.update_job`(`:72-98`) 不做状态校验 | 已 `cancelled` 的任务被改回 `completed` |
| **C6** | 执行中的工具不感知取消 | 无任何取消令牌：`registry._run_single_tool`(`:284-350`) 只在 `wait_for` 超时后返回，工具线程仍跑完 | "点了停止但还在查/还在写" |
| **C7** | 迟到结果照常落库 | `registry.py:326` 直接 `return ToolMessage(...)`；`research/tools.py:304 _save_artifact` 内 `path.write_bytes` + `INSERT INTO artifacts` 无取消检查 | 停止后仍出现新文件/新引用 |
| **C8** | 产物写入非原子 | `_save_artifact`:314 `path.write_bytes(content)` 后 :318 才插库；中途失败只 `unlink` 文件，DB 可能留行 | 半个 xlsx/HTML；文件与索引不一致 |
| **C9** | 前端删气泡、立刻解锁发送 | `app.js:1879` `botMessage.remove()`；`:1881`+`:1157` `setSendDisabled(false)` | 已完成的工具轨迹一起消失；停止中就能发新请求 |
| **C10** | `GET /jobs/{job_id}` 无属主校验 | `main.py:1534-1541`（无 `require_owned_session`） | 任意登录用户可读他人任务状态（IDOR） |

---

## 5. 目标状态机

```
                 ┌──────────── 用户点停止（POST /jobs/{id}/cancel） ────────────┐
                 ▼                                                             │
running ──detach(断线)──▶ detached ──(宽限期到)──▶ cancelled(理由=detached_timeout)
   │                        │                                                  │
   │                        └── 正常完成 ──▶ completed                          │
   ├── cancel_requested ──▶ cancelled(理由=user) ───────────────────────────────┘
   ├── 正常完成 ──────────▶ completed
   └── 异常 ──────────────▶ failed
```

规则：
1. **终态**：`completed | failed | cancelled`，一旦进入不得回退（`cancel_requested → completed` 允许，并记 `stop_note="停止请求晚于完成"`）。
2. `detached` 只表示"观察者离开，执行继续"，**不改变完成与否**；它永远不会被当成 user cancel。
3. 任何 `cancelled` 都必须带 `stop_reason ∈ {user, detached_timeout, shutdown, internal}`。
4. 前端只认终态解锁发送；`cancel_requested` 阶段显示"正在停止…"。

---

## 6. 函数级最小修改清单

改动总量：**新增 1 文件（~70 行）+ 修改 6 文件（+330/−90 行）**，不含测试。

### S1-01 新增 `src/core/cancel.py`（~70 行，无既有依赖）

```python
class ToolCancelled(Exception): ...            # 与 ERR_* 前缀一致的取消信号

class CancelToken:                              # 线程安全，跨 executor 线程共享
    def __init__(self): self._ev = threading.Event()
    def cancel(self): self._ev.set()
    def cancelled(self) -> bool: return self._ev.is_set()
    def raise_if_cancelled(self, where: str = ""): ...   # raise ToolCancelled

_current: ContextVar[CancelToken | None]
def set_current_token(t) -> token object  ...   # 返回 token 对象供 reset
def reset_current_token(token) -> None
def current_token() -> CancelToken | None
def raise_if_cancelled(where: str = "") -> None  # 无令牌时静默返回
```

**为什么能到达工具线程**：`registry.py:299` 已经 `ctx = contextvars.copy_context()`，`:310` 用 `ctx.run(...)` 进入 executor 线程 —— 请求级 `ContextVar` **天然随复制上下文进入工具线程**，且 `threading.Event` 跨线程可见。因此**不需要改任何工具的函数签名**。

### S1-02 `src/core/jobs.py`：幂等迁移 + 状态守卫 + 三个新函数

| 位置 | 改动 |
|---|---|
| `_connect()` `:26-48` | 建表后执行 `_migrate()`：`PRAGMA table_info(task_jobs)`，缺则 `ALTER TABLE task_jobs ADD COLUMN` 三列：`cancel_requested_at TEXT NOT NULL DEFAULT ''`、`detached_at TEXT NOT NULL DEFAULT ''`、`stop_reason TEXT NOT NULL DEFAULT ''`；幂等（重复执行无副作用，SQLite 不支持 `ADD COLUMN IF NOT EXISTS`） |
| `VALID_STATUS` `:23` | `{"running","detached","cancel_requested","completed","failed","cancelled"}` |
| `update_job()` `:72-98` | 加**终态守卫**：读当前 status；若当前 ∈ 终态 且 新 status ≠ 当前 → 拒绝并 `logger.warning("[Jobs] refused terminal downgrade ...")`；除非显式 `force=True` |
| 新增 `request_cancel(job_id, reason="user") -> bool` | `UPDATE ... SET status='cancel_requested', cancel_requested_at=?, stop_reason=? WHERE job_id=? AND status IN ('running','detached','cancel_requested')`；`rowcount==0` 表示已是终态（返回 False，调用方不得覆盖） |
| 新增 `mark_detached(job_id)` | `SET status='detached', detached_at=? WHERE status='running'`（不回退、不覆盖 `cancel_requested`） |
| 新增 `mark_stopped(job_id, reason)` | `SET status='cancelled', stop_reason=?, finished_at=? WHERE status IN ('cancel_requested','detached','running')` |

### S1-03 `src/api/main.py`：5 处

| # | 位置 | 现状 | 改成 |
|---|---|---|---|
| a | `process_graph` 开头 `:1140-1145` | 只注册 task | 额外建 `CancelToken`，登记 `_job_tokens[job_id]`，`set_current_token(token)`；`finally` 里 `reset_current_token` + `_job_tokens.pop` |
| b | 事件生成器 `except asyncio.CancelledError` `:1504-1519` | 非 write job → `graph_task.cancel()` | **一律** `observer_connected=False; keep_alive=True; jobs.mark_detached(job_id); adopt(graph_task)`；若 `settings.AGENT_DETACH_GRACE_SEC>0`，起一个监视任务：到期仍未结束且未被用户取消 → `token.cancel(); graph_task.cancel(); jobs.mark_stopped(job_id,"detached_timeout")` |
| c | `process_graph` 的 `except asyncio.CancelledError` `:1275-1284` | 只置 `cancelled` | 读 job 前态：`cancel_requested` → `mark_stopped(job_id,"user")`；否则 `mark_stopped(job_id,"internal")`；`finish_request(status="cancelled")` 保留 |
| d | `finally` `:1312-1331` | 无守卫 | 分支里的 `update_job(status="completed")` 改为 `jobs.mark_completed_if_open(job_id, ...)`（等价于带守卫的 update，C5 修复） |
| e | 新增 `POST /api/v2/jobs/{job_id}/cancel`；改造 `GET /jobs/{job_id}` `:1534-1541`；改造 `POST /chat/cancel` `:1561-1591` | 见 C2/C3/C10 | 见下 |

新端点语义（**这是 S1 的核心契约**）：

```
POST /api/v2/jobs/{job_id}/cancel
  auth : job.session_id → require_owned_session(...)；未知 job → 404（不泄露存在性）
  200  : {job_id, previous_status, status, accepted, target, stop_reason}
    · 已终态          → status=<终态>, accepted=false, note="already_finished"
    · running 且 task 在本进程 → request_cancel() → token.cancel() → task.cancel()
                                  → status="cancel_requested", accepted=true, target="in_process"
    · running 但 task 不在本进程 → request_cancel()（**不写 cancelled**）
                                  → status="cancel_requested", accepted=true, target="not_in_this_process"
  幂等 : 连续调用第二次 previous_status 变 cancel_requested，accepted=false
```

`POST /api/v2/chat/cancel` 保留为兼容封装：遍历会话 job → 逐个调用上面的同一函数（`limit` 提到 50，且只处理 `running/detached`）。

`GET /api/v2/jobs/{job_id}` 增加 `require_owned_session`（修 C10），返回 `status/current_step/stop_reason/cancel_requested_at/detached_at/created_at/updated_at/finished_at`。

### S1-04 `src/tools/registry.py`：迟到结果闸门

| 位置 | 改动 |
|---|---|
| `_run_single_tool` `:305-326`（`content_and_artifact` 分支）与 `:327-350`（普通分支） | executor 返回后**先**判令牌：`token and token.cancelled()` → 不登记 artifact、`diag("late_tool_result", tool=...)`、返回 `ToolMessage(content="[TOOL_CANCELLED] 已停止，本次结果未采用。", artifact={"cancelled": True}, ...)` |
| `except Exception` 两处 `:319/:336` | 若 `isinstance(e, ToolCancelled)` 优先返回 `[TOOL_CANCELLED]`（不要把取消当失败） |

### S1-05 `src/research/tools.py`：`_save_artifact` 原子化 + 取消闸门（唯一产物收口点）

现状 `:304-343`：`write_bytes` → 插库（失败删文件）→ `register_file`。

改成四步：
1. `raise_if_cancelled("artifact:before_write")` → 取消则抛 `ToolCancelled`（**不产生任何文件**）；
2. 写 `path.with_name(path.name + ".part")` → `flush()` + `os.fsync()`；
3. `raise_if_cancelled("artifact:before_commit")` → 取消则删 `.part` 并抛；否则 `os.replace(part, path)`（同目录 → 原子）；
4. 插库 + `register_file`；若此步抛异常 → `path.unlink()` + `DELETE FROM artifacts`（**注意：`artifacts` 表在 `agent/state/research_artifacts.sqlite3`，`session_files` 在 `agent/state/session_files.sqlite3`，两个库两处都要清**）。

> 影响面：17 个工具里凡产出文件（xlsx / html / ris / bibtex / csv / svg / md）全走此函数 —— 一次修改覆盖 C7+C8，不需要逐个工具改。
> 集成测试 M8 必须同时核对三个落点：磁盘文件、`research_artifacts.sqlite3:artifacts`、`session_files.sqlite3:session_files`。

### S1-06 `web/assets/app.js`：停止语义（4 处）

| 位置 | 现状 | 改成 |
|---|---|---|
| `stopCurrentTask` `:1142-1160` | abort + 会话级 cancel + 立刻解锁 | `requestStop()`：置 `isStopping=true` 且**不解锁**；按 `currentJobId` 调 `POST /api/v2/jobs/{id}/cancel`；每 800 ms 轮询 `GET /api/v2/jobs/{id}` 至多 8 s；终态 → `finalizeStopped(jobStatus)`；`currentJobId` 未知时退回 `/chat/cancel` |
| `removeInterruptedReply` `:1871-1882` | `botMessage.remove()` | 改名 `abortStream(reason)`：**不删气泡**；保留已完成工具行；未完成行标 `stopping`；`setSendDisabled(false)` 仅在终态 |
| `:1909-1914`（300 s 超时）/ `:2447-2457`（早退）/ `:1368-1369`（切会话） | 共用一个删气泡函数 | 传 `reason ∈ {timeout, network, switch, user}`，分别给出"处理超时/连接中断/已切换会话/已停止"文案 |
| `:2461` finally | 条件清理 `activeTurnCancel` | 保持，但新增 `isStopping` 复位 |

`currentJobId` 来源：服务端首个 `status` 事件或 `done.job_id`（`main.py:1195` 已下发 job_id），前端在 `handleEvent` 里存下即可。

### S1-07 `web/assets/app.css`：仅加两个类

```css
.tool-trace.is-cancelling .tool-trace-label { color: var(--warn, #B4803A); }
.tool-trace.is-cancelled  .tool-trace-label { color: var(--text-secondary); }
```

### 6.8 明确不改（避免范围蔓延）

`_scoped_toolset` / `tool_availability.py`（S2 议题）、`general_agent_graph` 主循环、`progress_bus` 事件协议、`session/manager.py` 序列化、`files.py` 关联逻辑、任何工具函数签名、`admin_audit` 表结构。

---

## 7. 测试设计

### 7.1 单元测试（新增 6 文件，本地 pytest，无网络）

| 文件 | 用例数 | 覆盖点 |
|---|---|---|
| `src/core/test_cancel_token.py` | 10 | 置位/查询；`raise_if_cancelled` 抛 `ToolCancelled`；无令牌时静默；`contextvars` 在 `copy_context()+ctx.run` 进入新线程后可见（复刻 registry 路径）；跨线程置位即时可见；重置后不泄漏 |
| `src/core/test_job_state_machine.py` | 14 | 迁移幂等（连跑两次列数不变）；`request_cancel` 幂等；终态不可回退（`completed→cancel_requested` 被拒）；`cancel_requested→completed` 允许且写 `stop_note`；`mark_detached` 不覆盖 `cancel_requested`；`mark_stopped` 只从非终态迁入；`finished_at` 只写一次 |
| `src/research/test_artifact_atomicity.py` | 8 | 预取消 → 无文件无 DB 行；提交后取消 → 文件与行都被清理；成功路径 URL 形状不变；`.part` 从不残留；插库失败 → 文件被删；重复文件名不覆盖（uuid 前缀） |
| `src/api/test_cancel_endpoints.py` | 10 | 他人 job → 404；未知 job → 404；终态 job → `accepted=false`；running 且无本地 task → `cancel_requested` 且**不是** `cancelled`；重复取消幂等；`GET /jobs/{id}` 越权 → 404；`/chat/cancel` 兼容返回列表；`limit` 提升后能命中第 21 个任务 |
| `src/tools/test_late_result_discard.py` | 5 | 慢工具期间取消 → 返回 `[TOOL_CANCELLED]`、artifact 丢弃、`diag` 有 `late_tool_result`；取消不记为 failed；取消后 `tools_called` 不含该工具 |
| `src/graph/test_disconnect_detach.py` | 5 | 模拟生成器 `CancelledError`（断线）→ job 变 `detached` 且**未** `cancelled`、task 未被 cancel；显式取消 → `cancelled`；宽限期到 → `cancelled(detached_timeout)` |

合计 **52** 用例。目标：本地 `179 → 231 passed / 0 failed`。

### 7.2 集成测试矩阵（真实实例，`experiments/trace/probes/cancel_matrix.py`）

8 个场景，每个场景记录**同一份 JSON**（`experiments/trace/results/cancel_<ts>/matrix.jsonl` + `summary.md`）：

| # | 场景 | 期望（机器可判） |
|---|---|---|
| M1 | 工具执行中（本地检索 20 s）点停止 | `cancel_requested` → `cancelled(user)`；工具行 `cancelled`；无新 artifact |
| M2 | 工具执行中直接断开连接（模拟关页面） | `detached`，**不是** `cancelled`；任务继续到 `completed` |
| M3 | M2 之后宽限期内重新连上 | 事件可回放/`/messages` 可见该轮完整轨迹 |
| M4 | 停止中再发一条新消息 | 新 job 独立 `running`，旧 job 仍 `cancelled`；无交叉污染 |
| M5 | 连续两次 `/jobs/{id}/cancel` | 第二次 `accepted=false`，状态不变 |
| M6 | 任务已完成后再取消 | `accepted=false`，`status=completed` |
| M7 | 排队中（capacity 等待）取消 | `cancelled(user)`，不占队列名额（`chat_capacity` 已释放） |
| M8 | xlsx 导出执行中取消 | 无 `.part`、无 `artifacts` 行、无 `session_files` 行 |

每场景同时抓：`GET /jobs/{id}` 三次快照（取消前/中/后）、`/messages` 该轮 tool rows、`artifacts` 表计数、`state/artifacts/<sid>/` 文件清单、`agent/logs` 相关行。全部落盘，供复核。

### 7.3 手工/浏览器清单（不可自动化的部分）

1. 点停止 → 气泡**不消失**，标题从"正在处理"变"正在停止…"→"已中断"；
2. 已完成的工具行仍在，未完成行显示"已停止"；
3. 停止过程中发送按钮保持禁用/显示"正在停止"，终态后解锁；
4. 刷新页面 → 该轮仍在历史里，可展开看工具轨迹；
5. 断网 10 s 内恢复 → 提示"连接中断"而非"已停止"。

### 7.4 数据留存与可复现

- 测试代码、探针脚本、`matrix.jsonl`、日志片段、`/jobs` 快照全部进仓库与 `experiments/trace/results/`；
- 每个场景一条命令即可重跑（脚本自带 `--scenario M3`）；
- 时间戳与 PID 写进 `summary.md`，便于比对同一实例。

---

## 8. 验收对照（你的 10 条稳定性要求）

| 你的要求 | S1 对应 |
|---|---|
| 前端展示不影响 Agent 执行 | 前端只在 `app.js` 侧改，`abortStream` 不触碰服务端执行；断线改为 detach 后执行不再受前端影响 |
| 断线不等取消 | S1-03b + `detached` 状态 + M2/M3 |
| 取消是协作式而非硬杀 | S1-01/04/05 三段闸门（写前、提交前、结果采用前） |
| 刷新后仍见真实记录 | 状态落 `task_jobs` + 历史行 status/summary/elapsed 已在前序修复中落库 |
| 无迟到结果落库 | S1-04 + M1/M8 |
| 无半个文件/孤立文件 | S1-05 原子提交 + M8 |
| 不能伪称已终止 | S1-03e `not_in_this_process` 只到 `cancel_requested` |
| 不改既有契约 | 只加列/加端点，`/chat` 与 `/chat/cancel` 保持兼容 |
| 端到端可复现 | §7.2 矩阵 + 脚本 |
| 不一次性做 S1–S5 | 本轮只出清单，未动代码 |

---

## 9. 提交、部署、回滚（S1 实施时执行）

1. 单提交：`git commit -m "S1: cooperative cancel, detach-on-disconnect, atomic artifacts"`（本地，不 push）。
2. 部署只写 `01_源码_src/`（§2.5 清单），先起 staging（§2.10）跑 §7.2 矩阵。
3. 生产部署前：`cp agent/state/sessions.db agent/state/sessions.db.pre-s1.<stamp>`；
4. 部署后健康检查：`curl /`=200、`/api/v2/session/{sid}/messages` 可读、跑 M1/M6 两个最小场景。
5. 回滚：§2.9 的命令 + 还原 DB 冷备。

---

## 10. 需要你拍板的 3 个决策

| # | 决策 | 选项 | 我的建议 |
|---|---|---|---|
| **D1** | 断线后执行多久算"宽限期" | (a) 无限期继续（最贴合"断线≠取消"，但可能烧 token）(b) 180 s 后自动 `cancelled(detached_timeout)`(c) 60 s | **(b) 180 s**，可配置 `AGENT_DETACH_GRACE_SEC`，且理由字段明确写"断线超时"，不写"用户取消" |
| **D2** | 停止中是否允许立即发新消息 | (a) 禁用直到终态（现有前序修复已是这个方向）(b) 允许并发新 job | **(a)** |
| **D3** | 生产进程纳管方式 | (a) 纳入 systemd（需停掉手工进程）(b) 保持手工 + 写 `deploy.sh` 用 pidfile | **(a)**，否则每次部署都可能"以为部署了、其实还在跑旧代码" |

---

## 11. 附录：本轮 S0 操作留痕（只读 + 一次备份）

| 操作 | 对象 | 结果 |
|---|---|---|
| 进程/端口/systemd 查询 | 服务器 | PID 3495578，`session-15667.scope`，unit inactive |
| 三列哈希比对 | 16 文件 | 生产 = `ff58b1f` |
| DB 表与列枚举 | `agent/state/` | 见 §2.6（**5 个库**，不是 1 个；另发现 0 字节遗留 `sessions.sqlite3`） |
| 迁移面确认 | `task_jobs` | 无取消列；`messages.execution_json` 已存在 |
| 语料规模 | `agent/data` | 3 批 / 126,943 块 |
| 本地测试 | `pytest -q src` | 179 passed |
| **备份** | `01_源码_src`（无缓存） | `backups/source-before-ff58b1f-20261030-114701.tar.gz`，5.3 MB，sha256 已记 |

**未做**（按要求）：未改任何代码、未重启、未部署、未 push、未删任何文件。
