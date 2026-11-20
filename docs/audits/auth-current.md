# R1 只读产品审计：账号 / 认证 / 会话链路（CitrusScholar）

- 审计性质：只读静态审计。未启动应用、未运行 pytest、未读写任何真实 DB / 附件 / 凭据 / 日志。
- 基线：远程 Linux 隔离候选仓库。
- 分支：`feat/invite-auth-nature-figure`。
- HEAD：`efeb7edc74606d1b6f62ac52e0b6e6187ec0792f`（`efeb7ed feat: add interactive Browse with tabs and bounded concurrency`）。
- 起始 `git status --short`：空（工作树干净）。
- 审计前 `docs/` 目录不存在；未发现其他审计文档，无覆盖行为。本文件是唯一新增文件。

---

## 1. 工单目标

核对当前认证链路的可交付事实：注册、登录、Cookie/Origin/Host/CSRF、账号 `active` 读取、业务租约入口、管理员列表/启停/重置、CLI 与 Web 重置差异、旧库/旧会话迁移集成点，并产出可验证的 AUTH-01–25 入口清单与缺件说明，供主会话 / A1 决策。本文件不设计最终 API。

## 2. 读文件

后端：
- `01_src/src/auth.py`（全量 740 行）
- `01_src/src/api/main.py`（重点区段：300–440、585–600、600–650、650–780、800–965、2735–2760、3680–3730、5255–5270）
- `01_src/scripts/manage_account.py`（全量 153 行）
- `01_src/scripts/create_admin.py`（全量 45 行）
- `01_src/src/core/account_admission.py`（全量 257 行）
- `01_src/src/core/account_leases.py`（全量 247 行）
- `01_src/src/core/resource_access.py`（全量 52 行）
- `01_src/src/session/manager.py`（285–300、432–466）
- `01_src/src/config.py`（PROJECT_ROOT / state 路径，17–58、285）
- 交叉引用（Cookie 读取旁路）：`01_src/src/api/figure_router.py`（88–112、163–165）、`01_src/src/api/integrity_router.py`（26–27）、`01_src/src/api/reference_router.py`（43–44）

前端：
- `01_src/web/index.html`（36–56、218）
- `01_src/web/assets/app.js`（1–60、755–870）
- `01_src/web/assets/admin.js`（1–130、200–215）
- `01_src/web/admin.html`（14、39）
- `01_src/web/assets/app.css`（1541–1552、1650–1651）
- `01_src/web/assets/admin.css`（15–16）

测试盘点：
- `01_src/web/tests/`：仅 4 个 `.cjs`（clipboard / navigation / session-recovery / task-observation），与认证无关。
- 全仓共 7 个 Python 测试文件；grep `set_user_status|user_for_token|reset_password|check_rate_limit|init_store|account_lease|account_admission` 在 `*test*.py` 中零命中。**认证无既有自动化测试。**

## 3. 写文件

- 仅新增：`docs/audits/auth-current.md`（本文件）。
- 为创建该文件新建目录 `docs/audits/`（此前 `docs/` 不存在）。
- 未修改任何源码 / 测试 / 运行配置；未提交、未推送、未部署、未重启、未安装依赖、未调用外部 API。

## 4. 关键事实（含路径与行）

### 4.1 存储与凭据
- `01_src/src/auth.py:26` `DB_PATH = PROJECT_ROOT/"state"/"accounts.sqlite3"`；`:27` `COOKIE_NAME="citrus_auth"`；`:28` TTL 7 天；`:29` PBKDF2 310,000 轮。
- `auth.py:53-74` SQLite 连接：WAL、busy_timeout 30s、foreign_keys ON，Unix 下收紧目录 0700 / 文件 0600。
- `auth.py:77-130` `init_store`：`CREATE TABLE IF NOT EXISTS` 建 `users / browser_sessions / account_deletion_retries / auth_events / daily_usage / workspace_files`；随后 `_ensure_user_profile_columns`、`account_admission.init_schema`、清理过期会话、`_ensure_session_owner_column`。
- 密码：`auth.py:162-163` PBKDF2-HMAC-SHA256，16 字节随机盐（`:205`）；比较用 `hmac.compare_digest`（`:272`、`:327`、`:344`）。会话令牌 `secrets.token_urlsafe(36)`，仅存 SHA-256 摘要（`:224-227`、`:329-333`）。

