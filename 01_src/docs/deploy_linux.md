# CitrusScholar Linux 部署、S6 发布与恢复

本文是 **8030 新应用的操作手册**。发布工具更改在本地做合成测试；父线程在用户调整权限后已恢复服务器 schema 只读核查，后续环境准备和部署以独立现场记录为准。用户授权先尝试 Apptainer，不可行时使用 systemd + 独立 Conda GPU 环境；本文的待执行命令不代表已经完成部署，也不授权改变旧 8000 应用。

## 科研工作区分支的新增部署要求

`feat/research-workspace` 新增脚本资产存储和 Nature 绘图检查；下文记录的历史 CPU SIF 不能据此视为兼容新 worker。应用和科研沙箱必须成套更新：用本分支 `Dockerfile.sandbox` 与 `requirements.sandbox.txt` 构建，包含固定的 Nature 2.8.0 核心（提交 `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`）、PyMuPDF、SciPy/Statsmodels 和共享纯审查方法。Apptainer 部署也需提供同等内容并独立验收，不能沿用旧镜像摘要冒充更新后的环境。

宿主会拒收缺少匹配 Nature 回执或 PDF 摘要不符的脚本图件。必须在实际执行后端验证创建、多输出、编辑重放及检查失败语义；只安装宿主依赖或读取技能说明不代表沙箱就绪。当前本机 WSL 的证据不等于远程 Linux/Apptainer 已验证。本分支尚未部署到远程，本文不授权重启或替换现有服务。

备份、恢复和账号删除范围需包含应用数据根内的 `state/research_scripts.sqlite3`，以及原有图件、内部数据与待答任务存储。任务脚本闲置期限由 `RESEARCH_SCRIPT_IDLE_DAYS` 配置，默认 7 天；依赖保护和正在执行的任务优先于期限清理。模型 GPU 默认与管理员 CPU/GPU 切换策略保持原有配置。

## 1. 当前边界与路径

| 用途 | 当前记录 | 本次操作边界 |
| --- | --- | --- |
| 服务器账号 | `wangziqiang`，home `/home/WZQ`；SSH `211.69.141.120:28022` | 本地工具不使用 SSH、不传输文件 |
| 新应用 | `/home/WZQ/citrusqa/src`，`citrusqa-next.service`，`127.0.0.1:8030` | 唯一可规划更新的服务 |
| 新应用数据根 | `/home/WZQ/citrusqa/state`，由源码根下的 `agent` 链接访问 | 使用最新 8030 数据，不能以旧 8000 快照替换 |
| 当前工作目录 | `/home/WZQ/citrusqa/src/01_src` | 现场检查有效 unit、drop-in、进程 cwd |
| 当前启动命令 | `/home/WZQ/citrusqa/venv/bin/python -m uvicorn src.api.main:app --host 127.0.0.1 --port 8030 --proxy-headers --forwarded-allow-ips 127.0.0.1 --workers 1` | 仅在确认过的维护窗口改变 |
| GPU 应用镜像 | `/home/WZQ/CitrusQA/container/citrusqa-cuda.sif` | SHA-256 `7a826f3146b249d9f7634655369d4ca9d51bc31e20ae3b3de8f912e1997d6935`；镜像 Python `/usr/local/bin/python` |
| CPU 绘图镜像 | `/home/WZQ/CitrusQA/container/citrusqa.sif` | 固定 SHA-256 `268295ac77d715a44c6867292f7a6b07d41b27fbb9a48aaa654d8e0c63900551` |
| 旧应用 | `/home/WZQ/CitrusQA`，8000 | 保持原进程、数据、unit、入口不动；上述两个 SIF 只读使用 |

现有代码以 `<release>/agent` 作为持久数据根。不可在新源码旁创建空 `agent` 再启动，否则账号、密钥、会话和附件会看似消失。发布工具在确认新 release 不含 `agent` 后，才把它链接到原有 8030 数据根。`state` 数据根内部实际布局必须现场清点，不能仅凭目录名字推断 DB 在根目录还是 `state/` 子目录。

