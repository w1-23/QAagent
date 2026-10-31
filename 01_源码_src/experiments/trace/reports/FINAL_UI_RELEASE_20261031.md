# 前端体验重做 · 发布报告（2026-10-31）

- 发布版本：`e5a6b32`（本地 HEAD = staging 8011 = 生产 8000）
- 上一生产版本：`14f00c7`（2026-10-30 23:03）
- 变更范围：`01_源码_src/src` + `01_源码_src/web`；未改 Agent 主循环、未改检索排序、未改部署方式
- 测试基线：508 passed（`ad72315`）→ **559 passed**（`e5a6b32`）

---

## 1. 改动清单（按条目）

### P0-A 刷新后执行气泡分裂
- 后端 `src/api/main.py`：新增 `build_history_turns()`，以 `turn_request_id` 把一轮内的 1 条 human + N 条 ai + M 条 tool 聚合成一个「轮」；`/api/v2/session/{id}/messages` 除 `messages` 外新增 `turns` 数组，且**分页不切断轮次**（跨页的轮整体落在同一页）。
- 前端 `web/assets/app.js`：新增 `groupHistoryTurns()` / `promoteLastAnswer()` / `historyTraceStatus()` / `orderHistoryTrace()`；重写 `renderHistoryMessages()`，每轮渲染 **1 个助手气泡 + 1 份执行轨迹 + 1 个文件区**。
- 新增测试 `src/api/test_trace_turn_merge.py`。

### P0-B 报告截断与重试重复
- 新增 `src/core/deliverable_check.py`：`validate_html_document()`（闭合标签/体量下限/是否空壳）、`validate_markdown_payload()`、`dedupe_key()`（**不含 client_request_id**）、`RetryLedger`（同一产物最多重试 2 次）。
- `src/research/tools.py::_save_artifact`：预算 → 校验 → 幂等 → 原子发布（先写临时再 rename）。
- `src/session/files.py`：新增 `dedupe_key` 列与「认回已存在文件」逻辑。
- 新增 33 例测试。

### P1-C 宠物缩放浮层常驻
- `web/assets/mascot.js` / `mascot.css`（V3）：
  - 缩放钮移左上、隐藏钮移右上（原本两钮都在右上且重叠）。
  - 控件待机 `opacity:0; pointer-events:none`；删掉 `visibility:hidden`（实测它让两钮永远无法 Tab 聚焦）。
  - 浮层与按钮间隙 6px → 26px（180% 时 ✕ 随 scale 长高会压住浮层）。
  - 移除残留 `overrideOn` 标志位（被高优先级动作替换后永久停留 true，导致自主动作永久停摆）。
  - `renderFrame` 补写 `data-strip`；`clampPopover` 平移除以 scale；`openPopover` 先取消 blur 定时器；新增 `#petScaleRange` 监听。
  - 暴露 `window.CitrusMascot.getScale/setScale/getVisible/setVisible/refreshPanelState`。

### P1-D 资料库拖拽宽度
- `web/assets/session_files.js` + `app.css`：拖拽上限 = `视口宽 − 左侧栏右边界 − 24px`，只写 `--library-w`；刷新后保持；双击复位 340。

### P1-E SVG 产物入资料库
- 预览走 `/api/v2/files/{id}/download` + `<img>`（与页面同源但脚本不执行），附「复制源码 / 定位来源 / 文件信息（9 行）」；修 `jumpFromFile` 未定义、更多菜单被裁切。

### P2-F Loading 对比度
- 三主题 `--loading-accent`：light `#A5560F`、cartoon `#9E5A16`、dark `#E8AE5A`（均 ≥ 4.5:1）。

### 资源版本号
`app.css?v=21`、`mascot.js?v=3`、`session_files.js?v=4`。

---

## 2. 验证矩阵（真实浏览器 / 真实 HTTP，非单测替代）

