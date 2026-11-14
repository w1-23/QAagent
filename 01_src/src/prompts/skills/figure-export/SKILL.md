---
name: figure-export
version: 1.2
purpose: 科研绘图、语义微调与 SVG/PNG/PDF 交付规范
trigger: 用户要求绘图或任务需要可下载科研图时加载；纯解释不强制作图。
---

# FIGURE_SKILL：科研配图与工作台微调

本规范指导模型选择图型、编写脚本及核验交付，不执行代码。对用户呈现图、依据与下载入口，不暴露内部工具协议。

## 1. 先确定 figure contract

先明确研究问题、输入文件与列、实验单位、分组/配对、X/Y/单位、误差含义、图注和最终用途。可由材料确定的内容直接使用；研究设计有实质歧义时再询问。不要为了配图虚构数据或强加统计方法。

按数据含义选图：时间/剂量趋势可连线，即使不足五个点；无顺序类别不要靠连线制造连续趋势。小样本尽可能展示原始点；需要均值/区间时说明样本量和排除。不同量纲优先分面；不要默认双轴。配色以组身份固定，搭配线型、点形或直接标签。

## 2. 绘图路径

- 安全脚本工具 `run_research_figure_script` 可用时，以脚本 + 本文适配的 nature-figure 工作流作为主要绘图路径；按上述 contract 编写 Matplotlib 脚本，尤其适合多子图、自定义布局、拟合与特殊标注。Seaborn 基于 Matplotlib，返回可捕获的 Artist，支持这一条路径；不要列出未安装或没有编辑合同的其他库作为可用选项。新增库必须先定义 Figure/Artist 捕获、稳定身份、支持/不支持的样式属性及导出/重放测试。
- `inputs_json={"data":{"file_id":"账号文件ID"}}` 将经过账号校验的文件快照传入脚本变量 `data`（Path）；例如 `pd.read_csv(data)`。不要提供宿主路径，不要从脚本文本猜测文件或 gid。服务保存快照的 SHA256 和持久引用；样式重放使用固定字节，原文件变更不改变旧图，快照损坏/无权访问则拒绝。改变数据应创建新来源版本。
- `analysis_result_ref_json` 接受统计工具返回的真实 `analysis_result_ref`；服务按当前账号加载不可变结果及数据版本，分别通过 `analysis_result`（JSON Path）和 `analysis_data`（CSV Path）传入。统计标注从该 JSON 的全精度 outputs 读取，不从聊天散文抄数值。来源保存原分析和数据版本引用并在重放时核验。
- 脚本只留下恰好一张 Figure；子图属于同一 Figure。对象身份以工具实际返回的 manifest 为准，重放保持相同源脚本、绘图顺序和输入；需要随机过程时设种子。工具不承诺任意自设 gid 都成为可编辑对象。
- 规整图型可用 `create_research_figure(spec_json=...)`；未配置隔离 runner 时使用这个受限路径，并如实说明复杂脚本能力不可用。不得在 Web 进程、普通宿主 subprocess 或任意目录里执行模型脚本。
- 图型、布局与数据表达由用户要求及数据本身决定。工作台侧栏是生成后的语义微调，不取代脚本生成。

## 3. 声明式输入

```json
{
  "chart": "bar | line | scatter | errorbar | boxplot | histogram",
  "title": "...", "x_label": "...", "y_label": "...",
  "x": {"kind": "attachment|account_file|inline", "column": "...", "index": 0, "file_id": "...", "values": []},
  "y": {}, "group": {}, "error": "none | sd | se | ci95", "legend": true,
  "style": {"palette": "nature", "font_size": 7, "figure_size_in": [7.0, 5.0], "dpi": 300}
}
```

attachment 使用本轮附件序号与列名；account_file 使用有权限的 file_id 与列名；inline 仅用于已有数值的小结果（最多200行）。分组时间序列按 group × x 聚合，有效行和误差由实际计算决定。SD、SE、95% CI 必须与图注一致，不能互换标签。

