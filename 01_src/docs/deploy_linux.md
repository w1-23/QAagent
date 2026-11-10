# CitrusQA Linux 部署

此部署方案先在现有 Linux/NVIDIA 主机上验证。网页服务、Agent、RAG 索引与模型运行在服务器；用户只需浏览器。每个账号有独立会话、输出目录和下载权限。管理员统一配置模型与联网 Key。

## v15 架构与权限说明

服务保持模块化单体，由一个 General Agent 调用中央注册表中的 17 个工具。明确的检索要求进入必需回执检查；自动路由建议采用 `advisory` 策略，不会仅因问题出现“论文、基因、实验”等关键词就强制重复检索。设置 `CITRUS_RETRIEVAL_ROUTER_MODE=enforce` 可临时恢复旧的强制门控行为。

服务以活动索引注册表作为唯一语料允许列表。当前五个清洗后的共享公开语料（`cqa_literature`、`cqa_methods`、`cqa_deng_book`、`cqa_translated`、`cqa_ucr_varieties`）对任意已登录用户开放；未登记、空注册表、声明冲突或非公开级别一律拒绝，不再把未知语料默认成 public。BM25、Dense/HyDE、多查询、缓存、原文阅读和来源链接共用同一 `CorpusAccessScope`，缓存键同时绑定注册表版本、索引 generation 和允许语料集合。部署前必须核对活动 registry 中五个语料均具备 batch/table 映射；只有 `corpus_access` 而没有语料映射的注册表会按设计失败关闭。

上传文件、课题记录和生成成果保持账号/会话所有权校验，包括预览、原文和下载入口；管理员角色不会跨越私人资料所有权。`/api/v2/corpus/source` 未登录返回 401，未登记或不可用记录统一返回不泄露存在性的 404。

数据审查在原文件副本上生成标注 Excel：含标色和批注、问题清单、审查概览、规则适用说明及源文件 SHA-256。原文件不覆盖，规则命中仅作为人工复核线索。PaperConan 适配器及 vendor 运行副本不再作为服务依赖；来源和借鉴范围见 `src/research/PAPERCONAN_ATTRIBUTION.md`。

内部科研任务结果根据真实工具终态、未满足的明确要求和已登记的校验产物汇总，执行状态与任务验收状态分开。未知工具状态按失败处理；“执行成功但没有结果”不等于找到证据或生成了文件。生成产物采用不可变版本并在下载前核对会话所有权、文件存在、格式和 SHA-256。`artifacts` 表只做加列、加索引和回填的幂等迁移，不删除既有会话、附件、产物或运行数据。

## 首次上线顺序

1. 管理员账号负责安装缺失的系统 Python 组件和配置系统服务；普通 Linux 账号 `wangziqiang` 运行应用。现有 Anaconda 为该账号所有，应用环境单独放在数据盘项目目录。
2. 项目统一部署在 `/home/WZQ/CitrusQA/`：`01_src/` 是代码和网页、`02_config/` 是配置、`agent/` 保存语料/模型缓存/状态/日志/用户文件、`.venv/` 是 Python 3.12 环境，`tmp/` 是临时空间。启动前确认 `/home` 挂载在数据盘。
3. 应用只绑定 `127.0.0.1:8000`。公开注册始终创建普通账号；管理员必须通过私下 CLI 初始化。先 SSH 登录普通账号并运行 `/home/WZQ/CitrusQA/.venv/bin/python /home/WZQ/CitrusQA/01_src/scripts/create_admin.py`。若该用户名已注册，脚本会要求输入该账号当前密码，并在尚无管理员时将原账号升级为首个管理员；否则会创建新的管理员账号。若管理员已存在，脚本会拒绝升级。此操作不会重置密码或清除会话。
4. 管理员登录后配置主模型和联网服务 Key；Key 保存在服务器 `agent/state/`，不进 GitHub。管理员面板可管理账号、每日限额、查看全局队列和请求审计记录。
5. 默认全局最多同时处理 10 个聊天请求，其他请求按 FIFO 排队；等待队列上限 100，单次最长等 15 分钟。审计保存问题、回答、工具名/状态摘要及附件元信息，保留 365 天；不保存附件原文或模型隐藏推理。记录不随用户删除会话而删除。
6. 购买域名后将 DNS 指向服务器，在 Nginx 或 Caddy 配置 HTTPS，并启用 `CITRUS_COOKIE_SECURE=1`。公网只开放 80/443；不要先开放应用端口或公网注册。

## 主机资源建议

现有机器有 96 核、128 GB 内存和两张 RTX PRO 6000D（每张显存单独为 84 GB）。生产环境已确认 `onnxruntime-gpu 1.21.1` 与 CUDA 12/cuDNN 9 运行库可用，ONNX Runtime 可见 `TensorrtExecutionProvider`、`CUDAExecutionProvider` 和 `CPUExecutionProvider`。代码与当前模型缓存、文献数据另需 CMake/索引增长、用户上传和备份空间。不要把 GPU 数量误当作共享显存。

保持单 Uvicorn worker，使活跃请求共享同一服务进程中的检索模型实例，不会每个请求加载一套模型。聊天并发上限 10，额外请求排队。

## Python 与 ONNX 推理设备

- CPU 环境：`python -m pip install -r requirements.txt`
- GPU 环境：`python -m pip install -r requirements-gpu.txt`
- 两份清单共享 `requirements-base.txt`。CPU 的 `onnxruntime` 与 GPU 的 `onnxruntime-gpu` 不要同时安装到同一个虚拟环境。
- 启动日志会记录 `onnxruntime.get_available_providers()`；若 GPU Provider 不可用，`auto` 会按优先级回退，`gpu` 明确切换会返回冲突错误。

