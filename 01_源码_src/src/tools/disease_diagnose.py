"""柑橘病害图像初筛工具（仅在用户本轮上传了图片时暴露给模型）。"""
from __future__ import annotations

import logging

from langchain_core.tools import tool

from src.vision import labels as label_bank
from src.vision.context import get_request_images
from src.vision.engine import format_report, get_engine

logger = logging.getLogger(__name__)


@tool
def disease_diagnose(image_index: int = 0, focus: str = "") -> str:
    """Screening citrus leaf/fruit diseases in the images the user uploaded this turn.

    Call it when the user asks for disease/symptom screening and the specialized
    classifier can add useful evidence. Images are addressed by their
    order in the current turn (image_index, 0-based). The returned text is a
    preliminary screening result from pretrained image models: it covers only a
    fixed list of classes, so never treat it as a diagnosis, always mention the
    coverage limits to the user, and never turn it into pesticide advice. ``focus``
    optionally names the symptom the user cares about (e.g. "叶背黄点").
    """
    images = get_request_images()
    if not images:
        return ("[ERR_NO_IMAGE] 本轮没有可用的图片附件，无法做图像识别。"
                "如果用户只是文字提问，请按知识库/联网证据回答，不要编造图像结论。")
    try:
        index = int(image_index)
    except (TypeError, ValueError):
        index = 0
    if index < 0 or index >= len(images):
        names = "；".join(f"{i}. {img.name}" for i, img in enumerate(images))
        return (f"[ERR_IMAGE_INDEX] image_index={index} 超出范围；本轮共 {len(images)} 张图片："
                f"{names}。请用 0..{len(images) - 1} 之间的序号重试。")

    image = images[index]
    diag = get_engine().diagnose_data_url(image.url)
    report = format_report(diag, image_name=image.name or f"第 {index + 1} 张图片")
    if focus and str(focus).strip():
        report += f"\n用户关注的症状：{str(focus).strip()[:80]}（回答时请对应该症状描述，不要跑题）"
    logger.info("[Vision] disease_diagnose index=%d status=%s top=%s runs=%d views=%d "
                "stage2=%s providers=%s elapsed_ms=%d quality=%s",
                index, diag.status, diag.top[:1], diag.inference_runs, diag.views,
                diag.stage2_triggered, diag.providers, diag.elapsed_ms,
                diag.quality_issues)
    if diag.status in {"error", "no_model", "low_quality", "uncertain"}:
        report += ("\n（主模型后续处理：若当前模型支持读取本轮原图，可结合可见特征与自身知识"
                   "做独立、谨慎的补充判断；须明确这是模型推断，并说明不确定性。"
                   "若图片无法辨读，应请用户补充清晰照片或症状信息。）")
    report += (f"\n（如需用药或防治方案，请检索本地文献/联网并标注来源；"
               f"不得由图像分类直接推导农药用量。可识别范围包括："
               f"{label_bank.covered_zh_list(label_bank.CITRUS_KEYS)}。）")
    return report
