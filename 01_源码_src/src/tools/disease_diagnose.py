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

    Call it once per image when the user asks what disease/symptom a photo shows, or
    when a photo is the main evidence of the question. Images are addressed by their
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
    logger.info("[Vision] disease_diagnose index=%d status=%s top=%s",
                index, diag.status, diag.top[:1])
    if diag.status in {"error", "no_model"}:
        report += "\n（提示：工具未能给出图像结论时，请如实说明，不要用文字猜测替代。）"
    else:
        report += (f"\n（可继续追问：{label_bank.covered_zh_list(label_bank.CITRUS_KEYS)[:60]}… "
                   "如需用药或防治方案，请检索本地文献/联网，并标注来源。）")
    return report