## 2. 工具与默认行为

工具位于 `engineering/product_evolution/release/`，均为 Python 标准库程序。

| 文件 | 职责 |
| --- | --- |
| `release.py` | 当前工作树打包、清单/哈希校验、安全 staging、预检、全量数据备份、隔离恢复演练、schema 检查、保留策略 |
| `switch_service.py` | 保存原有服务配置和源码指纹，准备/应用仅影响 8030 的 drop-in；按 schema 门控回滚代码和配置 |
| `launch_gpu.py` | 从私有 profile 生成显式 Apptainer/Conda argv 和统一严格环境；默认打印计划，`--execute` 才前台运行服务 |
| `gpu_entry.py` | 两种模式共用入口；验证真实 ORT CUDA 数值/计算 kernel、CPU 绘图隔离、已加载模型前向，然后在同一进程启动 Uvicorn |
| `host-profile.example.json` | 主机配置模板，未知路径故意保留占位符 |
| `fixture-review.json` | 单个预览脱敏测试中的合成凭据字面量的精确文件哈希豁免；文件变化即失效 |
| `test_release.py` | 仅使用临时目录和合成 SQLite/文件的本地测试 |

`bundle`、`stage`、`backup`、`restore-drill`、`prune`、`configure`、`rollback` 默认 dry-run，必须额外传 `--apply` 才写本机文件。没有任何工具会执行 `systemctl stop/start/restart/daemon-reload`；备份和配置应用只读取服务状态，要求服务已经由操作者明确停止。禁止为了绕过预检而改 profile 的服务名、端口、路径或关闭 GPU/绘图隔离要求。

本地验证命令（不导入应用、不运行模型、不读生产数据）为：

```bash
python -I engineering/product_evolution/release/test_release.py
```

测试覆盖源码脏修改、归档路径穿越/链接拒绝、文件损坏、多个 SQLite、WAL 自包含备份、外键错误、引用字节、隔离恢复、配额/保留、服务边界变化、schema 门控和保留新数据的代码回滚。Windows 上完整配置事务测试模拟 Linux 的 `agent` 符号链接、Apptainer argv 及主机预检；实际写入/恢复临时 drop-in。真实 Linux systemd、Linux `renameat2(RENAME_NOREPLACE)` 发布分支、Apptainer 嵌套、GPU 和生产恢复并未由这些测试证明；Linux 缺少该原子不覆盖操作时工具失败关闭。

## 3. 源码发布包：保留当前未提交修改

不用 `git archive HEAD`：它会遗漏当前未提交修改。builder 从当前文件系统读取 `01_src/`、`02_config/`、`engineering/product_evolution/` 中的源码/文档/配置（含验收说明和 runner，排除 results、缓存及预览二进制）及根 `README.md`/`.gitignore` 和三份 `requirements*.txt`，记录每个文件的 SHA-256、长度、模式、release ID、构建时间及参考 HEAD。工作树其他实验、历史结果和评测目录不进入运行包。

排除 `.git`、所有 `.env*`、密钥文件、凭据/secret 文件、`agent`、`state`、`workspace`、embedding/models/data、results/runtime、缓存、日志、虚拟环境、依赖目录和 SIF。配置中的非空凭据和常见字面量 Key 会使构建失败；此扫描不替代人工审核清单和配置。只可对确认是合成测试字面量的文件以**精确 SHA-256 + 原因**豁免，不能用目录级豁免纳入真实密钥。运行配置和模型仍在服务器的原位置，依赖安装不随源码包执行。

在父任务确认代码稳定后，在当前工作目录内、已排除的 `engineering/product_evolution/results/releases/<唯一版本>/` 新目录构建归档；不要在工作目录之外写文件。以下示例**没有传 schema compatibility，因此只得到可审核源码包**：