| 项 | 方法 | 修前 | 修后 |
|---|---|---|---|
| P0-A 气泡分裂 | headless Chrome + CDP，`probe_split_expr.js` | `273aec5d` 1 用户→**3** 气泡；`9c83f84f` 1→3；`b01558d0` 1→3（含真重复）；`0ce5a8dd` 1→2 | 四个会话均 **1→1**（1599/2030/3207/1779 字符），重复文本为空 |
| P0-A 轮次键完整性 | 生产 `/messages?limit=60`（临时 token） | ai/tool 行 `client_request_id` 全空 | 返回 `turns:15` / `messages:60`；**assistant+tool 缺轮次键 = 0**；`has_more=true` |
| P0-B 产物校验 | 真实产物 8 件过闸 | 截断 HTML/空 MD 会落库 | 7 件通过、1 件被拒；重试同源产物不重复落库 |
| P1-C 控件位置 | 真实 Chrome 1280×900，80/100/180% | 两钮重叠右上 | scale 左上、close 右上；浮层在三种 scale 下均不出界、不压按钮 |
| P1-C 自主调度 | 14s 真实计时器 + Node 假 DOM（124 断言） | `overrideOn` 残留 → 动作停摆 | 自主序列不重复不固定周期；8 类失败场景全过 |
| P1-D 拖宽 | 真实指针拖拽（`probe_library_resize.js`） | 固定 340 | 1440 → **1062**（间隔 24px，无横向滚动）；1024 → **946**；刷新保持；双击回 340 |
| P1-E SVG 预览 | 真实抽屉 + 真实接口 | 无预览 | `download` 200 / `image/svg+xml` / 1172 字符；`<img>` 720×420，注入脚本 0；「定位来源」返回 true |
| P2-F 对比度 | 逐层 alpha 合成后计算 | 3.33 / 3.49 / 9.01 | **5.07 / 5.06 / 10.32** |
| 生产真轮次 | 真 SSE `POST /api/v2/chat` | — | `turn_status=completed`，gen 2878ms，total 2953ms，`history_saved=true` |

已知取舍（用户已确认方向）：资料库拉到上限时，聊天区仅剩约 24px；如需保留最小聊天宽度，改 `session_files.js` 的 `chatSafeMax` 即可。

---

## 3. 部署与回滚

### 部署（已完成）
1. 打包 `_scratch/deploy_e5a6b32.tar.gz`（16.3 MB / 1036 条目，仅 `src` + `web`）。
2. 备份：`/home/WZQ/CitrusQA/backups/pre_deploy_20261031_164349`（含 `src`、`web`、`sessions.db`、`accounts.sqlite3`、`web_search_config.json`、`deepseek_web_api_key`）。
3. 解包到 `01_源码_src/`，写 `DEPLOYED_COMMIT=e5a6b32`。
4. 重启：`kill <旧 pid>` → `setsid nohup ../.venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 --workers 1`（cwd=`01_源码_src`）。
5. 新 pid `101332`，`Application startup complete`，`/`=200。
6. 字节一致性（LF 归一 sha256[:16]）：`main.py ebc6a008ef90f9b6`、`app.js 84a81cbfe406c9c0`、`index.html 31eb50e0bdc62ed7`；`app.js` 212342 B、`mascot.js` 48516 B、`session_files.js` 50520 B，与本地逐一致。
7. 冒烟：markers 全在（`ctxSentBlock`/`load-sprout-icon`/`sessionFilesResizer`/三版本号）；`/api/v2/auth/me` 与 `/api/v2/files` 均 401；生产日志无 ERROR/Traceback。

staging `/home/WZQ/staging_citrus`（8011）同版本，pid `99232`，提前一轮完成演练。

### 回滚
```bash
cd /home/WZQ/CitrusQA
kill <当前 pid>
cp -a backups/pre_deploy_20261031_164349/src 01_源码_src/
cp -a backups/pre_deploy_20261031_164349/web 01_源码_src/
echo 14f00c7 > DEPLOYED_COMMIT
cd 01_源码_src && setsid nohup ../.venv/bin/python -m uvicorn src.api.main:app \
  --host 127.0.0.1 --port 8000 --proxy-headers --forwarded-allow-ips=127.0.0.1 --workers 1 \
  > ../agent/logs/uvicorn_8000_rollback.log 2>&1 < /dev/null &
```
数据库未做 destructive 迁移（`dedupe_key` 为新增可空列），回滚不需回退库；如需彻底恢复会话库，备份内含 `sessions.db`。

---

## 4. 遗留与未验

- 真实指针 capture 与触屏 `.is-touch` 路径未在 Safari/iOS 验证。
- 复制源码的**剪贴板写入**在 headless 下被浏览器策略拒绝；数据源已由 `download` 接口验证，子 Agent 在 headful 下读到 1194 字符。
- Office 公式/图表、超大 PDF 性能、>200 轮长会话未验。
- `git push` 未执行（需明确指令）。

## 5. 提交

`682bc5e` mascot V3 · `616b880` P0-A 轮次聚合 · `6a09242` P0-B 产物校验 · `8eabefc` 契约测试更新 · `e5a6b32` P1-D/P1-E/P2-F 资料库与配色。

