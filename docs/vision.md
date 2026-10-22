# 病害图像初筛（vision）设计说明

这一模块解决的是「用户拍一张柑橘叶片/果实照片，问这是什么病」的问题。它的定位是**初筛**，不是诊断：
只回答固定覆盖范围内的高置信结论，范围外一律拒答，并强制附上覆盖范围与「需专家确认」。

## 1. 运行链路

```
本轮图片（data URL，仅内存）
  → 预处理：EXIF 校正 → 长边限 1600 → HSV 叶片候选区 → letterbox 边缘中位数补边
  → 第一阶段：每个启用模型各跑一次全图
  → 低置信度 / 小边际 / 模型分歧 / 未找到叶片区域时，第二阶段只复核相关模型
  → 视需要补充叶片框或 2×2 重叠局部视图
  → 跨模型加权噪声或融合 + 归一化
  → 拒识规则（置信度 / 边际 / 健康否决）→ 文本报告
```

要点：

- **不做居中裁剪**。手机照片里叶片常常偏在一侧，预处理先用绿色/黄绿色 HSV 掩码、闭运算和最大连通域找叶片候选框，再等比缩放补边到模型尺寸。
- **按需切块**。全图首轮发现置信度不足（top1 < 0.60 或 top1-top2 < 0.12）、模型首选不一致或未找到叶片区域时，才生成局部视图；只对需要复核的模型运行补充视图。
- **质量门**。短边 < 192 px、整体亮度过低或边缘方差过低时返回重拍提示，不运行分类器。阈值是工程筛查值，不代表通用图像质量标准。

## 2. 模型与许可