```bash
python -I engineering/product_evolution/release/release.py bundle \
  --root E:/codex_WORKSPACES/CitrusQA_Local \
  --output E:/codex_WORKSPACES/CitrusQA_Local/engineering/product_evolution/results/releases/s6-REPLACE_VERSION/source.tar.gz \
  --release-id s6-REPLACE_VERSION \
  --secret-review engineering/product_evolution/release/fixture-review.json
# 审核 dry-run 后，按相同参数追加 --apply。
```

归档内 `MANIFEST.json` 的 `deployment_status=BLOCKED`、`blocked_apply=true`、`rollback_compatibility_reviewed=false` 明确禁止应用；`rollback-compatibility.json` 的数据库映射为空，并说明缺少当前 8030 schema 审核。`switch_service.py` 拒绝该包。归档旁的 `.manifest.json` 是便于本地审阅的同一份完整清单，`.sha256.json` 记录归档和清单哈希并在成功发布后最后生成；三份文件都不能覆盖。此包没有生产备份，也不能证明回滚兼容。归档和清单不得宣称已签名。

将来取得维护窗口内的完整 schema 清单，并经维护者确认前后版本兼容后，才使用**新的 release ID**和 `--compatibility /path/to/reviewed-compatibility.json` 重新构建。不能原地修改归档/清单或覆盖旧 release 来解除阻塞。compatibility 格式是 `{"format":"citrusschalor-s6-v1","databases":{"runtime/state/sessions.db":["64位schema哈希"]}}`，必须覆盖每个当前数据库，不只是 sessions/artifacts。它记录 `sqlite_master` 和 `PRAGMA user_version` 的哈希；列出哈希代表维护者已审核该版本代码能使用这个 schema，工具不会从“结构相同”推断业务语义兼容。

归档验证需要使用独立记录的可信 SHA-256。校验不把可被一同替换的清单视作签名。staging 只接受普通文件，拒绝软/硬链接、特殊文件、重复/大小写冲突名字、绝对路径、反斜杠、驱动器路径和 `..`；同时限制成员数和解压总量。先在新的私有临时目录解压并复核字节，再发布为全新的 release 路径；不覆盖已有 release。脚本始终从受信任工具目录启动，使用 `python -I`，不要从收到的压缩包目录启动解释器。

## 4. 未来主机预检：先补齐输入

以下均为**操作者在主机上未来执行的步骤**，本次未执行。先把经过本地审核的工具放在独立的 `/home/WZQ/citrusqa/ops/`，把 profile 复制为该目录下私有 `host-profile.json`（目录 700，文件 600），逐项填入已检查的值：

- 有效 `systemctl --user cat/show citrusqa-next.service`、真实进程 cwd/argv、8030 listener；同时记下 8000 状态，用于变更后对比。不要把环境变量中的 Key 打到聊天或日志。
- `src/agent` 最终解析到的 8030 数据根，及该根下全部 SQLite、配置、上传、附件、会话、导出、图件项目、revision、Word 草稿、完整性审查和审计数据。每个根路径互不重叠。把完整的 `root名/DB相对路径` 列入非空 `expected_databases`，预检和备份遇到新增/丢失 DB 都要求重新审核；常见 `.db/.sqlite/.sqlite3` 文件即使文件头损坏也不能当普通文件跳过。若引用文件在根之外，必须把实际目录作为额外命名 root；不能仅备份 DB 而遗漏字节。
- 现有模型缓存真实位置，包括 FastEmbed、tokenizer、reranker ONNX 与附加权重文件。填写 `model_cache_root` 和非空 `required_cache_files`（相对路径逐个列出）。不猜缓存、不下载权重、不生成/重建 embedding、不复制历史评测输出。若缓存布局与代码要求不同，先形成经审核的路径方案；不要挪动在线缓存试错。
- CUDA SIF 实际 SHA-256、镜像内 Python 的绝对路径；实际宿主 Apptainer 及镜像内 CPU 绘图调用所需的可执行文件、配置、helper、cgroup/user-bus 路径。
- `extra_binds` 必须是现场确认过的最小同路径绑定 `{ "path": "/实际路径", "mode": "ro或rw" }`。宿主 Apptainer 并不因为容器能看到其可执行文件就一定可运行；不要猜测库目录或整棵绑定 `/usr` 覆盖 CUDA SIF 环境。
- 检查容量，按实际数据量设置备份总配额和最低剩余空间。模板仅给出 100 GiB 总配额、10 GiB 空闲保留的初始数值，不能作为容量验收结果。

