# 启动与运维（本机 + 服务器）

> 更新：2026-10-30。两条命令即可，不必每次跑验收脚本。
> 所有命令均已实测通过（本机 8010 = 26 秒就绪；服务器 staging 8011 = HTTP 200）。

---

## 一、本机（Windows）

### 1. 启动

推荐（脚本已带端口检查、日志重定向、就绪轮询）：

```powershell
cd E:\codex_WORKSPACES\Citrus_QA_Agent
powershell -ExecutionPolicy Bypass -File .\启动_本地_8010.ps1
```

原始命令等价于：

```powershell
$Root = 'E:\codex_WORKSPACES\Citrus_QA_Agent'
$env:PYTHONUTF8      = '1'          # 必开：GBK 控制台打印中文会崩
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONPATH      = "$Root\01_源码_src"
$env:NO_PROXY        = '*'          # 必开：本机代理会拦 127.0.0.1

Start-Process -FilePath 'E:\anaconda\envs\rag-agent\python.exe' `
  -ArgumentList '-m','uvicorn','src.api.main:app','--host','127.0.0.1','--port','8010','--workers','1' `
  -WorkingDirectory "$Root\01_源码_src" `
  -RedirectStandardOutput "$Root\agent\logs\local_8010.log" `
  -RedirectStandardError  "$Root\agent\logs\local_8010.log.err" `
  -WindowStyle Hidden
```

- 地址：<http://127.0.0.1:8010/>
- 日志：`agent\logs\local_8010_<时间戳>.log`（运行期业务日志另在 `agent\logs\agent.log`）
- 首次加载检索模型约 **60–70 秒**；BM25 缓存命中时约 **26 秒**

### 2. 停止

```powershell
powershell -ExecutionPolicy Bypass -File .\停止_本地_8010.ps1
```

等价：

```powershell
Get-NetTCPConnection -LocalPort 8010 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { Stop-Process -Id $_ -Force }
```

### 3. 前置条件（缺一不可）

| 项 | 路径 | 说明 |
|---|---|---|
| Python | `E:\anaconda\envs\rag-agent\python.exe` | 已装 lancedb / fastembed / onnxruntime-directml |
| 代码 | `01_源码_src\src` | uvicorn 的 cwd 必须是 `01_源码_src`（导入根） |
| 语料 | `agent\data` | 新语料：`active_index.json` 指向 `g20261028T165637Z_851926a9`，126,943 块 / 5 批 |
| 模型缓存 | `agent\.hf_cache` | e5 编码器 + ONNX 重排器，缺失会尝试联网下载 |
| Key | `agent\.env` | DeepSeek / Serper 等；联网检索失败多为这里 |

---

## 二、服务器生产实例（端口 8000）

> 服务器 **没有 systemd 单元**，只能手工起；`kill` 之后不会自动拉起。

### 1. 启动

```bash
cd /home/WZQ/CitrusQA
setsid nohup ./04_脚本_scripts/run_server.sh \
  > agent/logs/server_$(date +%Y%m%d_%H%M%S).log 2>&1 < /dev/null & disown
```

`run_server.sh` 已内置：`PYTHONUTF8=1`、`TMPDIR`、`HF_HOME`、`HF_HUB_OFFLINE=1`、`FASTEMBED_CACHE_PATH`、`PYTHONPATH`、`--workers 1`。
端口/主机可覆盖：`CITRUSQA_PORT=8000 CITRUSQA_HOST=127.0.0.1`。

### 2. 停止 / 重启

```bash
# 停
kill -TERM $(ps -eo pid,cmd | grep "uvicorn src.api.main" | grep -v grep | awk '{print $1}')

# 起（同上面启动命令）
```

### 3. 健康检查

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/     # 期望 200
ps -eo pid,etime,cmd | grep "uvicorn src.api.main" | grep -v grep    # 看进程与运行时长
tail -f agent/logs/server_*.log                                      # 看启动/请求
```

### 4. 当前状态（2026-10-30）

- 生产代码 = `ff58b1f`（**S0–S5 的改动都还没部署**）
- 生产 `main.py` sha256 = `5f5f9e3f25ecc2e4b72fc11fa4376840f358128f0232c57ac390e3a8c2e9ab88`

---

## 三、服务器隔离实例（staging，端口 8011）

用途：跑**新代码 + 真实语料**做验收，绝不碰生产库。

