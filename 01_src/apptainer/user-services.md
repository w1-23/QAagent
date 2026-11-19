# 持久运行隔离候选

`citrusscholar-web@.service` 和 `citrusscholar-worker@.service` 使用同一版本的 Linux 源码和同一候选 runtime。Web 先等待 Worker 的 systemd 启动顺序，随后由现有启动器检查该 runtime 的离线 Worker 心跳。启动使用有界的重试间隔。验收仍需要一次真实离线任务和一次真实 Browse 下载。

为候选选择固定实例名，例如 `8042`。将两个模板安装到当前服务用户的 `~/.config/systemd/user/`，并为该实例写入权限为 `0600` 的 `~/.config/citrusscholar/8042/service.env`。变量分别指向绝对路径下的 `01_src/scripts/apptainer_launch.py` 和 `deployment.json`，文件只包含路径。

```ini
CITRUS_LAUNCHER=/srv/citrusscholar/releases/candidate/source/01_src/scripts/apptainer_launch.py
CITRUS_DEPLOYMENT=/srv/citrusscholar/releases/candidate/deployment.json
```

在改动运行实例前，确认无在途任务、未确认写入及残留渲染器，保留已验证的源码、启动配置和 SQLite 一致性备份。先在独立候选验收 Chromium 自带沙箱、真实双账号 Browse 和离线 Worker。候选配置须为回环监听并使用已初始化的 runtime。依次停止原候选的 Web 和 Worker，确认端口和 runtime 已释放，再加载并启动新单元。

```bash
systemctl --user daemon-reload
```

```bash
systemctl --user start citrusscholar-worker@8042.service citrusscholar-web@8042.service
```

核对两个实例的 `InvocationID`、`MainPID`、实际源码和运行根。等待 `/ready` 明确就绪，再确认 Worker 可以执行真实离线任务，Web 重启后重新登录可读取原工程会话和附件。模板使用 `KillMode=mixed`，允许主进程先接收退出信号，超时后清理整组。演练停机时要确认两个单元均正常退出且端口释放。

```bash
systemctl --user enable citrusscholar-worker@8042.service citrusscholar-web@8042.service
```

用户服务的开机恢复取决于宿主现有的用户管理器与 linger 设置。每次 Browse 仍创建任务级 Chromium，并在任务结束时回收。

若候选未通过，停止并禁用新的两个实例，确认其写者已退出，再用原配置和源码恢复旧候选入口。先核对 runtime 兼容性和未确认写入；保留原有记录，按需用备份单独演练恢复。