### 4.2 注册 → 自动登录链（实际）
1. 前端 `app.js:759-771`：`authenticate('register')` → `POST /api/v2/auth/register`，body `{username,password,avatar_preset}`（`app.js:769-771`）。
2. `main.py:696-708` `register_account`：先 `check_rate_limit("register", ip, 8, 3600)`（`:700`），再 `register(...)`（`:702`），成功 `_set_auth_cookie(response, token)`（`:705`）。
3. `auth.py:238-241` `register` → `_create_account(bootstrap_admin=False)`；`auth.py:196-235`：校验、`BEGIN IMMEDIATE`、`INSERT users`（`active=1,daily_limit=30`）、生成令牌并 `INSERT browser_sessions`、提交，返回 `(public_user, token, None)`。
4. `main.py:690-693` `_set_auth_cookie`：`response.set_cookie("citrus_auth", token, max_age=604800, httponly=True, secure=<env>, samesite="lax", path="/")`。
5. 前端 `app.js:778-796`：保存 `currentAccount` 后 `startAuthenticatedApp()`；之后 `bootstrapAccount` 用 `GET /api/v2/auth/me` 复用同一 Cookie（`app.js:839-847`）。

结论：**注册即自动登录**，以 HttpOnly Cookie 承载；服务端在注册事务内即写入会话令牌摘要。

### 4.3 实际 Cookie / Origin / Host / CSRF
- Cookie 名称/属性见上（`main.py:690-693`）：`HttpOnly`、`SameSite=Lax`、`Path=/`、`Max-Age=604800`、无 `Domain`；`Secure` 仅当环境变量 `CITRUS_COOKIE_SECURE ∈ {1,true,yes}` 时为真（默认关闭）。
- 守卫 `main.py:346-408`：
  - 公共路径集合：`/api/v2/auth/register`、`/login`、`/logout`（`:349-351`）。
  - 非 `GET/HEAD/OPTIONS`（`:352`）：读取 `Origin`（`:353`）；若**有** `citrus_auth` Cookie 且**无** Origin 且非公共路径 → 403（`:355-356`）；若有 Origin，则 `urlsplit(origin).netloc` 必须等于 `Host` 头（`:357-361`）。
  - 公共路径 / `/health` / 非 `/api/` 且非 `/jobs/,/sessions/` → 直接放行（`:362-365`）。
  - 鉴权：`user_for_token(cookie)`（`:367-368`）；`DELETE /api/v2/auth/account` 额外允许删除重试令牌（`:369-371`）；否则 401（`:372-373`）；写入 `request.state.user`（`:374`）。
  - 管理员集中判定：`/api/v2/admin/` 或 `/api/v2/config/` 前缀、以及非 GET 的 `/api/v2/config` → 非管理员 403（`:376-381`）。
- 结论：**没有 CSRF token**；防护 = `SameSite=Lax` + “非安全方法必须 Origin == Host”启发式。该启发式强依赖反向代理透传原始 `Host`；`main.py` 未见 `TrustedHostMiddleware`（`app.add_middleware` 仅 `RequestBodyLimitMiddleware`，`:5269-5270`）。
- 旁路读取：`figure_router.py:104-108`、`integrity_router.py:26-27`、`reference_router.py:43-44` 仍直接读 `citrus_auth` 调 `user_for_token`；这些路由仍被全局中间件包裹，但存在多入口读取点。

