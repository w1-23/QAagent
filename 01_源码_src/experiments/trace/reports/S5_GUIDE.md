# S5：分页式使用指南（六页，不夸大、不依赖 CDN）

阶段：S5（`AUDIT_REPORT_20261030.md` 第二编 §E）
基线：S4 完成于提交 `816ee93`

## 1. 修改目的

原指南是**单页四宫格**（`web/index.html` 的 `onboarding-feature-grid`），四个卖点挤在一屏，
既放不下"怎么判读失败"，也没法从外部深链到某一节。S5 把它改成**六页可翻页指南**：

1. 这是什么、能做什么 / 明确写出**不做**什么；
2. 上传材料与语料范围（含"上传≠入库"）；
3. 四类常见任务（可点示例，只填入输入框，不自动发送）；
4. 怎么看结论和失败（completed/partial/failed/cancelled 与工具行语义）；
5. 导出与数据留存（文件在哪、能否追溯、原件不改）；
6. 安全与限制（隐私、密钥不下发、断网会明确失败、能力边界）。

硬约束：**只介绍已实现的能力**，禁止"自动核验全部引用""保证准确""自动确诊"这类夸大表述；
指南本身不引用任何外部 CDN 资源。

## 2. 实际文件与行号

| 文件 | 位置 | 改动 |
| --- | --- | --- |
| `web/index.html` | 原 `onboarding-overlay` 整块（原 :246-261） | 换成 `#onbViewport` + 6 个 `article.onb-page[data-page=1..6]` + `#onbPrev/#onbIndex/#onbNext` |
| `web/assets/app.js` | 原 `openOnboardingGuide()`（原 :193-196） | 新增 `onbPage/ONB_TOTAL/onbClamp/onbRender/onbStep/onbGo/onbFillExample`，重写 `openOnboardingGuide(startPage)`；新增全局 keydown（Esc 关闭、←/→ 翻页、Tab 焦点陷阱） |
| `web/assets/app.js` | `completeOnboarding()`（原 :243-255） | 关闭时清掉 `#guide=N` 深链 |
| `web/assets/app.css` | 文件末尾 | `.onb-viewport/.onb-page(.active)/.onb-list/.onb-example*/.onb-nav/.onb-index` 与 ≤640px 断点 |
| `src/api/test_usage_guide_markup.py` | 新增 | 13 例静态断言（页数、页号、初始可见性、导航、翻页逻辑、键盘、深链、示例不自动发送、无外链、CSS、无夸大、每页有正文） |

## 3. 变更前 / 变更后行为

| 场景 | 变更前 | 变更后 |
| --- | --- | --- |
| 首次进入 | 单页四宫格，信息密度高、看不到失败判读 | 六页，逐页推进，最后一步变"知道了" |
| 想回看某一节 | 只能从头读 | `#guide=3` 直接落到第 3 页；账户菜单打开也支持 |
| 键盘用户 | 只有 Esc/点击关闭 | Esc 关闭、←/→ 翻页、Tab 在卡片内循环 |
| 示例按钮 | 无 | 只**填入输入框**并聚焦，不发送（避免误耗额度） |
| 关闭后 | 深链残留 | `history.replaceState` 清掉 `#guide=N` |

## 4. 测试数量与通过情况

- 新增 `src/api/test_usage_guide_markup.py`：**13 例**全绿。
- 全量：**239 passed / 0 failed**（S4 结束 226，+13）。
- `node --check app.js` / `session_files.js`：通过。

## 5. 真实测试输入与结果

1. 静态：`pytest src/api/test_usage_guide_markup.py -q` → 13 passed。
2. 真实 HTTP（实例 `127.0.0.1:8010`）读取服务端实际下发的内容，产物
   `experiments/trace/runs/20261030/guide_http_check.json`：

```json
{"pages_in_served_html": 6, "has_viewport": true, "has_prev_next": true,
 "js_ONB_TOTAL_6": true, "js_deep_link": true, "js_escape_key": true,
 "css_pager": true, "css_nav": true, "all_passed": true}
```

## 6. 尚存风险

1. **未做像素级视觉验收**：本机无可用共享浏览器，只验证"服务端确实下发了六页结构 + 翻页逻辑 + 样式"，
   布局是否美观需人工过一眼。
2. **`index.html` 仍从 CDN 加载 `marked` 与 Google Fonts**（`index.html:10-12`）。本机实测：
   若 CDN 不可达，`app.js:416` 会把 `marked` 兜底成"原样输出"，回答里的表格/链接会退化成纯文本
   （不报错但难看）；字体则回退系统字体。**建议后续把 `marked.min.js` 自托管到 `web/assets/`**
   （内网部署必须做，否则 markdown 渲染退化）。本阶段未动它，属独立改动。
3. 指南文案需随能力变化维护；当前已与 `tool_availability`/导出格式一致（xlsx/ris/bibtex）。

## 7. Git

`S5: turn the onboarding blurb into a six-page guide that states limits`

## 8. 部署建议

纯前端（`index.html` + `app.js` + `app.css`），部署后**浏览器强刷**即可；可与 S1–S4 一起发。

## 9. 回滚方法

`git revert <S5 commit>`；无后端与数据库变更。
