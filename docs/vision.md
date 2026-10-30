# 病害图像初筛（vision）设计说明

这一模块解决的是「用户拍一张柑橘叶片/果实照片，问这是什么病」的问题。它的定位是**初筛**，不是诊断：
只回答固定覆盖范围内的高置信结论，范围外一律拒答，并强制附上覆盖范围与「需专家确认」。

## 1. 运行链路

```
本轮图片（data URL，仅内存）
  → 预处理：EXIF 校正 → 长边限 1600 → HSV 叶片前景检测 → letterbox 等比补边（不居中裁剪）
  → 视图集：全图 + 中心方块 + 2×2 重叠切片（最多 7 个视图）
  → 三个 ONNX 模型各自对视图做最大池化
  → 跨模型加权噪声或融合 + 归一化
  → 拒识规则（置信度 / 边际 / 健康否决）→ 文本报告
```

要点：

- **不做居中裁剪**。手机照片里叶片常常偏在一侧、背景占一半，居中裁剪会把病斑切掉。预处理先找叶片前景
  （绿色/黄绿色 HSV 掩码 + 闭运算 + 最大连通域），再等比缩放补边到 224，保留整片叶子。
- **大图切块**。长边超过 1600 且叶片占比大时，额外切 2×2 重叠子图（重叠 30%），取各视图最高分，
  避免「整片叶子缩到 224 后病斑只剩几个像素」。
- **质量门**。模糊（边缘方差过低）、过暗、长边 < 320、叶片占比 < 5% 会提示重拍，而不是硬给结论。

## 2. 模型与许可

| 权重 | 导出文件 | 大小 | 许可 | 覆盖 | 权重 |
| --- | --- | --- | --- | --- | --- |
| `ixrbhii/citrus-disease-convnext`（ConvNeXt-Small） | `citrus_convnext.onnx` | 188.8 MB | Apache-2.0 | 8 类柑橘 | 1.0 |
| `dima806/orange_fruit_disease_detection`（ViT-B/16） | `dima_orange_vit.onnx` | 327.4 MB | 未标注（unknown） | canker / healthy / melanose | 1.0 |
| `linkanjarad/mobilenet_v2_1.0_224-plant-disease-identification`（PlantVillage 38 类） | `plantvillage_mnv2.onnx` | 8.8 MB | other | 只取 `Orange with Citrus Greening` 与白粉病弱标签 | 0.35 |

校验值（SHA-256）：

```
d864845f226c234e5ea0736fa0a6a2feb73be1453032d5ce75aeb7f8d4798e2a  citrus_convnext.onnx
b687e5cf217de6ef135d5d28b1b83964848e256569ce10d9ce9d636373ee6bb2  dima_orange_vit.onnx
491296924490fe20ac9db96a17bff73c38205250b5494aef5c75da7aa3f5b150  plantvillage_mnv2.onnx
```

**许可策略**：`unknown` / `other` 的权重默认不加载（`licensed=False`）。它们补的是黑点病、黄龙病与白粉病弱标签，
需要覆盖这些类别时在 `config.yaml` 打开：

```yaml
vision:
  allow_unlicensed_models: true
```

关闭后只跑 Apache-2.0 的 ConvNeXt（8 类，无黄龙病/黑点病/白粉病）。

## 3. 覆盖范围（统一标签库）

可识别（10 类，柑橘样本训练）：柑橘溃疡病、柑橘黄龙病、柑橘黑斑病、柑橘黑点病（梅色斑病）、煤烟病、
叶片枯焦（生理/环境胁迫）、叶卷曲病毒病、红蜘蛛（叶螨）为害、柑橘细菌性疫病、未见明显病斑（健康）。

弱参考（1 类，非柑橘样本训练，不可当结论）：白粉病。

明确不在范围（16 类，输出里会列出，需人工判断）：疮痂病、脂点黄斑病、炭疽病、枝枯/衰退、细菌性穿孔/角斑、
粉虱为害、蚧壳虫、潜叶蛾、红圆蚧、得州螨、缺铁、缺镁、缺锌、缺锰、缺氮、衰退病。

## 4. 判读规则

- `top-1 < 0.60` → `uncertain`（置信度不足）
- `top-1 - top-2 < 0.12` → `uncertain`（模型间不一致）
- `top-1 = healthy` 但其他病害信号 ≥ 0.25 → `uncertain`（缺素/虫害/药害常被判成健康）
- 模型文件缺失 → `no_model`；解码失败 → `error`
- 报告固定包含：覆盖范围、弱参考标签、不在范围清单、知识库检索建议、「不是诊断结论、需专家复核」、
  以及「不得据此给农药用量」

## 5. 实测（仅作冒烟，不作指标承诺）

59 张真实照片 / 21 类（取自 `ML-Intern-lab/citrus-disease-vlm-instruct`，与训练数据同源）：

- ConvNeXt：可评 9 张 top-1 6/9；dima：8 张 6/8；MobileNetV2：6 张 1/6
- 三模型并集覆盖 10 类；朴素融合 top-1 6/17
- **风险数字**：42 张「真值不在覆盖范围」的图里，有 34 张曾被融合结果判为 healthy 且置信 ≥ 0.6。
  这就是「拒答 + 覆盖范围声明」必须存在的理由，调阈值解决不了。
- 延迟（DirectML，预热后）：三模型 × 6~7 视图约 90–140 ms；会话构建约 0.7 s（进程内缓存一次）。

## 6. 安装与启用

模型文件不进仓库（`.gitignore` 已排除 `*.onnx`）。首次运行：

```powershell
python 04_脚本_scripts/prepare_models.py --vision      # 从 GitHub Release 下载到 agent/models/vision
```

发布地址可用 `CITRUSQA_VISION_RELEASE` 覆盖（企业内网镜像），模型目录可用 `VISION_MODEL_DIR` 覆盖。
下载失败不影响文字问答主链，`GET /api/v2/vision/status` 会如实报告缺哪个文件。

依赖：`onnxruntime`（CPU）。要用 DirectML 显卡加速，换成 `onnxruntime-directml`（providers 优先级
`DmlExecutionProvider → CUDAExecutionProvider → CPUExecutionProvider`，见 `config.yaml` 的 `onnx.provider_priority`）。

## 7. 接口与配置

- 工具：`disease_diagnose(image_index=0, focus="")`，只在「本轮请求带图片」时暴露给主模型；
  图片通过请求级 `contextvars` 传递，不落盘、不入知识库。
- 端点：`GET /api/v2/vision/status` 返回启用状态、模型文件、许可、实际 provider、覆盖类别。
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
- 非柑橘图片（葡萄、番茄等）可能被映射成柑橘类别，报告里会提示「不在范围内需人工判断」。
- 训练数据与常见病害图库高度同源，真实田间照片（逆光、遮挡、多叶）准确率会明显低于上述冒烟数字。
- 不能替代专家，也不能据此推荐农药用量。