预检只验证路径/哈希/SQLite/文件引用，不执行模型或服务：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py preflight \
  --profile /home/WZQ/citrusqa/ops/host-profile.json
```

`roots` 默认完整备份指定目录，**不默默跳过缓存或数据库**。如果所选持久根混有巨大的可重建模型/embedding，应先确认完整持久目录映射，再列出不重叠的具体 roots；`runtime` root 必须仍是实际 `agent` 数据根，当前工具不支持靠排除规则挖空这个根。遇到内部符号链接会停止，应记录并审核实际布局后完善根映射/工具，不可用忽略链接参数丢文件。

路径字段、JSON 中的路径键会被解析并映射到备份 root；相对路径必须填写现场确认的 `relative_reference_base`。已被保留策略删除的历史文件可用 `missing_reference_waivers` 精确原路径加原因记录为缺失，豁免会进入备份清单。发现其他外部路径、丢失字节、未知引用格式时应停止检查。此解析覆盖显式 path/dir 字段和嵌套 JSON，不能证明所有任意自然语言字段里都没有文件引用；完整 root 的字节备份仍是基础。

若 DB 仍保存旧绝对路径，而经现场核对的**相同字节副本已存在于当前 8030 root 内**，可在私有 profile 中逐个声明精确 `reference_aliases`，不能扩大 `roots` 收养旧 8000 目录：

```json
{
  "reference_aliases": {
    "/home/WZQ/CitrusQA/agent/workspace/example/report.pdf": {
      "path": "/home/WZQ/citrusqa/state/workspace/example/report.pdf",
      "sha256": "REPLACE_WITH_BOTH_FILES_MEASURED_SHA256",
      "reason": "现场双边哈希核实相同的当前8030副本；保留原DB路径等待单独审核迁移"
    }
  }
}
```

alias 的原文件和目标都必须存在、是普通单链接文件且任何路径组件均非 symlink/junction，双边哈希必须等于提供的 64 位小写 SHA-256。目标只能位于唯一、已经登记的 8030 backup root；拒绝目录、硬链接、缺失文件/哈希、错误哈希、无原因和外部目标。只按 DB 字段原值精确匹配，不推测前缀、不搜索同名文件，也不隐式复制旧文件或改写线上 DB。`load_roots` 对旧 `/home/WZQ/CitrusQA` 数据的禁止规则不变。

预检/备份记录 alias 数量；备份在发布目录前再次读取双边文件核验，原文件在复制期间改变也会拒绝整份备份。`BACKUP.json` 的 `reference_aliases` 保存原路径、8030 `backup_path`、测得哈希及说明，各条引用保留 `original` 和 alias 证明。实际备份仍只复制现有 roots。`verify-backup` 与隔离恢复仅用清单哈希核对**快照中的目标真实字节**，不读取或依赖在线原文件/目标；DB 原路径保持原样，演练不启动应用。DB 路径迁移由操作者另行审核、留备份后执行，完成后应移除已不再需要的私有 alias，解除后续备份前对旧文件的双边核对依赖。

## 5. GPU 应用与 CPU 不可信绘图分别验收

`runtime_mode` 默认 `apptainer`，以既有 `citrusqa-cuda.sif` 加 `--nv` 启动；允许显式设为 `conda`，未知值一律拒绝，程序不会自动改模式。两种模式均验证已审阅 manifest、SIF 哈希及原有 8030 `agent` 数据根，并强制 `CITRUS_REQUIRE_GPU=1`、`CITRUS_ONNX_DEVICE=gpu`、`CITRUS_REQUIRE_FIGURE_WORKER=1`，保持单 worker。`launch_gpu.py` 默认打印 argv；`--execute` 才启动服务。

现场已确认宿主 `/usr/bin/apptainer` 可用，CUDA SIF 的 tensor 结果为 13，但镜像没有 `apptainer`/`systemctl`，无法直接满足嵌套 CPU worker 条件。因此按用户已授权的备选方案选择**显式 `runtime_mode=conda`**，由父线程准备新的独立 GPU 环境；模板仍保留默认 Apptainer，不隐式切换。`conda_python` 必须指向新 prefix 的 `bin/python`，不能使用或改动旧 8000 的 `/home/WZQ/CitrusQA/.venv`。该旧路径虽名为 `.venv`，实际是登记的 Conda 环境；其 CPU Torch 不是判断 ORT GPU 能力的依据。

Conda 校验要求真实且非空的 `conda-meta/history`、Python 包元数据、没有 `pyvenv.cfg`，并以清理环境的 `conda_python -I` 查询实际 `sys.prefix`、`sys.base_prefix`、`sys.executable`。三者必须与独立 prefix/解释器一致，不能给 venv 加一个空 marker 冒充 Conda。dry-run 会执行这次有限的解释器身份查询，不加载应用或模型。通过后 argv 为 `conda_python -I <release>/engineering/product_evolution/release/gpu_entry.py --source <release>/01_src`，CPU worker 仍由宿主 Apptainer 运行。

两种模式使用同一严格环境生成器。清除继承的 `APPTAINER*`、`SINGULARITY*`、旧 `CONDA*`、`VIRTUAL_ENV`、`LD_PRELOAD`、`LD_LIBRARY_PATH`、`LD_AUDIT`、`PYTHONPATH`、`PYTHONHOME` 等污染；覆盖 GPU/offline/worker 设置并禁止 local sandbox。Conda 的 PATH/CONDA_PREFIX 只在身份核实后指向新环境。CUDA 库使用已安装的 nvidia wheel：ORT 提供可调用的 `preload_dlls` 时，入口以 `directory=""` 调用公开 API，随后仍必须通过真实计算验证；不从父 shell 的任意 loader 路径找库。

应用的 embedding/reranker 推理栈是 **ONNX Runtime GPU 1.21.1 + CUDA 12/cuDNN 9**，不要求 CUDA 版 PyTorch。`gpu_entry.py` 必须显式通过 `probe_provider("CUDAExecutionProvider")`：真实 Gemm 数值正确、实际会话使用 CUDA、profiling 中真正执行的计算 kernel 为 CUDA；仅可见 provider 不够。之后使用本地缓存的 embedder/reranker 完成真实前向，保留已有 strict GPU/session/inference/kernel 就绪检查。CPU Torch 可用于现有依赖，不因 `torch.cuda.is_available()==false` 单独拒绝，也不借此接受 ORT CPU 回退；无需为了无关的 Torch CUDA 下载数 GB 或引入 CUDA 13 与 ORT CUDA 12 冲突。离线环境禁止模型下载和 CPU fallback。`S6_GPU_READY` 记录模式、实际 ORT probe 与同进程模型证据；启动流程结束后仍检查 `/ready` 的存储、检索、模型和绘图 worker，不能只看 `/health`。

GPU 环境仅安装 `requirements-gpu.txt`：其中使用 `fastembed-gpu==0.8.0`，CPU 默认环境才使用 `fastembed==0.8.0`。`PyMuPDF` 固定为 `1.26.7`；此次发现其 2.x 会额外安装 CPU `onnxruntime`，与 GPU distribution 共享模块并覆盖实际导入的运行时。已有污染环境应在独立 prefix 内卸除 CPU distribution 并恢复 GPU wheel，再检查 `pip check`、distribution 清单和实际 `ort.__version__`；仅有两个 distribution 的安装记录不能证明实际模块正确。

ORT 可能将形状向量和注意力维度标量主动安排到 CPU；对整模型启用 `session.disable_cpu_ep_fallback=1` 会使现有缓存模型在加载时失败。reranker 因而通过首轮真实前向的 profile 门控：要求 CUDA 等所选 GPU provider 实际执行计算，并拒绝未获允许的 CPU 算子、CPU 矩阵乘法、浮点张量 Gather、大张量和未知类型/尺寸。仅明确允许少量类型/尺寸受限的 CPU 操作：最多 8 元素的整型/布尔形状向量操作、`Shape` 的小整型输出，以及单元素整型转浮点 `Cast` 和单元素浮点 `Sqrt/Div`。允许项按操作计数记录为 `cpu_control_ops`，不得隐藏在 GPU 就绪标签后。这是有界 profile 分类规则，不是图级数据流来源证明；新模型或缓存版本仍需独立审核其实际节点分配。Python session fallback 与请求级 CPU retry 在 GPU 必需模式下仍禁用；CPU-only、GPU/CPU 未获允许的混合计算或只有控制节点的 profile 都不能通过。FastEmbed 的正式就绪证据仍是实际 GPU session 加成功前向，不能把独立 raw-model 诊断当作应用同进程 profile。

不可信绘图脚本必须使用另一个**CPU base SIF**，`FIGURES_SANDBOX_BACKEND=apptainer`，固定上述哈希，不带 `--nv`。现有后端包含 `--containall --cleanenv --no-home --no-mount hostfs,cwd,home,bind-paths,sys --net --network none`、独立 PID/IPC 和用户 cgroup 资源限制。宿主探测通过不代表在 CUDA 应用容器内的嵌套调用通过。入口会在**应用实际运行环境中**再次调用后端隔离探测（Apptainer 模式在 CUDA SIF 内，Conda 模式在宿主）；应用自身 `/ready` 还要求真实 worker probe 成功。嵌套 namespace、systemd user bus、cgroup 或 runtime helper 不可用就阻塞发布，不能退到 `local`、取消网络隔离或借用 GPU 应用环境执行用户代码。

既有 CUDA SIF 的 tensor 成功不等于完整服务可用；新 Conda 环境也必须以实际 ORT 计算、两个模型前向和 CPU worker 就绪为准。父线程正在独立准备并验证该环境，本地合成测试不代表 **GPU 服务已验收**。

## 6. 维护窗口内的一致备份与恢复演练

下列步骤只能在操作者批准维护窗口后执行。先停止接受新请求并等待/取消在途任务、绘图 worker；确认没有旁路脚本或其他进程写同一数据根，再明确停止唯一目标 unit。`systemctl` 的 MainPID=0 不足以识别其他手工 writer，操作者必须完成该检查。不要停止 8000。

```bash
systemctl --user stop citrusqa-next.service
systemctl --user show citrusqa-next.service --property=ActiveState,SubState,MainPID,InvocationID
```

设定一个新的私有备份目录名（以下 `s6-WINDOW-UTC` 为操作者填写的唯一版本）。先 dry-run，再按同样参数加 `--apply`：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py backup \
  --profile /home/WZQ/citrusqa/ops/host-profile.json \
  --destination /home/WZQ/citrusqa/backups/s6-WINDOW-UTC
# 审核计划及空闲容量后，追加 --apply。
```

