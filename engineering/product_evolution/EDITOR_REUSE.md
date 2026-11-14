# 图件编辑复用与来源记录

核查时间：2026-11-14（本地时间）。本轮优先使用项目已有依赖和浏览器原生能力，不引入另一套绘图内核。

## 已采用

| 来源 | 本轮使用方式 | 许可与归属 |
|---|---|---|
| Matplotlib 3.10.6（本机现有环境） | Text文字/字体属性；MarkerStyle生成标记路径；PathCollection路径、大小、颜色、描边；Line2D样式；Legend布局和标记 | 已读取安装包METADATA内完整许可；Matplotlib自有许可，元数据分类为PSF License。保留上游分发自带LICENSE及版权声明；没有修改或内嵌复制Matplotlib源码 |
| 浏览器原生HTML dialog | 当前页面使用指南的模态层、背景不可交互及键盘基础行为 | Web平台API，无需引入第三方JS运行库 |
| 现有CitrusSchalor图件基础 | 语义对象清单、白名单overrides、预览/保存/CAS、账号授权、发布与GC | 延续本仓库实现，未用外部编辑器替换生命周期 |

Matplotlib API依据（以安装版本运行测试为准）：

- https://matplotlib.org/3.10.6/api/markers_api.html
- https://matplotlib.org/3.10.6/api/collections_api.html
- https://matplotlib.org/3.10.6/api/legend_api.html
- https://matplotlib.org/stable/users/project/license.html

浏览器模态依据：https://developer.chrome.com/docs/modern-web-guidance/guides/accessibility/accessibility （Modals and Native Dialogs）。

## 检索后未引入

[a11y-dialog](https://a11y-dialog.netlify.app/) 提供轻量、成熟的无障碍模态组件。本轮目标环境已支持原生dialog，因此采用平台能力；没有下载或复制该库代码，也没有增加它的依赖或作未经核验的许可归属声明。

## Tavotto的角色

只用公开技术介绍、试用界面和用户操作截图整理可观察行为；没有读取或移植其实现源码、私有接口或视觉资产。其技术页当前自述AGPL-3.0，但我们不把公开页面的许可文字扩大成对全部发行物的判断，也不以此作为直接复用授权。功能规格与本地实现分开记录，参见 `EDITOR_BEHAVIOR_SPEC.md`。

## 发布时

分发依赖时保留对应包的LICENSE/NOTICE；如果以后确实拷贝第三方代码片段或资源，再逐项记录固定版本、路径、许可、修改说明。当前记录不声称完成了全仓库或全部转依赖的许可审计。