### 4.4 账号 active 读取与业务/租约入口
- 读取点：`auth.py:356-365` `user_for_token` 查询条件含 `s.expires_at>? AND u.active=1`（`:363`），停用/过期即 401。
- `require_admin`：`auth.py:400-401` 要求 `role=="admin" and active`；`main.py:324-331` 叠加 `authorize_resource(ADMIN_OPERATION,...)`。
- 业务入口：`main.py:400-407` 对非 GET（除 `/api/v2/auth/account`）在 `account_lease(user_id)` 上下文内执行；不可用 → 409 `{"code":"account_closed"}`。
- 租约实现：`account_leases.py:142-149` `account_lease()` → `acquire_account_lease`（`:135-139`）→ `account_admission.py:124-137` `acquire`，要求 `u.active=1 AND a.deleting=0 AND a.claim_token IS NULL` 且 epoch 匹配。
- 无状态 GET/HEAD/OPTIONS 不取租约（`:400` 条件），删除期间旧令牌因 `active=0` + 会话删除而失效。

### 4.5 管理员列表 / 启停 / 重置（Web）
- 列表：`main.py:871-875` → `auth.py:458-464` `list_users`（含 `requests_today`）。
- 重置密码：`main.py:918-926` `admin_reset_password` → `auth.py:500-509`：生成 `token_urlsafe(15)` 临时密码，更新 salt/hash，并 `DELETE browser_sessions WHERE user_id=?`（`:508`）；**不改变 active**；临时密码仅本次返回。
- 启停：`main.py:929-937` `admin_set_user_active` → `auth.py:467-479` `set_user_status`：`UPDATE active`（禁止把 `deleting=1` 账号复活，`:473-475`）；停用时删除该用户全部浏览器会话（`:476-477`）。路由层禁止管理员停用自己（`main.py:933-934`）。
- 限额：`main.py:940-946` → `auth.py:482-485`。
- 前端：`admin.js:17-24` 统一 `jsonRequest`（401 → 跳 `/?next=%2Fadmin`）；`admin.js:110-127` 重置/启停/限额；`admin.js:200-203` 再次校验 `/api/v2/auth/me` 且 `role==='admin'`；`admin.html:39` 用户表、`:14` 退出。

### 4.6 CLI 与 Web 重置差别（关键）
| 维度 | Web（管理员） | CLI `manage_account.py reset` |
|---|---|---|
| 入口 | `main.py:918-926` → `auth.reset_password` | `manage_account.py:66-82` |
| 查找 | 按 `user_id` | 按 `username` **精确匹配**（`:68`，未 `COLLATE NOCASE`） |
| 密码 | 随机临时密码 `token_urlsafe(15)` | 交互或 `--password`（`:72`） |
| 会话撤销 | **删除** `browser_sessions`（`auth.py:508`） | **不删除**会话 |
| active | 不变 | **强制置 1**（`:78`），即 CLI 重置会重新启用被停用账号 |
| 删除中账号 | 可改密但不会复活（不触碰 active） | `UPDATE active=1` 会触发 `account_admission_no_revive` 触发器（`account_admission.py:49-52`）→ ABORT |
| 返回 | 临时密码显示一次 | 打印成功文本 |
- CLI `create`（`manage_account.py:85-106`）：直接 `INSERT users`（`active=1`，role/daily_limit 直填），**不创建浏览器会话**；`INSERT` 会触达 admission 插入触发器（`account_admission.py:41-44`）。
- CLI `enable/disable`（`manage_account.py:109-117`）：直接 `UPDATE active`，**停用时不删除浏览器会话**；与 `set_user_status` 语义分叉。
- 首个管理员：`create_admin.py:16-40` → `create_first_admin` / `promote_existing_first_admin`（`auth.py:244-279`），创建后 `assign_legacy_sessions_to_admin` 认领无主旧会话（`auth.py:278`、`:512-524`）。