备份枚举 root 下**所有有 SQLite 文件头的文件**，包括没有 `.db` 扩展名的数据库；逐一执行 SQLite backup API、`integrity_check`、`foreign_key_check`，把目标转成独立 DELETE journal 数据库。非 DB 字节完整复制，DB 的原 WAL/SHM/journal 作为证据另外保存，不混入恢复后的活动数据库旁。备份前后核对 unit 的 stopped boundary 和源字节清单；只排除已识别 SQLite 的 SHM 读标记（读操作可变、无持久用户字节），数据库、WAL 和所有普通文件仍比较哈希。任何变动都拒绝发布备份。

`BACKUP.json` 保存每个 DB schema/user_version、所有文件 SHA-256、引用映射、缺失文件豁免、原路径和服务边界；命令返回该清单 SHA-256，另行保存于维护记录。它不是加密备份；root 700、文件 600 的本地权限只是起点，需要离机备份时由管理员按现有密钥管理方案加密并单独授权传输，不可把此数据归档混入源码包。

在**全新、隔离且不重叠原数据根**的目录恢复演练：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py verify-backup \
  --backup /home/WZQ/citrusqa/backups/s6-WINDOW-UTC
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py restore-drill \
  --backup /home/WZQ/citrusqa/backups/s6-WINDOW-UTC \
  --destination /home/WZQ/citrusqa/restore-drills/s6-WINDOW-UTC
