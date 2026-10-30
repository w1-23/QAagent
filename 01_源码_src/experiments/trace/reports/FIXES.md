# 三批修复（B1/B2/B3）改动与验收记录

日期：2026-10-30　服务器：`/home/WZQ/CitrusQA`　本地仓库：`E:\codex_WORKSPACES\Citrus_QA_Agent`
验收方式：**命令行重启 + 真实 SSE 采集**（`experiments/trace/collect.py`），不靠断言自证。
原始证据：`experiments/trace/runs/20261030/S1..S4/{trace.jsonl,session.json,summary.json,answer.md}`

## B1 工具绑定策略 + 不伪报本地检索

| 项 | 内容 |
|---|---|
| 根因 | 工具按"意图"裁剪时把本地检索裁掉（问答只剩联网）；中文查询进本地检索被硬拒；被裁掉的工具仍出现在可见步骤里，产生 `[TOOL_UNAVAILABLE]` 假步骤 |
| 改动 | 新增 `src/graph/tool_policy.py`：`WEB_TOOLS` / `LOCAL_TOOLS` / `WEB_ONLY_INTENTS`，问答类 = 联网 **+** 本地同时绑定；`general_agent_graph` 只广告真正绑定的工具；`src/prompts/source/16_tool_usage_file_rules.md` 增加"问答默认流程"（先联网取权威证据，本地语料作为候选补充，逐条标来源） |
| 验收 | S1：`网页搜索 complete 803 ms` + `本地检索 complete 6661 ms`，2/2 执行，0 个 unavailable；S4 复现：539 ms + 5936 ms |

## B2 输出预算 + draw_svg artifact-first

| 项 | 内容 |
|---|---|
| 根因 | ①服务端无图示注册表，每轮重新校验；②`output_budget=2048` 把思考也算进去 → SVG 被拦腰截断（`no element found: line 57` / `unclosed token`）；③截断后模型重试，最终撞时限被报成"超时" |
| 改动 | 新增 `src/core/figures.py`（进程级图号注册表、按渲染期图号集裁剪、线程安全）；`src/tools/draw_svg.py` 改为返回占位符 `[[FIGURE:n \| caption]]`，失败退化为占位符，最多 4 次；`general_agent_graph` 在出口 `figures.expand()`；视觉类问题预算 2048→**8192**；截断后按剩余预算重试一次 |
| 验收 | S2：3/3 工具 `complete`（含 `绘图 complete 7 ms`），`[[FIGURE:` 残留 **0**，` ```svg ` 块 **1**，回答 5,490 字符，整轮 **≈45.8 s**（此前必超时） |

## B3 文案单一来源 / 数据根（密钥）fail-fast / 只读旁路 trace

| 项 | 内容 |
|---|---|
| 根因 A（文案） | `general_agent_graph.py` 里 26 处 `本轮未完成：…` 字面量在失败路径上**覆盖模型输出**，措辞与 trace、`turn_status` 三处不一致 |
| 根因 B（密钥） | 仓库根存在一棵重复源码树 `CitrusQA/src`（147 个 .py，上次部署遗留）。`src/config.py` 以 `Path(__file__).parents[2]/"agent"` 推导数据根，从仓库根直接起服务会 import 到它 → 数据根变成空的 `/home/WZQ/agent`，表现就是"key 被删、会话没了、本地检索空" |
| 根因 C（观测） | 之前只能靠"工具行只剩已完成"猜执行情况 |
| 改动 A | 新增 `src/core/turn_notices.py`（22 条，唯一定义处）+ `src/graph/general_agent_graph.py` 全量替换 + `src/core/turn_status.py` 改用同一 `INCOMPLETE_PREFIX`；清单见 `NOTICES.md` |
| 改动 B | 重复树移入 `_orphan_src_20261030/`（保留取证/回滚，附 README）；`src/config.py` 新增 `_assert_managed_tree()`：数据根不在受管树内立即 `RuntimeError` 并给出正确启动方式（可用 `CITRUSQA_ALLOW_UNMANAGED_TREE=1` 显式放行） |
| 改动 C | `experiments/trace/collect.py`：纯 HTTP/SSE 观察者，不 import `graph/`、`tools/`、`api.main`；落四件套；支持第 3 个参数复用会话以测追问。用法见 `README.md` |
| 验收 | ①`grep -c 本轮未完成 general_agent_graph.py` = **0**，`turn_notices` 22 条；②`_assert_managed_tree` 对 `/home/WZQ`、`_orphan_src_20261030` 均 `RAISED_OK`；③重启后 `/api/v2/config` 仍 `has_api_key=true`、`web_search.configured=true`（key 未丢）；④S1–S4 四场景 trace 全部落盘 |

## 回归测试（服务器 `.venv` 与本地 `rag-agent` 各跑一遍，19 例全过）

- `src/core/test_turn_notices.py`（新增 6 例）：提示语全部带前缀、未知 code 兜底不抛、占位符缺失不吞句子、`turn_status` 认得每条提示语、**编排层禁止再出现硬编码"本轮未完成"**（源码扫描闸门）
- `src/core/test_turn_status.py`（9 例）
- `src/research/test_lost_constants.py`（4 例）

## 场景验收汇总

| 场景 | 工具执行 | turn_status | 关键指标 |
|---|---|---|---|
| S1 问答（木虱防治） | 网页 803 ms + 本地 6,661 ms，2/2 | completed | 回答 1,166 字符 |
| S2 绘图（传播途径图） | 网页 1,724 + 本地 6,476 + 绘图 7 ms，3/3 | completed | SVG 一次通过、占位符 0、5,490 字符、≈45.8 s |
| S3 同会话追问 | 0（复用上轮证据，无重复检索） | completed | 618 字符 |
| S4 问答（黄龙病症状） | 网页 539 + 本地 5,936 ms，2/2 | completed | 728 字符 |

## 残留与已知边界（不掩盖）

1. 工具级结果说明仍有 20+ 处 `未完成`（如 `src/research/tools.py` 的文献核验、数据审查报告用语）。它们属于**工具输出**而非整轮提示语，语义各不相同，本轮未强行合并；若要彻底单一来源需逐条定义 code。
2. 模型仍可能跳过检索：S3 追问 0 工具直接作答（属工具选择，非执行记录问题）。
3. 前端视觉验收未做（共享浏览器 provider 不可用），只用 SSE、落库行与静态资源 grep 验证。
4. 重启服务必须"先 kill 再单独 start"，且需 `setsid nohup … < /dev/null & disown`，否则 `rw_exec` 超时会连带杀掉子进程。