### 4.7 兼容旧 DB / 旧会话的迁移集成点
- `auth.py:133-144` `_ensure_user_profile_columns`：就地补 `avatar_preset / avatar_data / avatar_updated_at / onboarding_completed`，“existing users and sessions stay intact”。
- `auth.py:125-128` 调 `account_admission.init_schema`；`account_admission.py:57` `INSERT OR IGNORE INTO account_admission(owner) SELECT user_id FROM users` 回填旧用户。
- `auth.py:147-159` `_ensure_session_owner_column`：给 `state/sessions.db` 的 `sessions` 补 `owner_user_id` 并建索引。
- `session/manager.py:290` 同样在迁移列表中补 `owner_user_id`；`manager.py:434-441` 建会话时写入 owner；`manager.py:443-451` `get_session_owner` 说明“无主旧行不可访问”；`manager.py:464-465` `session_belongs_to`。
- `auth.py:512-524` `assign_legacy_sessions_to_admin`：把 `owner_user_id IS NULL` 的旧会话一次性归属首个管理员。
- 结论：旧 `users` 与新列、旧 `sessions` 与 `owner_user_id`、admission 回填均有代码级集成点；旧会话中除首个管理员认领外的无主行按设计不可被普通用户访问。

## 5. 建议接口（非最终 API，供 A1 决策）
以下仅为最小接缝建议，不主张新增对外的最终 API：
1. 将 `main.py:352-361` 的 Origin/Host 判定抽为纯函数（如 `is_same_origin(origin, host)`），便于单测与复用；不改路由契约。
2. `manage_account.py:66-117` 改为复用 `src.auth.reset_password` / `set_user_status`，消除 CLI 与 Web 的语义分叉。
3. 在 `main.py:918-926` 明确“是否允许重置自身 / 其他管理员”的策略（当前无此限制）。
4. 在 `config.py` 引入可选的可信 Host / Origin 允许列表，替代“仅比较 Host 头”，不改变现有端点。
5. `account_admission.reconcile`（`account_admission.py:229-246`）保持 operator-only，不暴露 HTTP。

## 6. 实际运行命令（全部只读）
- `cd <repo> && git status --short && git rev-parse --abbrev-ref HEAD && git rev-parse HEAD && git log -1 --oneline`
- `ls -la docs/audits/`、`test -e docs/audits/auth-current.md`
- `nl -ba <file> | sed -n 'A,Bp'` 读取 `auth.py`、`main.py`、`manage_account.py`、`create_admin.py`、`account_admission.py`、`account_leases.py`、`resource_access.py`、`session/manager.py`、`figure_router.py`、前端文件
- `grep -rl/-n` 检索 `account_auth_guard|citrus_auth|from src.auth|owner_user_id|set_user_status|...`
- `ls -la 01_src/web`、`ls -la 01_src/web/tests`、`find ... -iname '*test*'`
- 写报告：`mkdir -p docs/audits && cat > docs/audits/auth-current.md <<'REPORT_EOF' ...`

未执行：任何应用启动、pytest、DB 打开/迁移、真实账号/Cookie 操作、git commit/push、部署/重启。

## 7. 退出码 / 验收事实
- 所有 `remote_exec` 返回 `exit_code: 0`（只读命令）。
- 基线验收：`git status --short` 为空；branch=`feat/invite-auth-nature-figure`；HEAD=`efeb7edc...` 与工单一致。
- 文档前置校验：`docs/audits/auth-current.md` 审计前 `NOT_EXISTS`；`docs/` 目录审计前不存在。
- 写后：本文件为仓库内唯一新增文件（untracked），未产生其它改动。
- 未验证项：无运行期证据（按只读约束）。

