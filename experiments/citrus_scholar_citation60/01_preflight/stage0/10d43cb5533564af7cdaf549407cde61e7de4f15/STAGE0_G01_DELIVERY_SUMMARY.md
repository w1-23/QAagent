# Stage 0 / G01 服务器隔离取证 — 实测结果（脱敏）

work_unit: `STAGE0_G01_SERVER_ISOLATION`
captured_utc: `2026-11-06T02:48Z`
reviewed_commit: `10d43cb5533564af7cdaf549407cde61e7de4f15`
reviewed_tree: `7c6ada3d7bd1bb09d96fc0c7ec5a6368488f50c8`
G01 status: **BLOCKED**

本文件只包含可公开的 L2 级衍生信息：路径、哈希、布尔结论、计数、报错类型。
不包含凭据、Cookie、账号口令、生产 `agent/.env` 内容、chunk 正文（L1）。

---

## 1. 源码获取（Gate 1）

| 项目 | 结果 |
|---|---|
| 传输方式 | 本地 `git bundle`（完整 44 提交历史）经 SSH 上传，**非** API tarball |
| bundle SHA-256 | `d54c7808c8da8fb11c36e7dd1805d3a7ab615689be7aaa6db6a8b7c291c2fed9` |
| bundle 校验 | `The bundle records a complete history` / 服务器端 SHA 与本地一致 |
| 检出方式 | `git init` + `git fetch <bundle>` + `checkout --detach` |
| HEAD | `10d43cb5533564af7cdaf549407cde61e7de4f15` ✅ |
| HEAD tree | `7c6ada3d7bd1bb09d96fc0c7ec5a6368488f50c8` ✅ |
| 父提交 | `994a44beff5ccdf60493252ac3aaeb7c7263c794` ✅ |
| 工作树 | `git status --porcelain` 为空 ✅ |
| remotes | **空**（无内嵌凭据）✅ |
| `git fsck` | PASS ✅ |
| GitHub API 独立复核 | commit / tree / parent / `main` 均一致 ✅ |
| `994a44b..HEAD` 差异 | 仅 `DSH_EXECUTION_HANDOFF_2026-11-06_994A44B.md`（+436/-0）✅ |
| 检出位置 | `/home/WZQ/citrus_experiment/checkout`，**不在**生产根内 ✅ |

---

## 2. 隔离实例（真实运行）

| 项目 | 实测 |
|---|---|
| 服务 | `uvicorn src.api.main:app`，绑定 **`127.0.0.1:8012`**（仅 loopback） |
| `/health` | `{"status":"ok","version":"1.0"}` HTTP 200 |
| Python | 独立 conda 环境 `3.12.16`（生产为 `3.12.15`） |
| 依赖 | 从生产 venv 精确克隆 site-packages（147 条 freeze），无网络安装漂移 |
| 独立数据库 | `checkout/agent/state/accounts.sqlite3`（实验自有） |
| 独立会话 | `checkout/agent/state/sessions.db` |
| 独立上传/产物/日志 | `citrus_experiment/{uploads,artifacts,logs}` + `checkout/agent/logs` |
| 真实账号 | 经 8012 HTTP 登录成功；`/api/v2/auth/me` 返回 `user_id=1, role=admin`（实验自有 DB） |
| 生产 8000 | pid 596114 未变、`/health` 200、`active_index.json` SHA 未变 |
| staging 8011 | pid 593930 未变、`/health` 200 |

运行时快照：Ubuntu 24.04.5 / kernel 6.11.0-17 / 384 vCPU / 125 GiB / 2× RTX 6000D。

---

## 3. 真实语料与检索（核心验收）

索引 generation：`g20261028T165637Z_851926a9`（与协议记录一致）
registry：`g20261028T165641Z_0f8b211e`；LanceDB 表 **5** 张；chunk 总数 **126943**。

**五个语料库 → 四个 corpus family**（概念不混淆）：

| 语料库 | 所属 family | chunk 数 |
|---|---|---:|
| `cqa_literature` | `literature` | 105997 |
| `cqa_translated` | `literature` | 18227 |
| `cqa_ucr_varieties` | `ucr_varieties` | 2475 |
| `cqa_deng_book` | `deng_variety_book` | 212 |
| `cqa_methods` | `experiment_methods` | 32 |

**真实检索实测**（`local_hybrid_search`，认证上下文，真实语料，未调用任何模型）：

| family | 命中 | 延迟 | status |
|---|---:|---:|---|
| literature | 6 | 23170 ms | ok |
| ucr_varieties | 4 | 10180 ms | ok |
| deng_variety_book | 2 | 6702 ms | ok |
| experiment_methods | 1 | 9808 ms | ok |

四个 family 全部有真实命中；检索走 BM25 + dense + RRF + rerank。检索文本按 L1 规则未落盘，仅记录计数、延迟与内容 SHA。

语料快照逐库 SHA 见 `runtime/index_source_manifest.txt`（如 `cqa_literature` `200340ef…2cdf58`，204752118 bytes）。

---

## 4. 工具与离线检查

