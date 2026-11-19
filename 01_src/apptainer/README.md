# Linux / Apptainer 部署合同

状态：2026-11-18，已通过命令行 scp 上传并核对源码包，实际构建三个新 SIF。隔离 GPU Web 已通过严格 /ready、绘图六模式协议和 Code 实际执行/采集/整 cgroup 停止/移除。真实模型任务 在同 run 恢复至 generation 3 后完成，两份实际文件经浏览器鉴权下载/hash 校验，匿名读取均为 401；保留首轮 DNS 失败及一次确认停止后的失败执行记录。Search/Browse 任务 实际调用 deepseek_search 和 deepseek_fetch，均被已有远程独立凭据以 HTTP 401 拒绝，未取得搜索结果或网页正文，联网验收未通过。应用 SIF 空 resolv.conf 已通过只读宿主解析配置修复，未设置 VPN/代理或修改宿主网络。旧服务仍运行，正式切换与旧程序删除等待剩余验收。

## 拓扑

受信 Web 与独立 Code Worker 控制进程使用 application.sif；普通用户代码进入另一个固定 SHA-256 的 CPU sandbox.sif。Web 默认 GPU，管理员可在原后台选择 CPU/auto/GPU，选择保存在该 runtime 的 state/onnx_device.json，重启不由启动器覆盖。CPU 模式继续使用 GPU 发行中的 CPUExecutionProvider，不能额外安装 CPU onnxruntime/fastembed 覆盖 GPU 模块。

Web 使用现有 Deep Agents 单一主循环。Worker 不构建模型、不加载 dotenv/运行时模型凭据、不发布文件。Code 仍为 offline development 边界；不宣称公网任意代码安全准入。持久句柄是 apptainer-v1，绑定真实 systemd InvocationID/cgroup identity；unknown 不重放。

宿主 systemd 执行受控沙箱启动器。应用 SIF 的 Python 路径与宿主不同，必须显式配置 host_python；service_path 和 staging 在宿主与控制进程中使用同一个绝对路径。控制进程必须可观察宿主 /proc、只读 cgroup 与用户 D-Bus，因此外层受信 application.sif 不使用 --containall/--pid。内层不可信 sandbox 使用独立命名空间、只读根、无网络、去能力与资源限制，绝不继承 Web 的 --nv 或应用目录挂载。

对于已核对的远程主机，宿主直接 unshare 的 uid_map 写入被 AppArmor 拒绝。可选 namespace.sif 仅运行受信 service：宿主先验证 cgroup 限额和 namespace SIF 哈希，再正常使用 Apptainer --userns 进入控制镜像，随后 unshare 创建私有 user/mount namespace 和有界 tmpfs。仅绑定该 execution 的控制目录、受信 service、CPU SIF、宿主 Apptainer 及 /usr/libexec/apptainer、/etc/apptainer 和用户 D-Bus；这些挂载不进入内层用户代码。未配置 namespace SIF 时保留原宿主 unshare 路线，不自动降级或自动选择另一运行时。固定 helper 路径属于此 Linux 部署合同，其他发行版需要实测。

远程实际验证包括 systemd CPU/内存/PID 限额，内层独立网络/PID、零 capabilities、NoNewPrivs、只读根、有界 tmpfs，以及 namespace SIF → unshare CLI → 嵌套 CPU SIF → 实际输出采集 → 整 cgroup 停止和移除。挂载可能存在被覆盖记录，因此有效只读/nosuid/nodev/noexec 标志以 statvfs 核对，不能取 /proc/mounts 的第一条同路径记录。AppArmor 和宿主 DNS/sysctl/防火墙均未更改。固定工程探针不代替真实模型和浏览器成果闭环。

## 构建输入

在可信、独立的源码目录中构建。输入包括 `01_src/apptainer/`、`01_src/src/`、静态 Web、启动脚本、`02_config/`、`packages/`、`skills/`、发行元数据以及 `04_scripts/build_worker_bundle.py`。从已审查的服务源码快照准备构建树，随交付记录构建树的逐文件 SHA-256；如果修改过快照，则同时保存原快照标识和变更清单。使用 `LINUX-SOURCE-MANIFEST.json` 核对发布基线，它只描述生成该清单时的文件。模型、运行数据、数据库、日志、密钥、缓存和验收材料留在源码树之外。

应用及绘图 Worker 的受信输入均可从同一源码树获得。独立核查 Worker 发行包时，使用实际存在的构建脚本：

```bash
python -I 04_scripts/build_worker_bundle.py --output /absolute/new/location/figure-worker
```

如需上传源码，上传已核对清单的完整服务树至新的版本目录。以下定义的 `%files` 以该源码根为相对路径：

```bash
apptainer build --fakeroot /absolute/new/images/application.sif 01_src/apptainer/app.def
```

```bash
apptainer build --fakeroot /absolute/new/images/sandbox.sif 01_src/apptainer/sandbox.def
```

```bash
apptainer build --fakeroot /absolute/new/images/namespace.sif 01_src/apptainer/namespace.def
```

namespace.sif 仅含标准库 Python、systemd 客户端、util-linux 与 libseccomp 等受信控制依赖，不包含产品应用、用户脚本、模型或凭据。必须先实跑控制镜像与嵌套离线 CPU SIF 的完整生命周期，再允许该后端处理实际任务。

构建需要该机器实际允许 fakeroot；具有 subuid 条目不等于构建已经可用。缺 namespace/cgroup/运行时权限即保留阻塞，不使用宿主 Python 执行用户代码，不放宽隔离。构建期网络只用于下载发行依赖，和用户代码 offline 策略分开。