## 8. 安全与兼容观察
- 强项：PBKDF2 310k + 随机盐 + 常数时间比较；令牌仅存摘要；停用删除会话；删除闭环有租约/触发器/跨进程 claim；删除后不可复活；管理员前缀集中鉴权；登录失败统一 401 不泄露账号存在性。
- Cookie：`Secure` 默认关闭，生产 HTTPS 必须显式设 `CITRUS_COOKIE_SECURE=1`，否则令牌可能明文传输。
- CSRF：无 token；依赖 `SameSite=Lax` + Origin/Host；反代若改写 `Host` 且不回传原始 Host，会把正常同源写请求误判 403（或反之削弱判定）。无 `TrustedHostMiddleware`。
- 限流：注册 8/小时/IP（`main.py:700`），登录 12/15 分钟/(IP|用户名)（`:714-716`）；`auth_events` 每次调用清理 24h 前记录（`auth.py:408`）。`_client_ip` 仅在 `CITRUS_TRUST_CLOUDFLARE_PROXY` 开启且来源在可信代理内才采信 `CF-Connecting-IP`（`main.py:649-664`）。
- CLI 风险点：`manage_account.py:78` 的 `active=1` 可绕过管理员停用意图（删除中账号受触发器阻止）；CLI reset/disable 均不撤销会话，`enable` 后旧会话可能恢复可用。
- 管理端：重置密码端点未禁止重置自身/其他管理员（`main.py:918-926`）；启停禁止自我停用（`:933-934`）。
- 多入口：三处 router 直接读 Cookie（4.3），属需要随主守卫策略同步维护的耦合点。

## 9. 适用回归（建议新增，未执行）
最小测试面：
- `01_src/src/auth_test.py`：注册/登录/令牌/停用/重置/限流/删除重试。
- `01_src/src/api/auth_guard_test.py`：Origin/Host、公共路径、管理员前缀、会话/任务归属、租约 409。
- 前端：在 `01_src/web/tests/` 增加 `auth-flow.test.cjs`（登录/注册/登出/401 跳转），复用现有 `.cjs` 风格。

## 10. AUTH-01 – AUTH-25 可验证入口
说明：以下“验证入口”为建议新增的断言/命令，本审计**均未执行**；路径:行均为静态证据位置。

1. AUTH-01 注册创建会话并返回令牌 —— `auth.py:196-235`、`main.py:702-705`；验证：POST register 后 `browser_sessions` 恰增 1 行且响应带 Cookie。
2. AUTH-02 注册即自动登录 —— `main.py:705`、`app.js:771,796`；验证：带注册返回 Cookie 调 `GET /api/v2/auth/me` 返回 200 且同一 user。
3. AUTH-03 Cookie 属性 —— `main.py:690-693`；验证：`Set-Cookie` 含 `HttpOnly; SameSite=Lax; Path=/; Max-Age=604800`，`Secure` 随环境。
4. AUTH-04 登录成功/失败 —— `auth.py:321-334`、`main.py:710-721`；验证：错误密码 401 统一文案；正确密码下发 Cookie。
5. AUTH-05 令牌解析含 active/过期 —— `auth.py:356-365`；验证：过期会话、`active=0` 均 401。
6. AUTH-06 登出撤销会话 —— `auth.py:347-353`、`main.py:724-729`；验证：登出后该 token 行删除、`/me` 401。
7. AUTH-07 require_admin —— `auth.py:400-401`、`main.py:324-331`；验证：普通用户 403，停用管理员 403。
8. AUTH-08 守卫公共路径 —— `main.py:349-351,362-365`；验证：未登录可调用 register/login/logout。
9. AUTH-09 CSRF：Origin/Host —— `main.py:352-361`；验证：带 Cookie 且无 Origin 的 POST 非公共路径 → 403；Origin netloc≠Host → 403。
10. AUTH-10 管理员前缀集中鉴权 —— `main.py:376-381`；验证：普通用户 `GET /api/v2/admin/users` → 403（先于路由）。
11. AUTH-11 会话归属集中校验 —— `main.py:383-388`、`manager.py:464-465`；验证：跨用户 session id → 404。
12. AUTH-12 任务归属 —— `main.py:389-398,2739-2746`；验证：访问他人 job → 404。
13. AUTH-13 非 GET 账号租约 —— `main.py:400-407`、`account_leases.py:142-149`、`account_admission.py:124-132`；验证：停用账号 POST → 409 `account_closed`。
14. AUTH-14 注册限流 —— `auth.py:404-415`、`main.py:700`；验证：1 小时内第 9 次 → 429。
15. AUTH-15 登录限流 —— `main.py:714-716`；验证：15 分钟内第 13 次 → 429。
16. AUTH-16 管理员停用删除会话 —— `auth.py:467-479`（`:476-477`）；验证：停用后该用户 `browser_sessions` 为 0，旧 token 401。
17. AUTH-17 删除后不可复活 —— `auth.py:473-475`、`account_admission.py:49-52`；验证：删除中 `set_user_status(True)` 返回 False / 触发器 ABORT。
18. AUTH-18 Web 重置撤销会话 —— `auth.py:500-509`（`:508`）、`main.py:918-926`；验证：重置后旧会话失效，临时密码可登录。
19. AUTH-19 管理员不可自我停用 —— `main.py:933-934`；验证：自我 disable → 400。
20. AUTH-20 CLI reset 差异 —— `manage_account.py:66-82`；验证：CLI 停用后再 CLI reset，账号 active=1 且旧会话未被删除（可复现分叉）。
21. AUTH-21 CLI create 差异 —— `manage_account.py:85-106`；验证：新建用户无 `browser_sessions` 行；admission 行由触发器生成。
22. AUTH-22 CLI disable 差异 —— `manage_account.py:109-117`；验证：CLI 停用后 `browser_sessions` 仍存在（Token 因 active=0 暂不可用）。
23. AUTH-23 admission schema/回填 —— `account_admission.py:26-60`；验证：旧库升级后 `account_admission` 行数 == `users` 行数，epoch 触发器存在。
24. AUTH-24 删除流程与删除重试令牌 —— `auth.py:566-577,580-740`、`main.py:842-868`、`auth.py:368-391`；验证：有在途任务 → 202 `deletion_pending`；重试令牌仅对 `DELETE /api/v2/auth/account` 生效。
25. AUTH-25 旧库/旧会话迁移集成点 —— `auth.py:133-159,512-524`、`manager.py:290,434-451`；验证：旧库升级保留用户、补列、首个管理员认领无主会话；其余无主会话不可访问。

