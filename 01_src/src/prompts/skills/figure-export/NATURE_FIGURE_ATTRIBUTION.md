# nature-figure 工作流参考与许可

CitrusScholar 的 `figure-export/SKILL.md` 是针对现有 Matplotlib / Seaborn、语义 manifest、patch、隔离 worker 和统一 publisher 独立编写的工作流适配。参考范围为 figure contract、按数据选图、最终尺寸排版、统一导出与 QA；没有安装或嵌套 FigureForge，没有复制其编辑器实现、模板、示例数据或视觉资产。

公开参考固定版本（2026-11-14 核对）：

- 项目：<https://github.com/jing1312/nature-figure-skill>
- commit：`cb233215d6d9dbb25956c27c19b86377c1e9353e`
- 工作流：<https://github.com/jing1312/nature-figure-skill/blob/cb233215d6d9dbb25956c27c19b86377c1e9353e/skills/nature-figure/SKILL.md>
- 许可：<https://github.com/jing1312/nature-figure-skill/blob/cb233215d6d9dbb25956c27c19b86377c1e9353e/LICENSE>
- 派生说明：<https://github.com/jing1312/nature-figure-skill/blob/cb233215d6d9dbb25956c27c19b86377c1e9353e/DERIVATIVE_NOTICE.md>

该参考仓库声明派生自 Yuan Yizhe 及贡献者的 `Yuan1z0825/nature-skills`，上游快照为 `f3941a1722e39af78b24bc7a34167b8880629545`。其派生说明不表示自动同步或官方背书。CitrusScholar 没有将该参考项目称为 Nature 官方工具；Nature 公开规范与项目默认 profile 分开。

上述项目仅作为早期工作流适配的参考，不再打包或加载其旧来源快照。当前实际加载与执行的是用户指定的 `Yuan1z0825/nature-skills` 2.8.0，固定提交 `7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8`；来源、Apache-2.0 许可、逐文件哈希及 Python 核心适配边界见 `../vendor/nature-skills/`，由通用技能目录和 `src/prompts/nature_figure.py` 加载，隔离 worker 执行绘图检查。没有安装 FigureForge 或运行其外围 R、图像 API 与安装器。为保留早期参考归属，以下仍列该参考版本声明的 MIT 许可；当前打包内容的许可以上述 vendor 目录为准。

```text
MIT License

Copyright (c) 2026 Yuan Yizhe (nature-* skill content)
Copyright (c) 2026 jing1312 (FigureForge editor, templates, and documentation)

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
