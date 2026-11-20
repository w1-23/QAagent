# 局域网 HTTPS 入口

Web 在 `127.0.0.1:8043` 运行。Caddy 在已核实的 LAN IPv4 的 `18443` 提供 HTTPS，并保留浏览器发送的 Host。Worker 无 HTTP 监听。应用以进入 Caddy 的连接对端作为客户端地址，并校验同机代理共享凭证。旧环境可以省略 `lan_ingress`。

## 候选部署配置

将 `deployment.example.json` 的 `lan_ingress` 设为包含以下三个字段的对象。

| 字段 | 值 |
|---|---|
| `ipv4` | 受邀设备能够路由到的服务器 LAN IPv4，与 Caddy 的 `CITRUS_LAN_IP` 一致 |
| `port` | `18443` |
| `proxy_token_file` | Web 服务用户专用的绝对文件路径，文件权限为 `0600` 或 `0400` |

由部署环境生成 32 字节随机值，以 64 个小写十六进制字符写入该文件，Caddy 使用同一值的 `CITRUS_PROXY_TOKEN`。通过服务账户的私有文件或 systemd 凭证传递，避免出现在命令参数、源码、请求头日志和验证记录中。代理配置及 Caddy 进程环境仅对运维和服务账户开放。凭证轮换时协调 Web 和代理切换。LAN 模式自动启用 Secure Cookie，应用仅对代理流量和本机健康检查开放，入口的写操作须带准确的 HTTPS Origin。

## 验收与切换

1. 在至少两台实际受邀设备上核对源地址、路由、入接口和可达性。限制 `18443/tcp` 只接收该接口上批准的来源，验证其他网段和外部路由失败，并确认无额外 HTTP、IPv6 或转发入口。缺少合格边界时保持关闭。
2. 使用 `Caddyfile.lan.example` 和受保护配置验证语法。Caddy 启用内部 CA 后只分发 CA 公钥证书及 SHA-256 指纹，确认服务证书具有准确 IP SAN，再由受邀设备信任 CA。保持私钥仅由代理账户读取。
3. 在批准的切换窗口停接并排空，按 Web、Worker 顺序停旧，确认相同运行根只有一套写者。按 Worker、Web 顺序启动，等待新 Worker 心跳和 `/ready`，随后开放已核实的入口。记录实际版本、镜像哈希、监听地址及服务激活代次。
4. 从两台设备验证登录、邀请码审核、上传、SSE、账号隔离和代理重连。分别确认服务器回环就绪与客户端 HTTPS 可达。切换失败时关闭代理入口，排空候选并恢复已核验的上一版服务及配置。

## 持久 Web 和 Worker

`systemd/` 中的两个 user unit 配合 `~/.config/citrusscholar/<实例>/service.env`，该文件设置 `CITRUS_LAUNCHER` 和 `CITRUS_DEPLOYMENT` 的绝对路径。launcher 使用本发布的 `apptainer_launch.py`，也可使用兼容其 `--config`、`--role` 接口并保留现役只读绑定的版本固定包装器。应用 SIF 必须包含当前发行版的支持包，或者使用该版本已验证的支持包只读覆盖。运行前仍需核对模型配置、Browse、GPU 和 staging 路径。

Web 的启动检查等候本次启动之后的新 Worker 心跳。Worker 回执与业务队列用于判断任务是否执行或结算。配置用户服务常驻需要确认服务账户的 linger 设置，并在切换窗口按上面的排空流程安装和启用。旧 CitrusQA Web 和 Cloudflare 源码模板已经退役，历史版本可从 Git 恢复。
