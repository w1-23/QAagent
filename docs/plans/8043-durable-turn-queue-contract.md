# 8043：独立对话轮次队列合同

基线：`w1-23/CitrusScholar` `624c252124a1efa5dcdda257fa366dc6f10ec664`。契约由主智能体维护，下面的行为是实施目标，不是已经通过的声明。服务器是受理、顺序、历史和执行事实的权威。

## 身份、受理和存储

一次普通发送 = 一个账号级 `client_request_id`（保留现有唯一索引）+ 一个会话内独立轮次 + 一个稳定的 `task_jobs.job_id`（根 job）。在现有 `sessions.db` 的短 `BEGIN IMMEDIATE` 中同时写根 job `queued` 和 `chat_turn_queue`；一会话一个单调 `queue_seq`，不因取消、清理或重启复用。已有 `request_id` 用于历史，不能用其替代浏览器请求 ID。`child_job_id` 是续段观察地址，不是第二条普通用户消息。根与 child 都应受 owner/session 校验。事务内同时检验 pending 数/字节限额；幂等命中先于限额，不消耗配额或重新写文件。

命中同 ID 同规范化指纹返回原根 job；改变会话、文本、附件有序身份/摘要、Skill 或其它行为选项均为冲突。首次录入只保存有界执行参数（冻结文本及显式设置、输入引用），不能写 Base64、Cookie、密钥或宿主路径。原 blob 分库，由现有上传内容验证、账号租约、不可变字节 SHA-256 与 `session.files.replace_resource_references` 保护；用幂等暂存和补偿，不宣称跨库事务。确认 202 时字节、授权引用及 pin 都已可读；丢失响应后不能启动第二次上传/预算/执行。提交、认领、取消、重放和历史下载按现有账号/会话边界重新核权。提交后普通重新登录不改变受理事实；权限代次变化按照既有租约规则阻止继续，不存原浏览器 token。处理清除/删除时通过现有会话检查与账号删除锁，先拒绝或取消并核对，不允许已删会话被延迟执行复活。

## 串行门和恢复

| 阶段 | 进入依据与门限 |
|---|---|
| `queued` | 队列与根 job 已提交，无 Agent；同会话最低未结序号在未存在旧的活动/等待/未知轮次时才可认领。 |
| `claimed` | SQLite 比较并交换写入 generation；持有根门但尚未派发，容量等待复用 `ChatCapacity`。认领者需重查账号/会话/授权版本和输入摘要；只传有界参数给同一现有执行器。 |
| `running` | 在可能开始 Agent 及有副作用段之前先持久标记派发意图；在容量准入后才创建初始 active segment，不从 `current_step` 猜未执行。实际开始时间单列，用于计时；预算/计费按现有物理准入，queued 或取消 queued 不用模型不扣执行次数。 |
| `awaiting_input` | 原轮次未解决；运输 SSE 可以已结束。问题答复在父根下认领同一个 child，取消/过期本问题必须单独持久化并收尾。审批、Code park/唤醒和受权恢复不放开会话门。 |
| `settling` | 已有真实可读终态/明确历史结果，尚未确认外层 graph 资源全部释放。`history_saved=false` 必须有明确可解释的取消、失败或对账原因。 |
| `terminal` | 结算 CAS；终态证据 + 历史已保存或明确不保存 + release/cleanup 已确认。取消 queued 原子完成，不调用当前任务的 stop。 |
| `reconcile_required` | 派发或清理结果无法证明；本会话后继不自动越过，也不按 TTL 重新执行。凭现有受信 checkpoint/工作记录和可解释人工/宿主决定收敛。 |

根记录持有 `(owner, session, root_job, queue_seq, state, generation, active_child, account_epoch, dispatch_at, accepted_at, started_at, finished_at, payload_digest, bounded_payload, clear_reason)` 或等价字段。`queued`、`claimed` 与独立 job 的 `queued/running` 为不同层；只有认领代次下持久派发意图才表示不可盲重跑。没有意图并可证明未调用时按原序恢复；有意图不等于真的调用过，却足以阻止自动重放。只可用已有代码等待续段代次恢复原 run，不重新开始模型任务。未知会话不霸占其它会话的全局物理容量；未知不影响授权取消后继 queued。

调度器属于 Web lifespan 单一消费者，迁移及对账后开放受理，有界扫描加唤醒，不依赖浏览器或离线 Code Worker 就绪。独立会话仍走现有全局/账号 `ChatCapacity`，不改变全站执行容量。新发送与旧流式 `/api/v2/chat` 不允许各自创建同会话 Agent。`_dispatch_question_child`、`_resume_execution_segment`、`recover_chat_agent` 核同一根代次；`resume_chat_operation` 不是新轮次，只能按既有受授权定向行为且不得越过活动根。非轮次文献工具不串改聊天 query 或历史；会话清空/删除不得与活跃根及待提交的附件互相竞态。不要用 `supersede_pending_questions` 吃掉未回答问题。结算落在 `run_graph_segment` 外层租约释放后；不可从 `done` 帧、`finalizing` 或观察者断开直接推进。持久终态写入与是否有 SSE 客户端无关。旧的未决根必须统一检查和核对，不能用迁移直接清空。

## HTTP 与观察

- `POST /api/v2/session/{session_id}/turn-queue`：成功 202（首次及同指纹重放），返回 `session_id, client_request_id, job_id, queue_seq, queue_position, state, status_url, events_url`；位次仅 queued 并与本会话权威未执行项一致。`GET` 同路径为授权有界视图，用于恢复排队气泡；沿用 `GET .../task-by-request/{client_request_id}` 查询原受理及 `GET /jobs/{job_id}` 与 `/events?after=`。`DELETE .../turn-queue/{client_request_id}` 仅取消未认领项，重复取消返回同事实；已认领/运行时 409，不替换当前 stop。
- 旧 `/api/v2/chat` 复用相同持久受理和观察桥，稳定 SSE 形状。不可安全兼容的状态返回明确可对账的冲突，不另开执行。`POST .../updates` 仅显式任务卡用，仍按原 job 权限和阶段，不做普通发送，也不从更新触发新的 Agent。
- 服务端不发送敏感正文到运行诊断；队列/会话接口仅向授权用户提供必要内容/附件视图并 `no-store`。稳定原因码含 `turn_payload_conflict`、`turn_queue_full`、`turn_not_queued`、`turn_reconcile_required`、`turn_queue_not_ready`，保持现有 401/404 隔离、413 大小与 429 限额语义。输给认领的取消只给最新事实，不自动降级为全会话取消。
- 页面新消息先展示「确认发送中」，拿到持久凭证才显示排队位次。网络失败凭同 ID 查找；切会话仅关闭观察。活动轮和子段的事件不写入 B/C 气泡。不合并 root/active child 的事件游标。执行与排队计时分别由真实时戳和终态水位决定；流 heartbeat 不是执行证据，轮询 200 也不冒充工具仍活跃。

最低覆盖：两个真实连接竞争；同 ID 失去 202 重放和变载荷；等待问题、审批及 Code；只取消队列中项；close/soft-delete 与领取；FIFO 排位和同会话单 Agent；容量和账号配额；进程故障点（受理、认领、派发、历史和 cleanup）；附件分库补偿和跨账号；旧路径和终态回放。不得用虚拟 DOM 的单测冒充 Chromium 真实剪贴板/观察证明，不提交运行状态。