application.sif 实际安装 requirements-gpu.txt 与非 editable 支持 wheel，拒绝额外 CPU ORT/FastEmbed 发行。sandbox.sif 使用 requirements.sandbox.txt、字体与 build_worker_bundle.py，构建产物包含 Worker 成员清单和逐文件摘要。两者完成构建内 pip check、发行/资源检查；其 Python 基础镜像经官方 ECR 固定 linux/amd64 digest。namespace.sif 使用固定 Ubuntu 24.04 digest，匹配宿主 Apptainer/helper glibc 与 libfuse3.so.3 ABI，并检查完整标准库与挂载助手所需动态库。传递依赖仍非完整锁，必须记录实际 SIF SHA-256 和镜像内安装清单。

当前受信应用依赖固定于 application SIF，活跃入口来自只读绑定的版本源码；须同时记录上传基线与远程修订来源。Apptainer 的 /environment 为保留路径，Code 依赖 overlay 使用 /code-environment/site.zip，宿主持久协议和 OCI 路线不变。Web 单独只读绑定宿主现有 resolv.conf；Worker/用户 Code 不继承该挂载或 GPU。

## 隔离验收启动

复制 deployment.example.json 到运行区，填写真实绝对路径和实际 SIF SHA-256，不填写任何密钥。该远程主机的示例包含三个镜像；namespace_sif 与 namespace_sha256 必须成对配置。其他已验证允许宿主 unshare 的 Linux 环境可同时省略两项。Worker 启动器验证 namespace SIF；宿主 bootstrap 在 exec 前再次验证，错误或变动即拒绝。Web 不挂载该控制镜像。example 的占位哈希故意不能启动。figure_staging 要求当前服务账号所有、0700；code_staging 使用较短路径，Unix control.sock 总路径长度受内核限制。运行目录不能是源码目录或来源配置目录。

先新建隔离 runtime；仅复用授权的公共模型配置、语料、缓存，选择的模型 key 只由 Web 启动器在内存读取。不会复制账号/会话 DB，也不自动创建管理员。

```bash
python -I 01_src/scripts/apptainer_launch.py --config /absolute/runtime/deployment.json --role prepare
```

```bash
python -I 01_src/scripts/apptainer_launch.py --config /absolute/runtime/deployment.json --role worker
```

```bash
python -I 01_src/scripts/apptainer_launch.py --config /absolute/runtime/deployment.json --role web
```

长运行进程由独立 systemd 用户单元管理；ExecStart 是宿主 Python、该 host-visible 启动脚本、--config 与 --role，没有密钥。Web 的 --offline-code 要求该 runtime 的独立 Worker pulse 已存在；systemd After=只表示顺序，不能代替 pulse/readiness。用户单元需有明确工作目录与整个 cgroup 的停止策略。

/ready 要求检索、存储、实际模型推理和完整绘图生成/编辑/预览/v2输出协议。Code Worker 的 pulse 仅为 availability hint，必须另做真实模型、同 run 停车恢复、一次终答、授权成果读取与下载 hash 核验。Apptainer 真实 cgroup、独立网络/PID、磁盘边界、取消与崩溃对账仍需在隔离任务验证。AppArmor user namespace restriction 开启时，不能从 userns_clone=1 推断可执行。

## 切换和清理

先让隔离版本验证通过，再在无活动任务的切换窗口停止已确认身份的旧服务。正式运行可将 runtime_root 指向保留的原 runtime，existing_runtime=true；配置来源可与其相同。这是原地复用状态，不是账号/会话复制或手工数据库迁移。首次运行新源码的正常 schema 初始化仍需兼容性检查与状态备份，不能以隔离空库测试代替。

远程已核对存在两套需要保留的 data、模型缓存和状态根；清理前必须再次确认路径与 symlink。不能删除服务账号的整个主目录，也不能直接删除包含 agent/data、state/data 或账号状态的旧根。用户授权删除的是被替换的旧程序版本；新 SIF、新源码、现用运行区不能继续引用即将删除的旧源码、虚拟环境、SIF 或符号链接。

旧源码/虚拟环境/镜像只在新版本通过、旧服务确实停止、路径依赖排除之后逐项删除。无关实验和其他项目不属于此次替换。当前仅隔离验收入口运行；正式切换和旧程序删除尚未完成，没有提交或推送。

## 交互式 Browse（可选）

使用 unprivileged Linux 用户构建固定 Chromium 镜像，将本节命令中的输出路径替换为部署路径。宿主需要 Apptainer、用户级 systemd、cgroup v2 以及支持 `--network none` 的用户命名空间环境。

```bash
apptainer build playwright.sif docker://mcr.microsoft.com/playwright/python@sha256:38dd67fc654b56b3cba611e4c8eb71cd33750e1cd29eea2a5dc00b484817a75f
/usr/bin/python3.12 -m pip install --only-binary=:all: --no-deps --target browser-python -r browse-requirements.txt
sha256sum playwright.sif
```

将 `browse.example.json` 复制到服务配置目录，填写三个绝对路径、镜像的实际 SHA-256 和精确公网主机名。部署 JSON 的可选 `browse_config` 指向该配置。Web 只读挂载配置、镜像及 Python 依赖。省略 `browse_config` 时不暴露 Browse。可设置 `reuse_web_search_settings` 为 `false`，使候选不从源运行环境继承 Search/Fetch 设置。Browse 不依赖其密钥。

每个任务启用一个新 Apptainer 浏览器单元和独立上下文，最多三张标签页。单 Web 进程同时运行两个单元，每账号至多一个。不同账号最多四个请求可按先来顺序等待，每个最多二十秒。所有标签页共同计量该任务的请求、传输、动作和寿命。浏览器无直接网络接口，页面资源及下载通过应用侧 HTTPS 中继。当前 Chromium 启动方式依赖 Apptainer 的外部隔离，浏览器自身沙箱未启用。部署以单 Web 进程运行时，上述槽位才是实例级上限。
