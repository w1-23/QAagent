# ui_audit —— 真实浏览器（headless Chrome / CDP）UI 审计探针

全部探针只读：不写库、不发模型请求、不改页面状态（除临时挂载/移除模板节点）。

## 前置

1. 本机服务在跑：`http://127.0.0.1:8010`（见仓库根 `STARTUP.md`）。
2. 一份有效的登录 Cookie：`cookie.json`（内容形如 `[{"name":"citrus_auth","value":"...",...}]`）。
   生成方法与 `experiments/trace/collect.py` 的 `mint()` 相同（写 `browser_sessions` 表）。
3. Chrome 路径写在 `cdp_shot.js` 顶部；Node 版本 ≥ 18（只用内置模块，无依赖）。

## 运行

```bash
# 默认 1440x900（run_probe_sized.js 可通过环境变量改视口）
node experiments/trace/probes/ui_audit/run_probe_sized.js \
     experiments/trace/probes/ui_audit/probe_a11y_contrast.js \
     /tmp/out.png
```

Windows PowerShell：

```powershell
$env:W='430'; $env:H='900'
node experiments/trace/probes/ui_audit/run_probe_sized.js `
     experiments/trace/probes/ui_audit/probe_viewports.js `
     _scratch\viewport430.png
```

探针把结果以单行 `PREFIX:{json}` 打到 stdout，截图写在第三个参数。

## 各探针测什么

| 文件 | 输出前缀 | 测点 |
| --- | --- | --- |
| `probe_a11y_contrast.js` | `AUDIT_PROBE:` | 静态资源 9 个是否全部 200；左栏四入口的 role/aria-label/title/aria-expanded/可聚焦性/尺寸；宠物菜单互斥与 Esc |
| `probe_focus_contrast.js` | `A2:` | 打开面板后的焦点位置；`role`/`aria-label`；面板内可 Tab 控件数；三主题关键元素对比度（**未做 alpha 合成，结果仅参考**） |
| `probe_contrast_composited.js` | `CONTRAST:` | 逐层 alpha 合成后的真实背景色与对比度（**审计结论以此为准**） |
| `probe_viewports.js` | `NARROW:` | 430 / 1024 下的侧栏、快捷区、菜单包围盒、抽屉宽度、横向溢出 |
| `probe_sprout_motion.js` | `SPROUT_PROBE:` | 叶片 transform 是否随时间变化、三点的透明度时序、图标 getAnimations |
| `probe_img_svg_static.js` | `PROBE_JSON:` | 对照实验：`<img>` 引用的 SVG 内部 CSS 动画是否执行（canvas 像素比对）+ 内联 SVG 对照组 |

## 已知坑

- `probe_img_svg_static.js` 会向页面追加一个临时 `<img>` 与一个内联 `<svg>`，结束后自行移除。
- `cdp_shot.js` 每次运行都是全新 profile，localStorage/主题不会跨运行保留。