# 审核隔离位置后，restore-drill 追加 --apply。
```

演练复核所有恢复文件哈希、全部 DB 完整性/外键及引用路径映射，绝不启动应用或写在线根。DB 中原始绝对路径不改写；字节验证使用清单里的映射，所以演练目录**不能直接当应用数据根启动**。灾难恢复回生产必须另作明确计划：停全部 writer、对当前状态先做新快照、确认业务可接受的数据截止点、在离线副本上审查路径重映射、保留现状后再替换。工具有意不提供自动覆盖生产数据的命令。

为前后两版代码生成/审核完整 schema 兼容清单的观察命令是 `release.py schemas --profile ...`；把输出保存到私有文件仍是操作者的文件写入操作。在线观察不是一致基线，必须在上述 stopped snapshot 边界重新确认。

保留策略默认只给计划：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py prune \
  --parent /home/WZQ/citrusqa/backups --keep 7 --min-age-days 30
```

只有同目录中通过全部验证的旧备份才会被列入删除候选，保留最新 7 份且至少保存 30 天；工具强制最低保留两份。未知目录不删、坏备份使清理停止、不自动挤出数据来满足配额。审核候选路径并确认仍有可用恢复点后才可加 `--apply`。大数据备份先按约两倍输入量加空闲保留预估空间，超配额失败而非静默删除旧备份。