管理员登录后进入 `管理后台 → 推理设备`，选择 `auto`、`gpu` 或 `cpu` 并点击“应用”。接口为：

```text
GET /api/v2/admin/onnx/device
PUT /api/v2/admin/onnx/device   {"mode":"auto" | "gpu" | "cpu"}
```

设置持久化到 `agent/state/onnx_device.json`。切换时已有请求不中断；Embedder 和 Reranker 的现有 ONNX 会话被丢弃，下一次嵌入或重排请求按新模式重建，通常有 1–2 秒预热。GPU 显存不足或 Provider 初始化失败时，请求路径仍回退到 CPU。

## 进程与公网入口

当前服务由普通账号临时启动，systemd 服务尚未安装。要启用开机自启，请管理员账号执行：

```bash
sudo cp /home/WZQ/CitrusQA/01_src/scripts/citrusqa.service /etc/systemd/system/citrusqa.service
sudo cp /home/WZQ/CitrusQA/01_src/scripts/citrusqa-tunnel.service /etc/systemd/system/citrusqa-tunnel.service
sudo systemctl daemon-reload
sudo systemctl enable --now citrusqa
sudo systemctl status citrusqa --no-pager
```

系统服务以 `wangziqiang` 身份运行，监听 `127.0.0.1:8000`，只信任来自本机 Tunnel 的转发头，并为公网 HTTPS 设置安全 Cookie。首次启用时如果临时服务仍占用 8000 端口，先用 `sudo ss -ltnp 'sport = :8000'` 确认 PID，再停止已确认的 CitrusQA 旧进程，然后启动 systemd 服务。

域名注册并接入 Cloudflare 后，在 Cloudflare 控制台创建**命名 Tunnel**，将公开主机名指向 `http://127.0.0.1:8000`。在服务器下载 `cloudflared` 到数据盘 `/home/WZQ/CitrusQA/04_scripts/cloudflared` 并赋予执行权限。将控制台给出的 Tunnel Token 写入 `/home/WZQ/CitrusQA/agent/state/cloudflare-tunnel.env`，格式为 `TUNNEL_TOKEN=...`，文件权限设为 `600`、目录权限 `700`；不要把 Token 发到聊天、命令历史或 Git。

先完成学校网络/信息化部门审批及适用的网站备案和 AI 服务要求，再启用公网 Tunnel：`sudo systemctl enable --now citrusqa-tunnel`。Quick Tunnel 不适用于本服务的流式回答。公网自助注册会在 Tunnel 可达后开放；管理员账号必须先通过上面的 CLI 脚本建立。

Cloudflare Tunnel 提供 HTTPS 入口并转发到本机 Uvicorn。公开链接启用前须确认校园网络允许该 Tunnel 和公众服务；无需对公网开放 Uvicorn 端口。将来迁移到学校子域名时，重新配置同一 Tunnel 的公开主机名并完成外部访问验收。

## 多用户数据与维护

- 账号数据使用 SQLite，密码为 PBKDF2 哈希；无需收集邮箱。管理员能停用账号、重置临时密码和调整普通账号每日请求上限。默认上限为每人每天 30 轮，管理员不受该上限限制。
- 20 个柑橘头像预设作为静态前端资源；自定义头像经内容校验和尺寸压缩后保存在账号 SQLite 记录中。头像读取需要登录且按当前账号授权，删除账号时随账号记录删除。首次登录引导的完成状态也保存在账号记录中。
- 会话和生成文件按账号隔离；聊天内容和附件可能发送给管理员配置的模型及联网服务。上线前应在首页/隐私说明中告知用户，并用 HTTPS 保护传输。
- 附件只在当前会话处理，不加入共享语料。导出文件有期限清理；定期备份 `state/`、本地语料和模型配置，备份同样需要加密并限制管理员访问。
- 首期没有邮箱验证和自助找回密码；用户忘记密码时由管理员重置，并通过安全渠道交付一次性临时密码。
- 上线前验证两个普通账号互相访问对方会话、任务状态、引用、工作区文件和下载链接均被拒绝；确认普通账号无法访问管理设置。

## 当前已核实的运行状态（2026-11-03）

生产 CitrusQA 位于 `/home/WZQ/CitrusQA/`，当前进程在 `211.69.141.120:8000` 运行；隔离 staging 位于 `/home/WZQ/staging_citrus/`，端口 `8011`。两端均为 Python 3.12、单 Uvicorn worker，SSH 本机 `/health` 检查返回 200。生产和 staging 均已有部署数据、会话与资料，运维更新不得按“空库首次部署”处理。

本次代码实施前发现两端的 API 文件哈希及提示词集合指纹不一致，部署标记不能单独证明所有运行文件相同。新的服务候选必须先在 staging 启动并验收，再按文件清单备份和更新生产；配置、密钥、数据库、会话、语料、模型缓存和既有备份留在原位置。发布前使用 SQLite 在线备份 API 备份 `agent/state/sessions.db` 与 `agent/state/research_artifacts.sqlite3`，记录备份路径、数据库完整性结果、代码提交、关键文件 SHA-256 和回滚副本。对公网入口、TLS/代理和 systemd 当前状态应以主机现场核查为准；不要将历史首次部署段落当成当前公网状态证明。不要在聊天中发送服务器密码、私钥、API Key 或 Tunnel Token。