| 权重 | 导出文件 | 大小 | 许可 | 覆盖 | 权重 |
| --- | --- | --- | --- | --- | --- |
| `ixrbhii/citrus-disease-convnext`（ConvNeXt-Small） | `citrus_convnext.onnx` | 188.8 MB | Apache-2.0 | 8 类柑橘 | 1.0 |
| [`dima806/orange_fruit_disease_detection`](https://huggingface.co/dima806/orange_fruit_disease_detection)（ViT-B/16） | `dima_orange_vit.onnx` | 327.4 MB | 模型卡标注 Apache-2.0 | canker / healthy / melanose | 1.0 |
| [`linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification`](https://huggingface.co/linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification)（PlantVillage 38 类） | `plantvillage_mnv2.onnx` | 8.8 MB | `other`（未给出标准许可证名称） | 只取 `Orange with Citrus Greening` 与白粉病弱标签 | 0.35 |

校验值（SHA-256）：

```
d864845f226c234e5ea0736fa0a6a2feb73be1453032d5ce75aeb7f8d4798e2a  citrus_convnext.onnx
b687e5cf217de6ef135d5d28b1b83964848e256569ce10d9ce9d636373ee6bb2  dima_orange_vit.onnx
491296924490fe20ac9db96a17bff73c38205250b5494aef5c75da7aa3f5b150  plantvillage_mnv2.onnx
```

**许可核查（2026-10-22）**：Dima 模型卡标注 Apache-2.0；PlantVillage 模型卡只标 `other`，没有给出可识别的标准许可证名称。本地非商用配置按用户选择默认启用；这不等同于对上游权重或训练数据授予许可。公开服务部署、权重分发前必须核实模型和训练数据条款。

```yaml
vision:
  allow_unlicensed_models: true
```

关闭后只启用许可证已标注的 ConvNeXt 与 Dima ViT；PlantVillage 的黄龙病标签和白粉病弱标签不参与推理。

## 3. 覆盖范围（统一标签库）

可识别（10 类，柑橘样本训练）：柑橘溃疡病、柑橘黄龙病、柑橘黑斑病、柑橘黑点病（梅色斑病）、煤烟病、
叶片枯焦（生理/环境胁迫）、叶卷曲病毒病、红蜘蛛（叶螨）为害、柑橘细菌性疫病、未见明显病斑（健康）。

弱参考（1 类，非柑橘样本训练，不可当结论）：白粉病。

明确不在范围（16 类，输出里会列出，需人工判断）：疮痂病、脂点黄斑病、炭疽病、枝枯/衰退、细菌性穿孔/角斑、
粉虱为害、蚧壳虫、潜叶蛾、红圆蚧、得州螨、缺铁、缺镁、缺锌、缺锰、缺氮、衰退病。

## 4. 判读规则

- `top-1 < 0.60` → `uncertain`（置信度不足）
- `top-1 - top-2 < 0.12` → `uncertain`（模型间不一致）
- 局部复核后启用模型的首选类别仍不一致 → `uncertain`
- `top-1 = healthy` → 始终为 `uncertain`；仅描述“未见明显病斑”，不确认健康，因为模型没有范围外检测能力
- healthy 候选同时有病害信号 ≥ 0.25 时，额外记录缺素/虫害/药害误判风险
- 图片质量未过门限 → `low_quality`，不运行分类器
- 模型文件缺失 → `no_model`；解码失败 → `error`
- 报告固定包含：覆盖范围、弱参考标签、不在范围清单、知识库检索建议、「不是诊断结论、需专家复核」、
  以及「不得据此给农药用量」

## 5. 实测（仅作冒烟，不作指标承诺）

59 张真实照片 / 21 类（取自 `ML-Intern-lab/citrus-disease-vlm-instruct`，与训练数据同源）：

- ConvNeXt：可评 9 张 top-1 6/9；dima：8 张 6/8；MobileNetV2：6 张 1/6
- 三模型并集覆盖 10 类；朴素融合 top-1 6/17
- **风险数字**：42 张「真值不在覆盖范围」的图里，有 34 张曾被融合结果判为 healthy 且置信 ≥ 0.6。
  这就是「拒答 + 覆盖范围声明」必须存在的理由，调阈值解决不了。
- 旧记录的 90–140 ms 来自全模型多视图路径，不能代表本次分阶段路径。当前分阶段实测为平均 5.36 次推理/图、暖机 P50 47 ms / P95 77 ms；相对每模型运行全部旧视图，推理次数减少 74.4%。样例中 17 张覆盖类图片有 6 张 top-1 匹配，42 张范围外图片仍有 35 张把“未见明显病斑”排在首位；这些全部降为不确定结果。完整口径见 `docs/vision_staged_benchmark.json`。该样例集与模型来源数据同源，只作冒烟比较，不作田间准确率承诺。

## 6. 安装与启用

模型文件不进仓库（`.gitignore` 已排除 `*.onnx`）。首次运行：

```powershell
python 04_脚本_scripts/prepare_models.py --vision      # 从 GitHub Release 下载到 agent/models/vision
```

发布地址可用 `CITRUSQA_VISION_RELEASE` 覆盖（企业内网镜像），模型目录可用 `VISION_MODEL_DIR` 覆盖。
下载失败不影响文字问答主链，`GET /api/v2/vision/status` 会如实报告缺哪个文件。

依赖：`onnxruntime`（CPU）。GPU provider 优先级为 `CUDAExecutionProvider → DmlExecutionProvider → CPUExecutionProvider`，见 `config.yaml` 的 `onnx.provider_priority`。状态接口不加载模型，只报告已加载会话；实际推理结果记录会话首选 provider，CPU 仍可能作为个别算子回退 provider。

## 7. 接口与配置

- 工具：`disease_diagnose(image_index=0, focus="")`，只在「本轮请求带图片」时暴露给主模型；
  图片通过请求级 `contextvars` 传递，不落盘、不入知识库。
- 端点：`GET /api/v2/vision/status` 分别返回模型文件状态、推理运行时、会话加载/首选 provider 与当前启用模型的覆盖类别；不为查看状态而提前加载大模型。
- 配置（`config.yaml`）：

```yaml
vision:
  enabled: true
  model_dir: ""                 # 留空 = agent/models/vision
  confidence_threshold: 0.60
  margin_threshold: 0.12
  healthy_veto: 0.25
  allow_unlicensed_models: true
```

## 8. 已知局限

- 没有缺素细分（铁/镁/锌/锰/氮）、虫害细分（蚧壳虫、粉虱、潜叶蛾、螨类）、疮痂病、炭疽病等；
  这些只能靠文字症状 + 知识库/联网回答，图片工具会明确说「不在范围内」。
- 非柑橘图片（葡萄、番茄等）可能被映射成柑橘类别；图像分类器本身没有通用 OOD 保证，必须结合覆盖声明和人工判断。
- 训练数据与常见病害图库高度同源，真实田间照片（逆光、遮挡、多叶）准确率会明显低于上述冒烟数字。
- 不能替代专家，也不能据此推荐农药用量。
