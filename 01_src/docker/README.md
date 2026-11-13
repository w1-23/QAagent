# 容器化部署（服务镜像 + 绘图沙箱）

这一份文档说明两件事：怎么把服务本身装进容器，以及怎么让「执行自定义绘图脚本」这条路径在容器里仍然是安全的。

服务镜像与沙箱镜像是**两个不同的镜像**，不要混：

| 镜像 | 文件 | 作用 | 是否常驻 |
|---|---|---|---|
| `citrusqa/app:1.0.0` | `Dockerfile.app` | 服务本身：API、前端、语料、账号数据 | 是 |
| `citrusqa/sandbox:1.0.0` | `Dockerfile.sandbox` | 跑用户/模型生成的绘图脚本 | 否，每个作业起一个一次性容器 |

沙箱镜像里**没有**服务、没有数据库、没有语料、没有任何密钥。声明式出图（`create_research_figure`）在服务进程内完成，不需要沙箱；只有自由脚本路径需要它。

---

## 1. 目录布局为什么不能随便改

`src/config.py` 从自身位置推导运行期数据根：`<仓库根>/agent`。它有一个守卫，在数据根不在「受管源码树」内时直接启动失败——受管树指的是仓库根下存在一个名为 `01_src` 的目录。

这个守卫不是形式主义。源码树被从错误的位置导入时，`PROJECT_ROOT` 会静默指向一个空目录，于是 API key、会话、语料全部「消失」，而真实数据还躺在原处。守卫把这个故障变成一条明确的启动错误。

所以镜像里保留仓库原本的形状：

```
/app/01_src/src        Python 包
/app/01_src/web        前端
/app/01_src/scripts    运维脚本
/app/02_config         YAML 配置
/app/agent             运行期数据根（compose 里是 volume）
```

`PYTHONPATH=/app/01_src`。把包复制到 `/app/src` 会让服务拒绝启动——行为是对的，但不值得用这种方式去学布局。

---

## 2. 启动

```bash
# 宿主机上准备两个目录。job 目录必须让 Docker 守护进程也能按同一路径看到。
#
# state 下的四个子目录要一次建全：这个挂载点会**盖掉**镜像里建好的
# /app/agent，镜像里的 state/logs/workspace/data 到运行期就看不见了。
# 只建 state 时服务能起来，但检索层会报 Data directory not found。
sudo mkdir -p /srv/citrusqa/state/{state,logs,workspace,data} /srv/citrusqa/jobs
sudo chown -R 10001:10001 /srv/citrusqa/state
sudo chmod 700 /srv/citrusqa/state/state /srv/citrusqa/state/logs
sudo chmod 777 /srv/citrusqa/jobs

# 凭据不进镜像，从环境注入。
export DEEPSEEK_API_KEY=...
export LLAMA_CLOUD_API_KEY=...
export DEEPSEEK_WEB_API_KEY=...

# 启动服务与 socket 代理
docker compose -f 01_src/docker/docker-compose.yml up -d app socket-proxy

# 构建沙箱镜像（只构建，不常驻）
docker compose -f 01_src/docker/docker-compose.yml build sandbox
```

### 为什么 job 目录的路径必须两边一致

Docker 守护进程在**宿主机**上解析 `--mount type=bind,src=...` 的源路径。服务容器内请求 `/srv/citrusqa/jobs/<job>` 时，守护进程去宿主机的 `/srv/citrusqa/jobs/<job>` 找。所以：

- compose 里 `/srv/citrusqa/jobs` 在宿主机和容器里是同一个挂载点；
- `CITRUSQA_FIGURE_JOB_DIR` 指向这个路径；
- 服务在每个作业开始时建目录、结束时删掉（`src/figures/sandbox.py` 的 `_job_directory`）。

只改一边，脚本路径会以「挂载源不存在」失败。

---

## 3. 为什么要有 socket 代理

服务需要能启动容器来跑脚本。直接挂 `/var/run/docker.sock` 等于把整台宿主机的权限交给应用进程——应用一旦被拿下，宿主也就被拿下了。

`tecnativa/docker-socket-proxy` 挡在中间，只转发容器生命周期相关的调用。compose 里默认放行的：

