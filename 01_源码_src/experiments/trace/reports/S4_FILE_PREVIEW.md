# S4：文件预览（PDF 空白 / Office 回退）的诚实化

阶段：S4（`AUDIT_REPORT_20261030.md` 第二编 §B）
基线：S3 完成于提交 `67dd69b`；生产仍在 `ff58b1f`

## 1. 修改目的

审计（第二编 §B）把“预览空白”定性为**无法用 `iframe.onload` 证明**的问题：

- 前端只塞一个 `<iframe src=preview_url>`，`onload` 只代表“导航完成”，不代表渲染成功；
- 文件记录里的 `mime_type` 可能是空值或 `application/octet-stream`（老数据），浏览器遇到
  这种类型**下载而不渲染**，iframe 里就是一片空白，而 `onload` 依然会触发；
- 失败时没有任何可点击的兜底，用户只能看到白框。

S4 的目标：把“预览成功”变成**可验证的事实**（字节到达 + 魔数正确 + 交给渲染器），失败时
明确说明原因并给出打开/下载入口；同时让后端不再把 PDF 的 MIME 退化成二进制流。

## 2. 实际文件与行号

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `src/session/files.py` | `_INLINE_MIME_BY_SUFFIX` / `_canonical_mime`（新增，`_row_to_public` 之前） | 按后缀固定可内联 MIME（pdf/png/jpg/gif/webp/svg/txt/md/csv/json），空值与 `application/octet-stream` 一律按后缀纠正 |
| `src/session/files.py` | `_row_to_public` | 输出前先 `mime_type = _canonical_mime(...)`，再算 `kind` |
| `src/api/main.py` | `preview_account_file`（约:3117-3145） | 预览路由自己再归一次 MIME，并 `kind = info.get("kind") or kind_for(...)`（不再信任上游一定给了 kind） |
| `src/api/main.py` | `preview_session_file`（约:3176-3225） | 同上 |
| `web/assets/session_files.js` | `previewStageHtml` 的 pdf 分支 + 新增 `loadPdfPreview` | 先 `fetch` 字节 → 校验 `%PDF-` 魔数 → `blob:` 交给 iframe；任何一步失败显示原因 + “在新窗口打开 / 下载原件” |
| `web/assets/session_files.css` | 文件末尾 | `.sf-pdf-wrap` / `.sf-pdf-status` / `.sf-pdf-fallback*` 状态样式 |

## 3. 变更前 / 变更后行为

| 场景 | 变更前 | 变更后 |
| --- | --- | --- |
| 正常 PDF | iframe 直接指向预览接口，白屏也算“已加载” | 先取字节验魔数，再交给浏览器；空白不再等于“成功” |
| 老记录 MIME 为空 / octet-stream | 浏览器下载，iframe 空白，无提示 | 后端按后缀纠正为 `application/pdf`，正常内联渲染 |
| 文件损坏 / 0 字节 / 非 PDF 字节 | 空白 | 显示“读取失败/不是 PDF/内容为空” + 打开与下载按钮 |
| 接口 404 / 401 | 空白 | 显示“读取失败：HTTP 404” + 兜底链接 |

## 4. 测试数量与通过情况

- 新增 `src/session/test_file_preview_mime.py`：**10 例**（legacy octet-stream→pdf、空值→pdf、
  带 charset 归一、图片按后缀固定、文本/表格保留 charset、未知后缀保留上报值、空值未知后缀回落、
  `_row_to_public` 同时修 mime 与 kind、`kind_for` 稳定、**路由层**用 monkeypatch 喂一条
  “空 MIME、无 kind”的老记录仍返回 `application/pdf` 且字节是 `%PDF-`）。
- 全量：**226 passed / 0 failed**（S3 结束时 216，+10）。
- 前端：`node --check session_files.js` 通过。

## 5. 真实测试输入与结果

探针 `experiments/trace/probes/probe_pdf_preview_e2e.py`（公开 HTTP，真实实例 `127.0.0.1:8010`，
真实模型）。输入：上传 403 字节的手写单页 PDF `probe_preview.pdf`。

产物：`experiments/trace/runs/20261030/pdf_preview_131556.json`

```json
{"status": 200, "content_type": "application/pdf", "disposition": null,
 "accept_ranges": "bytes", "range_status": 206, "bytes_equal_original": true,
 "size_bytes": 403, "library_kind": "pdf", "library_mime": "application/pdf",
 "checks": {"library_kind_is_pdf": true, "content_type_is_pdf": true,
            "body_is_real_pdf": true, "inline_disposition": true,
            "range_supported": true},
 "all_passed": true}
```

（过程记录：第一版探针试图直接在 `session_files.sqlite3` 里插一条“空 MIME”的重复行来复现老数据，
结果与服务的读连接竞争，`Range` 请求偶发 404。结论：**复现老数据不该改生产库**——改为 pytest 的
monkeypatch 用例，探针只保留只读断言。）

## 6. 尚存风险

1. 浏览器内置 PDF 查看器在极旧版本或企业策略禁用时仍可能不渲染；此时用户会看到明确失败提示与
   两个兜底链接，而不是白屏。
2. `blob:` 预览把整份 PDF 读进内存；超大扫描件（数百 MB）会占用浏览器内存。
3. 未做像素级视觉验收（本机无共享浏览器），只验证“字节正确 + 类型正确 + 失败可解释”。
4. Office（docx/xlsx）仍走下载查看，未接入在线渲染。

## 7. Git

`S4: render previews only from verified bytes and never trust a stored MIME`

## 8. 部署建议

与 S1–S3 一起部署；后端改了两处预览路由，需重启服务。

## 9. 回滚方法

`git revert <S4 commit>`；无数据库结构变更。