## 7. Stage、配置事务与明确启动

仅在拥有**重新构建的、兼容清单已审核**源码包后使用下列步骤。`BLOCKED` 源码包可在本地校验/展开供审阅，但不能配置上线。接收文件放在新私有 upload 目录，工具保留在独立 ops 目录；下述 `REPLACE_SHA256` 来自独立可信记录：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/release.py stage \
  --archive /home/WZQ/citrusqa/uploads/s6-VERSION.tar.gz \
  --sha256 REPLACE_SHA256 --releases /home/WZQ/citrusqa/releases
# 审核新路径后，追加 --apply。
```

保存上一版本代码支持的 schema 清单为私有 `previous-compatibility.json`。配置工具要求备份的 root、原始文件哈希与当前 stopped boundary 都仍相同，并验证前/后版本的 per-DB compatibility；任意写入后必须重新备份。

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/switch_service.py configure \
  --profile /home/WZQ/citrusqa/ops/host-profile.json \
  --release /home/WZQ/citrusqa/releases/s6-VERSION \
  --backup /home/WZQ/citrusqa/backups/s6-WINDOW-UTC \
  --previous-compatibility /home/WZQ/citrusqa/ops/previous-compatibility.json \
  --transaction /home/WZQ/citrusqa/transactions/s6-WINDOW-UTC \
  --dropin /home/WZQ/.config/systemd/user/citrusqa-next.service.d/s6-release.conf
# 审核 proposed_dropin、全部验证结果和事务路径后，追加 --apply。
```