### 1. 启动

```bash
cd /home/WZQ/staging_citrus
CITRUSQA_PORT=8011 setsid nohup ./04_脚本_scripts/run_server.sh \
  > staging_verify_8011.log 2>&1 < /dev/null & disown
```

### 2. 停止

```bash
kill -TERM $(ps -eo pid,cmd | grep "port 8011" | grep -v grep | awk '{print $1}')
```

### 3. 说明

- 目录：`/home/WZQ/staging_citrus`（8.8 G，自带 `.venv`、`agent/state`、新语料）
- 代码版本：与本地仓库 HEAD 逐字节一致（`main.py` = `639a353f…`、`app.js` = `b33f13be…`）
- 首次启动加载约 **60 秒**

---

## 四、在本机访问服务器（SSH 隧道）

```powershell
# 生产（本机 8000 → 服务器 8000）
ssh -N -p 28022 -L 8000:127.0.0.1:8000 wangziqiang@211.69.141.120

# 隔离实例（本机 8020 → 服务器 8011）
ssh -N -p 28022 -L 8020:127.0.0.1:8011 wangziqiang@211.69.141.120
```

- `-N` 只做转发、不执行命令，窗口会一直占着；关窗口即断开。
- 已经连上时，浏览器直接开 `http://127.0.0.1:8000/`（生产）或 `http://127.0.0.1:8020/`（staging）。
- 注意：**本机 8000 是隧道端口**，所以本机实例请用 8010，别用 8000，否则会"以为在测本地其实在测生产"。

---

## 五、常见故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `ModuleNotFoundError: No module named 'src'` | uvicorn 的 cwd 不是 `01_源码_src` | `-WorkingDirectory` / `cd` 到 `01_源码_src`（服务器用 `run_server.sh` 即可） |
| 启动后端口无响应，日志里在等 60 s | 正常：e5 + 重排模型加载 | 等；第二次起有 BM25 缓存会快 |
| 检索结果为空 / 日志出现 `quarantined corpus batch` | 语料还是旧契约（缺 `source/source_type/source_file`） | 换新语料：`agent\data\active_index.json` 应指向 `g20261028T165637Z_851926a9` |
| `网页搜索 | failed | auth` | 联网 key 缺失/失效（`agent\.env`） | 补 key；本地检索不受影响 |
| 端口被占用 | 旧进程没退 | 先跑停止脚本；或 `Get-NetTCPConnection -LocalPort 8010` 查 PID |
| PowerShell 报 `Missing closing '}'` / 中文乱码 | `.ps1` 存成了无 BOM 的 UTF-8（PS 5.1 按 GBK 读） | 脚本必须以 **UTF-8 with BOM** 保存 |

---

## 六、怎么确认"跑的是新代码 + 新语料"

```powershell
# 本机代码版本
cd E:\codex_WORKSPACES\Citrus_QA_Agent; git rev-parse --short HEAD
# 关键文件哈希（应与服务器 staging 一致）
Get-FileHash 01_源码_src\src\api\main.py, 01_源码_src\web\assets\app.js -Algorithm SHA256

# 语料版本
Get-Content agent\data\active_index.json -Raw    # chunk_count 应为 126943
```

```bash
# 服务器
sha256sum /home/WZQ/CitrusQA/01_源码_src/src/api/main.py      # 生产：5f5f9e3f…
sha256sum /home/WZQ/staging_citrus/01_源码_src/src/api/main.py # staging：639a353f…
```

---

## 七、归档区（旧的实验 / 旧语料 / 过时脚本）

2026-10-30 把"用不到但可能还要回溯"的东西集中到两处，**都不参与运行、都不入 git**：

| 位置 | 内容 | 规模 |
|---|---|---|
| `E:\codex_WORKSPACES\Citrus_QA_Agent\_backup_20261030\` | 旧实验单元、旧语料（187,441 块）、新语料 tar、DOCX 往返残渣、旧日志 | 3,521 文件 / 5.37 GB |
| `/home/WZQ/CitrusQA/_backup_20261030/`、`/home/WZQ/staging_citrus/_backup_20261030/` | 语料 tar、隔离实例历次探针日志、两棵旧源码树 | 1.0 GB + 180 MB |

清单、恢复步骤与校验值见 **`_backup_20261030\README.md`**。整个目录删掉不影响系统运行。
