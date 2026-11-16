# CitrusScholar 本地完整启动

在仓库根目录运行统一 Python 入口（PowerShell 或终端均可）：

```powershell
E:\anaconda\python.exe -B -X utf8 01_src/scripts/local_startup.py
```

若本机 PowerShell 策略允许执行该脚本，也可使用 `01_src/scripts/start_local.ps1` 包装入口。本机当前要求脚本签名，因此验收中没有运行该未签名包装；无需为 Python 入口修改执行策略。

默认使用 `E:\anaconda\python.exe`、WSL `Ubuntu-24.04` 和 `http://127.0.0.1:8010`。可以通过 `-Python`、`-Distribution`、`-Port` 指定现有环境。根目录旧 `start_local_anaconda.ps1` 是兼容入口；桌面预览的 `local-workspace` 也调用同一个 Python 启动器。

启动链是 Windows 主应用 → 指定 WSL 中的 Docker → 按作业创建的绘图容器。主应用与 embedding/reranker 继续在 Windows 中运行，使用现有 ONNX/DirectML 和 `agent/.hf_cache`；检索继续读取现有 `agent/data/active_index.json`，不会重新编码语料。

启动器唤起 WSL、检查 Docker。daemon 停止时尝试 `sudo -n systemctl start docker`，只使用已有的免交互授权；需要密码时明确失败，不改 sudo 配置。随后检查已有镜像，启动模型和检索预热，再实际创建合成图件、修改标题与字号、重放并导出 SVG/PNG/PDF，同时检查容器权限、网络和资源限制。必要能力未通过时应用启动失败；本地完整模式还要求两个模型实际使用 GPU，不能把 CPU 回退当作完整就绪。请先停止旧启动模式的本地实例，避免两个大型模型实例争用显存。`GET /ready` 返回 `ready=true`、`status=ready`、`worker.verified=true` 才表示完整就绪；它读取已保存的探测证据，不重复启动探测容器。

重复启动会核对工作区、解释器、PID/创建时间、监听端口、运行配置和完整就绪证据，再复用健康实例。端口属于旧启动模式、其他程序或不健康实例时会明确拒绝，不自动结束它们。Ctrl+C 或停止桌面预览结束主应用；不会关闭全局 WSL、Docker 或其他容器。

绘图没有必须手动保持运行的第二个终端。每个脚本使用一次性 CPU 容器；容器无网络、只读根文件系统、非 root 用户、移除 capabilities，并限制 CPU/内存/PID/时间。只挂载受管作业的只读输入和可写输出目录，不挂载应用源码、账号库、密钥或 Docker socket。Windows 路径通过 `wslpath` 双向核对，允许中文和空格；作业结束或取消后确认本作业容器清理，再删除 staging。若 daemon 故障导致清理无法确认，会使 worker 就绪证据失效并保留该作业目录，供恢复后清理，不虚报清理完成。

正常启动不下载模型、安装软件、拉取或重建镜像。镜像 worker 与当前编辑协议不兼容时，可以在 Docker 可用后显式运行：

```powershell
E:\anaconda\python.exe -B -I -X utf8 01_src/scripts/update_local_sandbox.py
```

该维护入口保留旧镜像标签，只把允许的绘图模块、共享纯审查方法和按来源哈希验证的 Nature 资源送入临时构建上下文，不包含账号数据、密钥或整个仓库。它会检查基础镜像的依赖版本；旧镜像不匹配时构建失败，不替换当前标签。需要安装固定科研依赖时显式加 `--install-table-engines`（包含 PyMuPDF、scipy/statsmodels 等完整固定依赖）。镜像变更后须重启主服务重新验证。

外部大模型和搜索 API 继续使用原有账号配置；本地启动器不能启动远端服务，也不将远端可用性冒充本地组件启动结果。此入口仅适用于 Windows 本地，不更改 Linux 服务器的 8030/8000 部署。

离线启动契约和原生 Docker/Apptainer 回归入口：

```powershell
E:\anaconda\python.exe -B -I -X utf8 engineering/product_evolution/run_checks.py --group local_startup
```

这组回归使用工程夹具和 mock，不等同于真实 Docker 验收。实际容器门禁在完整主服务启动时执行。