| 变量 | 作用 |
|---|---|
| `CONTAINERS=1` | 创建/启动/等待/查看/删除容器 |
| `IMAGES=1` | 拉取沙箱镜像 |
| `VOLUMES=1` | 绑定挂载作业目录 |
| `NETWORKS=1` | 容器网络（沙箱用 `--network none`，不需要自建） |
| `PING/VERSION/INFO=1` | Docker CLI 的握手 |

其余一律 `0`：`EXEC`（进容器）、`BUILD`、`COMMIT`、`SWARM`、`SERVICES`、`TASKS`、`SECRETS`、`CONFIGS`、`SYSTEM`、`AUTH`、`EVENTS`、`NODES`。

应用容器只连代理的 TCP 端口，**看不到** socket 文件本身。应用能做的最坏的事，是启动一个沙箱镜像的容器——而那个容器没有网络、没有特权、根文件系统只读、以非 root 身份运行。

---

## 4. 沙箱容器本身的限制

每个作业的容器由 `src/figures/sandbox.py` 的 `DockerBackend.build_command` 拼装，参数是安全边界的一部分，测试直接断言这些参数：

```
--rm                                    作业结束即销毁
--network none                          无网络
--cap-drop ALL                          无能力
--security-opt no-new-privileges       不可提权
--read-only                             根文件系统只读
--tmpfs /tmp:rw,noexec,nosuid,size=64m  临时目录受限
--pids-limit / --memory / --cpus        进程数、内存、CPU 上限
--user 65534:65534                      非 root
--mount ...dst=/work/in,readonly        输入只读
--mount ...dst=/work/out                只有输出可写
```

作业目录在结束时删除；沙箱产物（SVG/PNG/PDF、清单、脚本）经服务登记进账号资料库，不留在临时目录里。

---

## 5. gVisor（可选，更强一层）

默认用的是 Docker 的原生隔离（runc + 内核 namespace）。gVisor（`runsc`）在用户态再套一层沙箱，内核暴露给容器的是受限接口，对内核攻击面更小。

宿主需要先装 runsc：

```bash
curl -fsSL https://gvisor.dev/gvisor-apt.gpg | sudo apt-get install -y ca-certificates gnupg
echo "deb [arch=$(dpkg --print-architecture)] https://gvisor.dev/debian generic main" \
  | sudo tee /etc/apt/sources.list.d/gvisor.list
sudo apt-get update && sudo apt-get install -y containerd runsc
sudo systemctl enable --now runsc
```

然后在 `02_config/config.yaml`：

```yaml
figures:
  sandbox_runtime: "runsc"
```

服务会在 `docker run` 时加 `--runtime runsc`。

> gVisor 会让 matplotlib 的启动明显变慢（首次字体缓存尤其明显）。如果沙箱作业不是常态路径，可以只给沙箱容器加 `--runtime runsc`，而应用容器仍用 runc——上面的 compose 已经是这个结构。

---

## 6. 配置项

`02_config/config.yaml` 的 `figures:` 段：

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `true` | 关掉后图件工具整体不可见 |
| `sandbox_backend` | `off` | `off` / `docker` / `local` |
| `sandbox_image` | `citrusqa/sandbox:1.0.0` | 沙箱镜像名 |
| `sandbox_runtime` | `""` | gVisor 填 `runsc` |
| `sandbox_timeout_sec` | `120` | 单作业墙钟上限 |
| `sandbox_memory_mb` | `1024` | 单作业内存上限 |
| `sandbox_cpus` | `1.0` | 单作业 CPU 上限 |
| `sandbox_allow_local` | `false` | 仅开发环境可用；`local` 后端**不是安全边界** |
| `dpi` | `300` | 位图导出分辨率 |
| `max_script_chars` | `200000` | 脚本文本上限 |

**默认是 `off`。** 这不是保守，是设计：`select_backend()` 在 Docker 不可用时返回 `None`，不会退回到本地子进程。配置成 `docker` 但守护进程没起来时，脚本路径是关闭的，工具也不会出现在模型的可见列表里（`script_sandbox` 这一项需求事实为假）。

开启脚本路径前，先把 §7 的检查跑完。

---

## 7. 部署后的人工验收

