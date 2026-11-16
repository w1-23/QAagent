# Agent loop 上游参考与适配记录

2026-11-15。用户允许参考 pi 或 opencode 底层实现，现有科研能力作为插件。这里记录实际阅读过的固定版本、采用的机制以及本地验证范围。没有引入整套上游 SDK，也没有把上游的本机命令权限继承到本服务。

## 固定来源

- **pi-mono**，commit `c3cc91632d0a1950fb122605b190233f2aa0006e`：[agent-loop.ts](https://github.com/badlogic/pi-mono/blob/c3cc91632d0a1950fb122605b190233f2aa0006e/packages/agent/src/agent-loop.ts)、[MIT LICENSE](https://github.com/badlogic/pi-mono/blob/c3cc91632d0a1950fb122605b190233f2aa0006e/LICENSE)。参考 `runLoop`、`streamAssistantResponse`、`executeToolCallsSequential/Parallel`、`prepareToolCall` 及 `runAgentLoopContinue`。
- **OpenCode**，commit `d432a06b9c07bab0be4e8d642173b1cb14137f11`：[runner/index.ts](https://github.com/sst/opencode/blob/d432a06b9c07bab0be4e8d642173b1cb14137f11/packages/core/src/session/runner/index.ts)、[runner/step.ts](https://github.com/sst/opencode/blob/d432a06b9c07bab0be4e8d642173b1cb14137f11/packages/core/src/session/runner/step.ts)、[run-coordinator.ts](https://github.com/sst/opencode/blob/d432a06b9c07bab0be4e8d642173b1cb14137f11/packages/core/src/session/run-coordinator.ts)、[MIT LICENSE](https://github.com/sst/opencode/blob/d432a06b9c07bab0be4e8d642173b1cb14137f11/LICENSE)。该版本实现位于 `packages/core`，不是旧的 `packages/opencode`。

本地使用 Python 原生 async 实现这些边界，保留已有 LangChain/DeepSeek 传输。没有复制运行环境、provider attribution、凭据加载、Effect/Bun 基础设施或工作区 shell。

## P3/P4 当前生产源码

生产 `host.py` 已切换到 `harness.py` 的公开 LangChain `create_agent`/`AgentMiddleware`，真实 model/tools 节点由 LangGraph 执行并同步提交加密 SQLite 检查点。`runtime.py` 保留共享值类型与基线测试，生产不再实例化旧执行器。沿用既有 ModelDriver、CapabilityExecutor、HostTools 和 operation runner，无直接工具执行后门。

`checkpoint_store.py` 保护原生消息及私有协议状态；`batch_journal.py` 保存批次意图与完成观察，已完成不重复执行，未知结果先对账。补充指令水位先持久后确认 applied。`context_projection.py` 先归档并验证原文，再用公开 SummarizationMiddleware 生成内部发送视图；原始图消息不改写。`context_tools.py` 提供授权精确回读。显式恢复复用原任务身份、预算和截止时间，入口为 `/api/v2/chat/jobs/{job_id}/recover-agent`。

P4 使用已安装 `deepagents==0.7.38` 的公开 `SkillsMiddleware.modify_request` 生成简短能力目录，使用公开 `StoreBackend` 处理文件 list/glob/search/write/edit 语义。原始字节留在既有 session_files；精确读取使用公开 download_files，避免普通 read 对 CRLF 的规范化。没有复制私有上游实现。上游部分入口读取即可披露的方法不作为本产品的完整加载证明，宿主另以现有 registry 校验必需资源、版本/hash、真实身份与交付范围，再经原操作 runner 派发。没有新增通用执行或继承沙箱权限。

固定组件版本、MIT 来源与真实验证见 [迁移证据](agent_migration_evidence.md)，生命周期见 [上下文契约](context_lifecycle.md)。当前服务尚未切换。以下记录保留 pi/OpenCode 参考基线的来源与适配历史，不代表 P3 仍使用旧循环或仅进程内检查点。

## 基线采用的机制及差异

| 上游机制 | CitrusScholar 适配 |
|---|---|
| pi 的模型→工具观察→下一次请求 | `src/agent/runtime.py` 为唯一通用循环实现；工具失败可继续，最终事实校验失败回到同一个有工具的循环 |
| pi 的消息/执行事件边界 | `events.py` 把公开事件和受保护执行检查点分开；公开事件没有正文、参数、结果或私有 reasoning。保存回调完成后才通知事件/派发动作 |
| pi 的工具准备、顺序/并行执行、插件钩子 | `execution.py` 只调用宿主注入的受控 operation runner；按目录元数据调度，只读有界并行，写入串行；等待容量后重新检查权限 |
| pi 的继续执行入口 | `resume()` 不追加虚构用户消息，校验 owner/session/user-turn，继承逻辑步数；同一轮不能用 `run()` 重置步数。只有已对账的 ready 检查点可恢复 |
| OpenCode 的 step 与用户请求分离 | `ExecutionIdentity` 区分 user turn、run，LoopEvent 使用 model_steps；内部控制消息不新建用户轮 |
| OpenCode 的取消、等待工具收尾与未决调用 | 截止时间覆盖执行中的 await；取消保留原始 provider call ID；操作 runner 对账后才能恢复。异常时 TaskGroup 取消并等待兄弟任务，不假称写入撤销 |
| OpenCode 的保留 continuation 步数 | 检查点携带已用步数，新 run 不刷新逻辑预算；真实调用费用、供应商能力和原截止时间仍由宿主持久层恢复 |
| 上游的不同停止/恢复策略 | 截断/未知/中止响应不能执行工具，即使被截断 JSON 恰好可解析；等待用户与未知写入都暂停；没有科研覆盖计数或固定两次修复上限 |

**基线明确不采用：**轮内自动压缩、自动通过子 agent 或新 run 清空预算、把每次模型调用当作新用户轮、从普通模型正文恢复可执行命令、工具插件直接结束整个任务。基线实现仍为轮内不压缩。2026-11-15 后续批准的成熟Harness迁移允许有条件、透明的同轮工作集导航摘要，并保留显式不压缩模式；该政策已在 P3 源码实现，完整边界见 [上下文生命周期](context_lifecycle.md)；基线本身仍不含摘要或持久恢复。

一个带 pending_question 的结果必须来自目录中标记的 interaction 能力；同批其他调用得到真实的 not_run 观察。插件加载失败只影响该能力，返回不包含原异常正文的可恢复观察。参数校验、账号权限、幂等、租约、质量检查、完整结果存储仍是宿主 operation runner/publisher 的责任，调度器不提供绕过这些步骤的直接调用后门。

## 基线接线与当时的验证边界（历史记录）

生产图源码的 `supervisor` 已指向 `src.agent.host.general_agent_node`；模型调用、动作调度和终答校验由 `AgentRuntime` 负责。保留 `session_load → candidate_prefetch → supervisor → session_save` 外壳，API 仍在会话保存后发布 done。这是源码接通，不代表正在运行的实例已更新。

已接通并离线验证：

1. `HostTools` 核对真实 job、owner、session 和 request 身份，要求持久 operation，不允许缺 job 时静默直接执行。
2. `preset_catalog` 安装 25 项能力的固定 schema 和权限/并发/恢复元数据；实现按需加载并检查 schema 漂移。调度仍经过现有权限、租约和操作记录。
3. 工具完整结果、来源引用和资产验证完成后才进行有界首次交付；存储不可用则停止，不让下一次模型依赖无法回读的观察。
4. 补充指令使旧提案失效，返回同一循环重新选择；初始 Nature 与 Word 方法在选中相应操作时加载。没有独立理解模型、科研覆盖停滞计数或无工具终答模型。
5. 取消保留原始调用 ID；已实际调度但无法确认结果的写操作标为 unknown，禁止自动重试。租约申请失败属于 not_run；公开事件与保存轨迹不能把 unknown 降为 failed。

测试使用合成模型/工具，以及真实的临时 jobs/session/结果/资产数据库；覆盖实际图入口到会话保存、SDK 协议、取消、补充要求、存储故障、真实资产字节校验及旧阈值之外的连续探索。严格交互 schema 在 MockTransport 捕获的实际 SDK 请求体中验证；异步工具保留结构化元数据与生产者上下文，持久会话保留轮次/操作/回读身份且排除私有推理。

2026-11-15，经用户批准删除三个旧控制模块后，包含异步工具元数据修复的组合回归共 557 项通过，1 条已有的 Matplotlib Annotation 图例警告。该结果不等于真实供应商、科研任务或完整产品验收。

剩余工作：候选预取外壳仍需替换为按需共同发现；宿主仍复用旧工具结果适配器，整体插件卸载隔离尚未完成。补充指令沿用旧 apply_updates 的确认时序，需与持久循环检查点共同改造。通用命令和隔离实例切换受既有审批拒绝约束。经用户明确批准，已删除旧 `graph/agent_runtime.py`、`graph/loop_kernel.py`、`graph/finalization.py`，移除旧模型循环、科研停滞/修复计数器和独立终答流程。仍被使用的工具适配器及旧记录兼容逻辑保留；本次删除不代表整个架构迁移或所有旧代码清理完成。

`LoopCheckpoint` 是进程内边界契约；它尚未接入持久恢复存储。`checkpoint.messages` 包含受保护的执行消息，不能直接 `asdict/json.dumps` 作为公开日志或假装所有 provider 私有字段已可恢复。生产必须使用经过协议验证的专用存储适配器。

## 许可证声明

为保留参考实现的许可与归属，以下保留两份 MIT 许可的版权行与共同条款。该声明不代表使用了上游服务或获得上游背书。

```text
MIT License

pi-mono:
Copyright (c) 2025 Mario Zechner

OpenCode:
Copyright (c) 2025 opencode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
