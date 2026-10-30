# S3：文件 ↔ 会话 / 具体轮次跳转闭环

阶段：S3（`AUDIT_REPORT_20261030.md` 第二编 §A / §D 的“导航不可验证”问题）
基线：S2 完成于提交 `9a127fe`；生产仍在 `ff58b1f`（未部署、未 push）

## 1. 修改目的

审计（第二编 §A）确认三处断链，导致“从文件回到它所属的那一轮”在代码层面**不可能成立**：

1. `GET /api/v2/session/{id}/messages` 每行不带行主键：前端无法定位任何一条历史消息；
2. 同一接口 `if len(out) >= limit: break` **从最老端截断**，长会话最新几轮反而看不到，也
   没有游标可以往回翻；
3. 文件行（`session_files.client_request_id`）与人消息（`messages.request_id`）虽同源，但
   对外都不可见，前端只能“跳到会话顶部”，属于假跳转。

S3 的目标：让“文件 → 会话 → 具体那一轮”这条链在**真实数据**上可验证；找不到锚点时必须
明确失败，不得显示“已定位”。

## 2. 实际文件与行号

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `src/session/files.py` | `_row_to_public`（约:428-450） | 新增 `turn_request_id`（由 `crid:<id>` 去掉前缀）与 `source_session_id`；仍不暴露 `storage_path` / 原始 `client_request_id` |
| `src/api/main.py` | `_slice_history`（新增，`session_messages` 之前） | 分页核心：按游标取**最新** `limit` 条，返回 `(window, has_more, next_before)`；容错 `limit<=0`、坏游标、无 `message_id` 的历史行 |
| `src/api/main.py` | `session_messages`（约:2780-2870） | 每行补 `message_id`；人消息补 `request_id`；`limit` 默认 200→60；新增 `before` 查询参数；响应新增 `has_more` / `next_before` |
| `web/assets/app.js` | `restoreSessionHistory`（约:2826-2960） | 拆出 `renderHistoryMessages` / `fetchHistoryPage` / `buildEarlierHistoryButton` / `loadEarlierHistory` / `jumpToMessage` / `window.jumpToFileTurn`；历史消息带上 `data-message-id` / `data-request-id` |
| `web/assets/session_files.js` | 文件卡片操作区 | 新增“定位到对话”按钮 → `window.jumpToFileTurn(sid, turn_request_id)`；失败只 warn，不假报 |
| `web/assets/app.css` | 文件末尾 | `.history-earlier*` 与 `.message.is-jump-target` 高亮动画 |

## 3. 变更前 / 变更后行为

| 场景 | 变更前 | 变更后 |
| --- | --- | --- |
| 打开一个 30 轮会话 | 只返回最老 200 条（且从最老端截断），最新轮可能缺失 | 默认返回**最新** 60 条，`has_more` / `next_before` 支持往回翻 |
| 想往回看更早的对话 | 无入口、无游标 | 顶部“载入更早的对话”，每次 60 条，翻到底按钮消失 |
| 点文件库里的“定位” | 只切到会话（跳到顶部），看不出是哪一轮 | 滚动到那一轮并高亮；找不到锚点 → 明确失败（console.warn，不高亮、不提示成功） |
| 刷新后消息的稳定标识 | 无 | `data-message-id` / `data-request-id`（来自数据库行主键，不靠前端猜测） |

## 4. 测试数量与通过情况

- 新增 `src/api/test_history_pagination.py`：**8 例**（最新页而非最老页、无更多、游标三段回退不丢不重、越界游标、`limit<=0` 与坏游标容错、无 `message_id` 的历史行仍可分页、`crid:` 前缀归一、非前缀原样保留）。
- 全量：**216 passed / 0 failed**（S2 结束时 208，+8 分页回归）。
- 前端 `node --check`：`app.js`、`session_files.js` 均通过（本机 `node.exe` v24.21.0）。

## 5. 真实测试输入与结果

探针：`experiments/trace/probes/probe_history_jump_e2e.py`（只走公开 HTTP，不 import Agent。
本机实例 `127.0.0.1:8010`，真实模型）。输入：3 轮对话，第 1 轮带 CSV 附件
`probe_history_jump.csv`，`client_request_id=probe<hex>`。

产物：`experiments/trace/runs/20261030/history_jump_130613.json`

```json
{"session_id": "624d28d0-…", "total_rows": 6,
 "page1_ids": [12482, 12483], "page2_ids": [12480, 12481],
 "file_carries_this_turn_id": true, "jump_target_resolvable": true,
 "checks": {"newest_page_returned": true, "paging_walks_backwards": true,
            "rows_carry_message_id": true, "file_carries_turn_key": true,
            "jump_key_matches_history": true,
            "no_fake_jump_for_unknown_turn": true},
 "all_passed": true}
```

要点：`limit=2` 的第一页是**最新**两条（12482/12483），游标回退得到 12480/12481，两页无重叠；
上传文件行的 `turn_request_id` 与该轮人消息的 `request_id` 完全一致；不存在的 turn id 不产生任何目标。

## 6. 尚存风险

1. 往回翻页是“重渲染全部已加载消息”，超长会话（数百轮）会有一次明显的重排；当前上限是每页 60 条、最多连续 8 次自动回溯。
2. 跳转依赖 `client_request_id` 存在：老会话里由服务端生成的 uuid 同样可用，但若某些历史行完全没有 request id，则该轮无法被跳转（返回 false，不假报）。
3. 未做视觉验收（本机共享浏览器不可用），只看 DOM 结构与真实接口数据。
4. `limit` 默认从 200 降到 60，第三方如果依赖“一次拿全量”需要改用游标。

## 7. Git

`S3: page session history by cursor and jump from a file to its exact turn`（见 `git log`）

## 8. 部署建议

与 S1/S2 一起部署；部署后须重启服务（静态资源即时生效，后端需重启）。

## 9. 回滚方法

`git revert <S3 commit>`，或恢复三份前端静态文件与 `src/api/main.py` / `src/session/files.py`。
数据库无结构变更（没有新增列），回滚不需要迁移。