| 检查 | 结果 |
|---|---|
| 注册工具数 | **17**（与 G01 模板 `registered_count=17` 一致） ✅ |
| 工具名 | 见 `runtime/tool_registration.json`（含 `local_hybrid_search`、6 个 `research` 导出工具等） |
| `00_repository_asset_audit.py` | **PASS**，blockers 空 ✅ |
| `REPOSITORY_AUDIT` | PASS（DETACHED、clean、fsck PASS、无凭据 remote） ✅ |
| `ASSET_INTEGRITY_REPORT` | PASS，314 个受管文件 ✅ |
| `compileall` | rc=0 ✅ |
| `pytest scripts` | **117 passed, 5 skipped**（与基线一致） ✅ |
| 5 个 skip 性质 | 全部为「protected frozen citation60 assets are unavailable / `CITRUS_EXPERIMENT_L1_ROOT` 未配置」——**不是 PASS** |
| `check_package.py` | PASS，blockers 空 ✅ |
| `audit_static_package.py` | PASS，314 文件已做密钥扫描 ✅ |
| `git diff --check` | clean ✅ |

---

## 5. G01 逐项状态

| G01 字段 | 结果 |
|---|---|
| separate_service / database / sessions / uploads / artifacts | ✅ True |
| 五语料可访问 | ✅ True（4 family 均有真实命中） |
| index generation 与来源 manifest | ✅ 已记录 |
| 17 工具注册 | ✅ |
| 日志去向 | ✅ 实验自有目录 |
| 依赖锁 / 服务配置 / 运行时快照 | ✅ |
| **production_write_denied** | ❌ **False** |
| **production_credentials_unreadable** | ❌ **False** |
| **os_identity_separation** | ❌ **NOT_ACHIEVED** |

**隔离不通过的根因（实测，非推测）**：实验进程与生产**同属 uid 1003**。
`test -w /home/WZQ/CitrusQA` → YES；`test -r /home/WZQ/CitrusQA/agent/.env` → YES。
已穷举并全部失败的加固途径：

- 无免密 `sudo`（`sudo -n true` 要求口令）
- `useradd` 需 root
- 非特权 userns 被禁：`kernel.apparmor_restrict_unprivileged_userns=1`
- `bwrap` / `unshare` 均报 `setting up uid map: Permission denied`
- 无 `podman` / `docker`，无容器 socket
- 无 setuid `newuidmap` / `newgidmap`
- `systemd-run --user` 不支持 `BindReadOnlyPaths` / `ProtectHome` 等命名空间属性

---

## 6. 论文资产盘点（关键缺口）

受控资产清单 `L1_CONTROLLED_ASSET_MANIFEST.json` 共 **16** 项，逐项核对字节与 SHA：

| 结果 | 数量 |
|---|---:|
| MATCH | **0** |
| NOT_PRESENT | **16** |

缺失项包括 `chunks_FROZEN.jsonl`、`questions_FROZEN.jsonl`、`question_authoring_packet.jsonl`、`chunk_manifest.csv`、`sampling_config.json`、`citation_run_plan.csv`、`question_validity_review_packet.csv` 等全部 Citation60 冻结链。
`CITRUS_EXPERIMENT_L1_ROOT` 未配置，服务器上无任何 L1 受控存储。

**QA60**：`qa60_CANDIDATE.csv` 存在且完整（60 行，L12/V10/W10/X12/P8/N8）；但 `qa60_LOCKED.csv` **不存在**，60/60 题仍为「待有效性认证」，双人和仲裁均未做。

**Pilot12**：`pilot12.csv` 与 `pilot12_v4_4_2.csv` 均存在（含 `allowed_corpus_family_ids` 字段）。

**Citation60 可重建性实测**（种子 `20261104`、300–3500 字符、每语料 12）：

- 两次独立重跑：**chunk 选择完全确定**（60 个 chunk_id 集合一致）
- 但整文件 SHA **不可复现**：`d9cc68d1…` / `d94e56f2…`，与清单 `87a20701…` 不符
- 字节差 184658 vs 184718 = 60 行 × 1 字节，与 `sampled_at_utc` 时间戳同长移位一致

结论：**chunk 集可重建，但不是原冻结文件**（新 SHA、需 change_log 与新版本冻结）；**60 道冻结题不可重建**（含人工出题决策）。

---

## 7. 阻塞与下一步

**G01 = BLOCKED**（5 项 blocker，见报告 `blockers`）：

1. 生产写入未被拒绝（同 uid）
2. 生产凭据可读（同 uid）
3. Citation60 L1 资产 0/16
4. QA60 未锁定、未双人认证
5. 模型/判官配置仍 `UNCONFIGURED`，绑定废弃提交 `187182b`

**启动 Stage 1 最少还需要**：

1. 用真正请求的专用低权限运行身份（需 root 建服务账号，或给 PI 申请受限 systemd 单元）解决 G01 隔离两项
2. 恢复或经审查重建 Citation60 的 60 chunk + 60 题（新版本 + change_log）
3. 完成 120 题双人有效性认证并锁定 `qa60_LOCKED.csv`
4. 轮换并配置 `CITRUS_AGENT_*` / `CITRUS_JUDGE_*` 到服务器 600 权限环境文件（不使用历史泄露 Key）
5. 由 PI/审查者批准 Stage 1 的 probe plan（不得直接跑仍绑 `187182b` 的旧计划）

---

## 8. 本单元未越界事项

未调用任何真实付费模型；未执行 Stage 1 / Stage 2 / Pilot / 正式实验；未修改生产 8000、
未重启生产、未改生产数据库/会话/上传/缓存/日志；未改 staging 8011；
未使用历史泄露 API Key；未伪造任何 PASS；未把模拟数据当真实证据。