多子图的整图规格使用 `panels:[{"panel_id":"growth", "chart":"line", "x":..., "y":...}, ...]`，每个 panel 单独指定实际数据列；`layout:{"rows":1,"cols":2,"share_x":false,"share_y":false,"wspace":0.35,"hspace":0.35}` 按行放置。整图 `style.figure_size_in` 决定实际画布，`shared_legend:true` 可合并同名组图例。panel_id 必须唯一且稳定，系列身份由该 panel 的真实组名确定；已有单子图 gid 保持原样。共享图例的 manifest 返回 `shared_legend_groups`；同一图例标记关联多个系列时，样式编辑必须显式对全部关联 gid 提交相同修改，不能让图例与数据标记分离。共享分类 X 轴要求所有 panel 的类别及顺序相同。

## 4. 数值与来源

每个数必须能追溯至输入或真实计算输出。统计量用 `analyze_research_data` 的结果，不心算 p 值，不从模型散文中制造统计证据。统计工具返回不适用或设计歧义时保留原因；不得绕开拒绝来生成显著性标记。演示合成数据只在用户明确要求时使用，并在图题/图注及交付中标明。

## 5. nature-figure 工作流与投稿规范

采用 nature-figure 的工作顺序：figure contract → 有依据的图型选择 → 统一样式与导出 → QA。该工作流参考不是 Nature 官方认证，也不代表每张图自动符合投稿要求。

- 字体以实际渲染环境为准；中文使用可用的微软雅黑、Noto CJK 等中文字体。Latin 文本可用 Arial/DejaVu Sans 等。不要宣布未安装字体已生效。
- 以最终印刷尺寸核查字号、线宽和标签可读性；7 pt 等只是项目默认值，具体稿件按目标期刊公开要求和用户约定。
- 轴标题包含变量和单位，图注明确样本量、误差与统计方法；图例与数据标记一致。刻度范围随表达目的确定，不能仅为突出差异而隐藏范围变化。
- 使用可区分的分类配色，控制类别数量，避免仅靠红绿区分。单图白色投稿画布不随网页主题染色。
- 规格中的 `qa_profile` 或脚本工具的 `qa_profile_json` 可设置 `{"name":"scientific|nature_single|nature_double","final_size_mm":[89,65],"min_font_pt":5,"min_linewidth_pt":0.5,"caption":"真实图注..."}`。nature_single/double 未指定尺寸时分别按 89/183 mm 宽度等比核查；名称是尺寸参考，不是期刊认证。receipt 测量实际画布、最终比例缩放后的最小字号和可测线宽、有限绘图坐标、图注是否存在及画布文字边界；任何检查失败都如实返回 needs_attention，不影响交付已生成的图件。
- `qa_receipt.unsupported` 明确列出未验证的科学论断、配色可访问性、标签碰撞和位图分辨率；图注存在不代表样本量/误差/统计语义已核验。不能将 receipt 或文件生成等同于投稿质量全部通过。修改后重测并保存新的 receipt。

公开参考：
- Nature 制图规范：https://research-figure-guide.nature.com/figures/preparing-figures-our-specifications/
- nature-figure 工作流（MIT 项目）：https://github.com/jing1312/nature-figure-skill

这里是独立编写的工作流适配，未复制外部实现代码。参考 commit 固定为 `cb233215d6d9dbb25956c27c19b86377c1e9353e`；许可与上游派生关系见同目录 `NATURE_FIGURE_ATTRIBUTION.md`。

## 6. 工作台微调

工作台 `/figures` 按 manifest 声明的能力提供字体/字号/字重、颜色、线宽、文字/图例位置与显示状态；支持的散点可逐点着色，轴线和刻度标签可单独选择。不承诺所有脚本 Artist 都能编辑；脚本重绘仍需要可用的安全 runner。

原始脚本/spec 与数据不因样式编辑改写。删除对象通过可撤销显示记录处理；隐藏数据点会改变呈现范围，应说明其依据，不伪装成完整数据。数据坐标、误差长度和统计量不属于样式编辑；改变数据、图型或分析方法应重新生成来源版本。

完成动作后更新临时预览，正式保存才发布下载。仅保留当前未保存预览与最近一次正式保存，不积累历史渲染版本。**未保存预览与已保存下载可能不同**；仅同一保存结果的 SVG 预览与 SVG 下载应为同一字节，PNG/PDF 核对视觉内容而非字节相同。

## 7. 交付

回答简洁呈现图、关键图注和真实下载链接，需要继续编辑时给工作台入口。只引用工具实际返回并通过验证的地址；发布失败、未保存、只读与沙箱不可用应分别说明。源脚本未实际登记为可下载文件时，不声称已经交付复现包。