```bash
# 1. 准备一个作业目录：输入只读、输出可写
sudo mkdir -p /srv/citrusqa/jobs/accept/{in,out}
sudo chmod -R 777 /srv/citrusqa/jobs/accept
cat > /srv/citrusqa/jobs/accept/in/job.json <<'JSON'
{"job_id":"accept-1",
 "script":"import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots()\nax.bar(['CK','T1'],[10.1,12.2])\n",
 "inputs":{}, "outputs":{"svg":"figure.svg","png":"figure.png"}, "timeout_sec":30}
JSON
```

```bash
# 2. 沙箱镜像跑通这个作业，参数与服务下发的完全一致
docker run --rm --network none --cap-drop ALL --security-opt no-new-privileges \
  --read-only --tmpfs /tmp:rw,noexec,nosuid,size=64m --pids-limit 64 \
  --memory 1024m --cpus 1.0 --user 65534:65534 \
  --mount type=bind,src=/srv/citrusqa/jobs/accept/in,dst=/work/in,readonly \
  --mount type=bind,src=/srv/citrusqa/jobs/accept/out,dst=/work/out \
  --entrypoint /usr/bin/timeout citrusqa/sandbox:1.0.0 \
  --signal=TERM --kill-after 5s 30s python -I /opt/citrusfig/worker.py --job /work/in/job.json
ls -l /srv/citrusqa/jobs/accept/out    # figure.svg、figure.png、objects.json
```

`--entrypoint /usr/bin/timeout` 不是可选项：镜像的 ENTRYPOINT 已经会跑 worker，同一条命令里再写一次会让 argparse 收到两个多余的位置参数并退出 2；而墙钟上限必须在容器里生效，杀掉 docker 客户端并不会停掉容器。

信号也不能写成 `KILL`。实测 GNU coreutils 9.4（宿主机与镜像内一致）：`--signal=KILL` 时 `timeout` 自己被这个信号杀死，容器报 **137**——而 137 同时也是内存被杀时的码，于是一个死循环脚本会被描述成「内存不足」。`--signal=TERM` 才是文档里的 **124**。`--kill-after 5s` 用于兜住「脚本自己接管了 SIGTERM」的情况：超时 5 秒后补一发 KILL，服务侧再按墙钟把这条 137 归因回超时（`_interpret` 的 `timeout_sec` 分支）。

```bash
# 期望：3 秒左右结束，退出码 124
docker run --rm --network none ... --entrypoint /usr/bin/timeout \
  citrusqa/sandbox:1.0.0 --signal=TERM --kill-after 5s 3s \
  python -I /opt/citrusfig/worker.py --job /work/in/job.json
```

```bash
# 3. 服务侧：后端关闭时脚本工具不可见、调用被拒
docker compose -f 01_src/docker/docker-compose.yml exec app \
  python -c "from src.graph.tool_availability import script_sandbox_available; print(script_sandbox_available())"
# off → False；把 FIGURES_SANDBOX_BACKEND 设成 docker 后再跑 → True
```

```bash
# 4. 确认代理只放行容器生命周期
docker compose -f 01_src/docker/docker-compose.yml exec app docker exec x true   # 403
docker compose -f 01_src/docker/docker-compose.yml exec app docker build -q .    # 403
```

**不要用 `docker run --rm hello-world` 判断代理**：proxy 放行了 `CONTAINERS` 与 `IMAGES`，它能跑通。被拒的是 `exec` 与 `build`。

端到端（需要两个账号）：

1. 账号 A 上传 XLSX，在聊天里要求「按处理分组统计果实重量的均值与标准差并做组间检验」→ 拿到 JSON 结论与结果表；
2. 继续要求「把这张表画成带误差棒的分组柱状图」→ 拿到图与工作台链接；
3. 打开 `/figures`，改标题字号、拖图例、改一条系列颜色 → 保存 → 刷新页面，改动仍在；
4. 导出 PNG/PDF，与预览一致；
5. 账号 B 登录：看不到账号 A 的任何图件，直接访问 A 的项目 id 返回 404。

---

## 8. 本机没有 Docker 时

声明式路径（`create_research_figure`）不需要 Docker，任何环境都能用：图由服务端根据受校验的规格生成，不执行调用方的代码。

自由脚本路径在没有沙箱时整体关闭，这是刻意的。本机开发可以用 `sandbox_backend: "local"` + `sandbox_allow_local: true` 临时打开——但 `LocalBackend` **不是安全边界**，它只是清理过环境变量的本地子进程。生产环境不要这么配。
