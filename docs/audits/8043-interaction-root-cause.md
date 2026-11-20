# 8043：交互故障证据与实施前基线

只读审计以 `624c252124a1efa5dcdda257fa366dc6f10ec664` 为基准。R1–R4 分别核对服务端、浏览器、绘图/上下文和运维。静态源码可以证明分支与风险，不能代替现场逐 job 复现。本文不包含用户消息、附件或凭据。

| 证据 | 已确认 | 未证实 / 待验 |
|---|---|---|
| `web/assets/app.js:3790–3810`、`task-controls.js`、`src/api/main.py:3268` | 主发送活跃时隐式 POST 旧 `/updates`，合轮是发送语义。日志有四次聊天和四次更新 200。 | 某个具体文本对应第几版未从正文对账。 |
| `src/core/jobs.py:35–37,68–70,137`；`src/api/main.py:2177–2181,2824,2921,2995–3013` | 已有账号幂等/全局容量，无会话 FIFO；初始 segment 早于容量。原轮待答虽属运输终态，仍不能推进新轮。历史结算和外层清理不同步。 | 重启时受理、未知派发、等待子段、并发和删除只在隔离 fixture 注入。 |
| `src/api/main.py:2764–2784`；`src/core/user_questions.py:350–466` | 断线后取消可能只有终态快照；问题续段可落库未派发，取消问题不必然结束父 job。 | 未对用户现场故障按 job 确认。 |
| `web/index.html:349`、`web/assets/app.js:1251,1288,3961,4466,5452` | 仅 textarea 监听 paste；已有对象 URL 缩略图；Enter 默认行为与 IME 未统一；固定 300 秒观察看门狗与已消费 heartbeat 分离。工具行 attempt 序号不能证明此前失败。 | 可信浏览器访问、剪贴板及具体任务的第七次分类、终态/计时和焦点需实测。 |
| `src/figures/core.py:119–196`、`script_outputs.py:25–83`、`src/research/figure_tools.py:383–389` | legacy 单图和分离 renderer 都无 emit；v2 主脚本有报告/表/允许时的图 emitter。固定 renderer 仅获得声明冻结表的 data 和图形作用域；这是合同，不是沙箱安全边界。 | 用隔离工程 Worker 复现 renderer 误用与 F02；真实用户回执未归因。 |
| `src/agent/context_projection.py:352–398`、`src/core/diag.py:77–92` | 已分类权限/消息/预算/超时要保留；剩余异常泛化 `context_projection_failed`。只加服务端白名单阶段与异常类型，不提高模型限额。 | journal、agent.log 和 diag 的允许标签未给出该次具体异常。本次不能定为预算、图件或断联。 |
| 11:58:42–12:00:17 CST 与 13:25–13:29 探测 | 有 8043 Web 重载窗口；最近远端 /ready=200，Worker 单元活跃，8042 持久 Web/Worker 已停且禁用。portal 无活动隧道，本机 18043 两次连接拒绝。 | 当时 Worker 是否完成用户任务、最早断联归属、已鉴权工作台访问均未通过这组探测认证。未操作服务或隧道。 |

隔离基线：Python 任务/上下文 13 通过，认证/图模板 84 通过 +13 子测试；应用 SIF＋实际 worker site-packages 支持覆盖下 QA/SVG/认证/CLI 61 通过。裸 SIF 的分类柱图对应 1 失败；单加父进程 PYTHONPATH 同样失败，图形子进程以 `-I` 加载；实际只读 site-packages 覆盖后定向通过。Node 两套 21 通过。原导航 Python 缺空占位中的夹具，三个 JS 套件缺历史 harness；另起未改产品源码的 Git archive，单补 `78a5ff1` 四个旧测试文件后 Node 13 通过/20 失败（夹具 DOM 与模拟网络不匹配），Python 旧夹具继续缺 `src.test_account_deletion`。不能把这些结果记作新实现的 PASS；新测试要自包含或仅恢复经复核的夹具。具有用户现场证据的端到端条目另行记录。
