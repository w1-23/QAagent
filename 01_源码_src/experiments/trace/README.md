# 如何监视工具调用（第三方旁观，不改动 agent）

原则：**监视必须站在 agent 之外**。本目录的工具只读 HTTP/SSE 与落库结果，
`from src import auth` 只用于在本地库里插入一条浏览器会话凭据（等价于人工登录），
从不 import `graph/`、`tools/`、`api.main`，因此**观测本身不可能改变 agent 的决策**。

## 1. 实时看（人工，最直观）

浏览器打开 `http://127.0.0.1:8000/` → F12 → Network → 发一轮问题 → 点 `chat` 请求 →
`EventStream` 逐帧就是真实执行过程：`tool_call_start` / `tool_result` / `done`。
每帧带 `tool_call_id`、`status`、`elapsed_ms`、`summary`、`action_hint`。

命令行等价物（不装任何东西）：

```bash
# 先拿一份 cookie：登录一次后从浏览器复制，或用 collect.py mint 出来的 token
curl -N -H "Cookie: citrus_auth=<token>" -H 'Content-Type: application/json' \
     -d '{"query":"柑橘木虱的防治措施有哪些","light_mode":false}' \
     http://127.0.0.1:8000/api/v2/chat
```

## 2. 落盘留证（可复现，推荐给实验/论文）

```bash
cd /home/WZQ/CitrusQA/01_源码_src
../.venv/bin/python experiments/trace/collect.py S2 "画一张柑橘木虱传播途径示意图"
# 追问同一会话（不新建 session）：
../.venv/bin/python experiments/trace/collect.py S3 "这张图里还有什么传播途径？" <上一轮的 session_id>
```

每次运行产出 `experiments/trace/runs/<YYYYMMDD>/<场景>/`：

| 文件 | 内容 |
|---|---|
| `trace.jsonl` | 每一帧 SSE，含 `t=+相对秒`、`event`、`data` |
| `session.json` | 落库的会话行（工具行带 `status/summary/elapsed_ms`） |
| `summary.json` | 机器可判的断言结果：`executed / unavailable / turn_status / answer_chars` |
| `answer.md` | 最终回答原文 |

`summary.json` 是**可复现的判据**：工具是否真的执行、失败几次、整轮状态如何，一目了然。

## 3. 事后查（重启后仍在）

- 会话级：`GET /api/v2/session/{sid}/messages` → 每条工具行的 `status / reason_code /
  summary / elapsed_ms / completed_at_ms`（存 `messages.execution_json` 列）。
- 服务级：`agent/logs/server_*.log`，含每轮 `General Agent finished: N chars, M tool types, Xms, stop=...`。

## 4. 已知边界

- 不展示模型隐藏思维链；展示的是**工具调用、结果摘要、耗时、状态、失败原因**。
- `action_hint` 是"这次调用在做什么"的短说明（≤56 字符），由工具名+参数推导，不是模型自述。
- 前端 provider 不可用时（无 Electron）无法做视觉验收，只能用 SSE + 落库 + 静态资源 grep 验证。