配置事务先以私有目录保存原 effective unit、原 drop-in 字节、原源码哈希、兼容清单和备份清单哈希，再创建运行数据链接、原子替换唯一 drop-in。失败会恢复它已经修改的配置并撤销刚创建的链接，保留失败事务作为证据，不能复用事务 ID。既有源树和其他 drop-in 不修改。新 drop-in 将工作目录/启动入口指向不可覆盖的 release，设置 `Restart=no`，防止 GPU 验证失败后反复启动写数据。

只有操作者审阅并批准启动后，才手工执行：

```bash
systemctl --user daemon-reload
systemctl --user cat citrusqa-next.service
systemctl --user start citrusqa-next.service
systemctl --user status citrusqa-next.service --no-pager
curl --fail http://127.0.0.1:8030/ready
curl --fail http://127.0.0.1:8030/health
```

核对实际进程 argv/cwd、SIF、`S6_GPU_READY` 日志和 `/ready` 中 `ready=true`、存储/检索就绪、两个真实模型 loaded/GPU/inference_verified、worker verified/required。比对服务版本与已验归档清单，复核 8000 状态未变，再完成普通账号的会话/上传/下载/图件编辑/导出小规模产品检查。不要跑 Case A/B/C、FW20/40、240 routing benchmark 或修改 gold/scorer。`/health=200` 单独不能通过 GPU/worker 验收。

## 8. 代码/配置回滚，不回退用户新数据

若新版本不可用，先明确停 8030 和所有关联 writer，按第 6 节对**此刻最新 8030 数据**生成新的备份并完成隔离演练。然后 dry-run：

```bash
/home/WZQ/citrusqa/venv/bin/python -I /home/WZQ/citrusqa/ops/switch_service.py rollback \
  --profile /home/WZQ/citrusqa/ops/host-profile.json \
  --transaction /home/WZQ/citrusqa/transactions/s6-WINDOW-UTC \
  --backup /home/WZQ/citrusqa/backups/s6-ROLLBACK-UTC
# 审核后追加 --apply；之后仍需明确 daemon-reload/start。
```

回滚核对当前全部 DB schema 是否被**上一版本**兼容清单接受、旧代码字节是否仍与事务记录一致、当前 drop-in 和原配置副本哈希是否匹配。任何新 DB、schema/user_version 变化或源文件变化都会阻塞回滚，除非此前已经审查并列入兼容清单。通过后只恢复原 drop-in（或删除本次新加的 drop-in），由原 unit 再选择旧代码，**不把旧 DB/附件覆盖到当前状态**。继续使用当前 8030 数据，不把 8000 当作恢复数据源。

schema 不兼容时必须保持停止并制定前向修复或显式数据迁移方案，不能自动降库或恢复旧快照丢弃用户新数据。恢复原 unit 后依旧由操作者手动 `daemon-reload/start` 并按上一版本的就绪契约验收；历史 8030 版本若没有 `/ready`，不得把 `/health` 冒充严格 GPU 就绪证明。

## 9. 运行与访问约束

应用保持单 Uvicorn worker，共享模型实例；现有账号、会话、产物和附件以所有权校验隔离。管理员 Key 存在私有持久数据根，不进入源码或发布清单。维护时保留当前注册表、语料/索引、密钥和配置，工具不重建语料、不下载模型、不运行历史评测。

8030 仅绑定 loopback，代理头只信任 `127.0.0.1`。是否已有公网反向代理/TLS 应现场核查，本次没有切换域名、Tunnel、8000 或公网入口。仅在实际 HTTPS 入口到位时设置安全 Cookie；不要照历史首次部署片段启停旧 8000 服务。新注册普通账号、管理员初始化及隐私告知按现有产品权限设计执行，不能借维护绕过账号隔离。