## 11. 缺件 / 未执行（明确）
- 认证域无任何既有自动化测试（4 个 web 测试、7 个 Python 测试均不涉及）。
- 未启动应用、未运行 pytest、未对真实 DB 做迁移演练（工单禁止）。
- 未验证生产反代/Cloudflare 的 Host/Origin 实际透传（禁止读取线上配置）。
- 未验证 `CITRUS_COOKIE_SECURE` 在生产环境的取值。

## 12. 最多五个剩余点
1. CLI 与 Web 在 reset/create/disable 上的语义分叉需产品确认是否统一（AUTH-20/21/22）。
2. Origin/Host 判定依赖反代透传 Host，需在目标部署验证，否则可能误 403 或削弱 CSRF 防护。
3. 管理员重置密码未限制自我/他管理员，需确认策略（AUTH-18 相关）。
4. 旧库迁移仅代码级确认，未在真实 DB 演练（禁止读取真实 DB）。
5. 无认证回归套件，AUTH-01–25 需新建测试后方可标记通过。

## 13. 窄文件建议（若后续修复）
- 统一 CLI/Web：只改 `01_src/scripts/manage_account.py`（复用 `src/auth.py` 函数）。
- 加可信 Host/Origin 配置：`01_src/src/api/main.py:352-361` + `01_src/src/config.py`。
- 管理员重置保护：`01_src/src/api/main.py:918-926`。
- 测试：新增 `01_src/src/auth_test.py`、`01_src/src/api/auth_guard_test.py`（及可选 `01_src/web/tests/auth-flow.test.cjs`）。

## 14. 移交主会话 / A1
- A1 待决：①是否统一 CLI/Web 账号语义；②是否为管理员重置加自我/他管理员保护；③是否引入可信 Host/Origin 配置；④是否在 `Secure` 默认关闭的情况下要求生产显式开启。
- 建议 A1 在本分支先补最小回归（AUTH-01–25 对应断言），再做任何实现改动；按既定约束在远程服务器改动，最终服务以远程为准。
- 本审计未产生运行期证据，所有结论均为 `efeb7ed` 上的静态事实；未提交/未推送。