---

## 6. 吉祥物自主行为调度器（本轮补充）

### 6.1 结构（调度层加在既有状态机上，没有第二个控制器）

```
真实 Agent 状态 ─┐
用户点击/拖动/缩放 ─┼─► requestAction(action, reason, rank) ─► 帧播放器
带种子的待机调度 ─┘        （rank: 用户 40–50 > 任务 30 > 自主 20）
```

- 优先级：用户交互（rank 40–50）> 真实任务态（rank 30）> 自主随机（rank 20）> 待机 `idle.rest`。
- 交互打断任务动作后，`restoreFromTask()` 回到被打断的真实任务态，而不是永久待机。
- `canAuto()` 守卫：`autoOn && !hidden && !dragging && !taskState && !docHidden && !still()`，
  且当前动作是 `cat === 'interact'` 时不排自主动作。

### 6.2 动作清单（30 个已注册；18 个允许自主播放）

| 分类 | 数量 | 动作 ID | 自主 | 权重/冷却 |
|---|---|---|---|---|
| idle 日常微动作 | 5 | `idle.blink` `idle.glance` `idle.tilt` `idle.stretch` `idle.shift` | 可 | 32/20/16/12/10，冷却 6–24s |
| expression 表情 | 5 | `expr.smile` `expr.shine` `expr.surprise` `expr.squint` `expr.curious` | 可 | 30/25/20/15/10，冷却 18–30s |
| leisure 休闲 | 6 | `leisure.read` `leisure.readbook` `leisure.doze` `leisure.music` `leisure.tea` `leisure.books` | 可 | 26/18/20/12/10/8，冷却 26–50s |
| sleep 低频 | 2 | `sleep.curl` `sleep.blanket` | 可 | 60/40，冷却 70–90s |
| work 任务 | 5 | `work.think` `work.read` `work.search` `work.type` `work.done` `work.fail` | **否** | 由 Agent 状态触发 |
| interact 互动 | 3 | `interact.click` `interact.wave` `interact.love` | **否** | 仅点击/触摸触发 |
| drag | 2 | `drag.hold` `drop.land` | **否** | 拖动/落下 |
| fallback | 1 | `idle.rest` | 否 | 全部动作的回退姿态 |

素材覆盖：动作图里被切成 30 段，全部已注册；18 段进自主池，12 段保留给任务/互动/拖动。
没有「已注册但没有任何触发路径」的动作，也没有「自主池里混入任务专用动作」。

### 6.3 调度参数

- 随机：`mulberry32(seed)`，种子在启动时按 `Date.now() ^ Math.random()` 生成一次并记入日志，可复现。
- 选动作：先按 `BUCKET_WEIGHT`（idle 45 / leisure 25 / expression 20 / sleep 10）选桶，再按动作权重选。
- 不重复：`RECENT_MEMORY = 3` + `lastActionId` 双保险；池空时逐级放宽（严格 → 放宽重复 → 放宽冷却 → 任意合法），
  保证「缺帧/全冷却不会让调度器停摆」。
- 间隔：`ACTIVITY` 三档 4–8s（活泼）/ 6–12s（自然）/ 15–25s（安静），实际间隔在区间内随机。
- 计时器：全程 `setTimeout` 单链，无 `setInterval`，无轮播。

### 6.4 实测（真实浏览器，8010，CDP）

| 场景 | 结果 |
|---|---|
| 活泼档 45 秒 | 5 个不同动作 `sleep.blanket → idle.stretch → expr.shine → expr.smile → idle.tilt`；相邻重复 0；间隔 14424/7492/7634/5789ms；计时器数 **1**；种子 3519040215；位置与尺寸不变 |
| 任务态 `retrieving` 12 秒 | 自主动作 **0** 次，动作为 `work.read`（任务动作） |
| 任务结束后点击宠物 | 立即 `interact.click`；交互结束后 7 秒内自主动作恢复 2 次；计时器数 1 |
| 动作分类分布 | 三个桶实际出现：sleep / idle / expression，符合权重设计 |

### 6.5 契约测试

`src/api/test_mascot_scheduler_markup.py`（9 例）锁定：30/18 动作与桶分布、权重 45/25/20/10、
自主动作必须有权重与冷却、任务与互动动作 `auto:false`、种子仅初始化一次、冷却逐级放宽、
三档间隔、无 `setInterval`、`canAuto` 六项守卫。
