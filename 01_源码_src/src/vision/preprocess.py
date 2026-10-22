"""图像预处理：解码 → 叶部前景框 → letterbox 多视图（不居中裁剪）。

设计约定（与冒烟测试结论一致）：
- **不裁剪主体**：病虫害在图上的位置不固定（叶尖、叶缘、叶背），居中裁剪会把
  病斑裁掉。统一 letterbox 缩放后整幅送入，另加若干局部视图做“投票”。
- **多视图**：整幅 + 叶部前景框 + 中央 72% + 四象限 60%，最多 7 个视图；
  单视图漏检的部位由其它视图补回，概率按视图取最大后归一。
- 只依赖 PIL + numpy，不引入 torch / torchvision。
"""
from __future__ import annotations

import io
from typing import Iterable, List, Tuple

import numpy as np
from PIL import Image, ImageOps

#: 解码后长边上限（4032×3024 的实拍图降到 1600 已足够，且更快）。
MAX_SIDE = 1600

#: 单张图片最大字节数（与 API 附件限制一致）。
MAX_BYTES = 20 * 1024 * 1024


class ImageError(ValueError):
    """无法作为图像处理的输入。"""


def decode_image(payload: bytes) -> Image.Image:
    """把原始字节解码成 RGB 图（自动转正 EXIF 方向并限制尺寸）。"""
    if not payload:
        raise ImageError("空图片数据")
    if len(payload) > MAX_BYTES:
        raise ImageError(f"图片超过 {MAX_BYTES // (1024 * 1024)}MB 上限")
    try:
        img = Image.open(io.BytesIO(payload))
        img.load()
    except Exception as exc:  # noqa: BLE001 - 任何解码失败都归为无法识别
        raise ImageError(f"图片解码失败: {type(exc).__name__}") from exc
    img = ImageOps.exif_transpose(img)
    if img.mode != "RGB":
        img = img.convert("RGB")
    if max(img.size) > MAX_SIDE:
        scale = MAX_SIDE / float(max(img.size))
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))),
                         Image.BILINEAR)
    if img.width < 32 or img.height < 32:
        raise ImageError("图片尺寸过小（<32px）")
    return img


def green_box(img: Image.Image) -> Tuple[int, int, int, int] | None:
    """粗略定位叶片：在 96×96 缩略图上按“绿色占优”取前景外框。

    找不到（枯叶、黄化、果子特写、背景干扰）时返回 ``None``，调用方退回整幅视图。
    """
    probe = img.copy()
    probe.thumbnail((96, 96), Image.BILINEAR)
    arr = np.asarray(probe, dtype=np.float32)
    if arr.ndim != 3 or arr.shape[2] < 3:
        return None
    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    mask = (g - np.maximum(r, b)) > 10.0
    ratio = float(mask.mean())
    if ratio < 0.03 or ratio > 0.97:
        return None
    ys, xs = np.nonzero(mask)
    if len(xs) < 32:
        return None
    sx = img.width / probe.width
    sy = img.height / probe.height
    pad_x = 0.04 * (xs.max() - xs.min()) * sx
    pad_y = 0.04 * (ys.max() - ys.min()) * sy
    x0 = max(0, int(xs.min() * sx - pad_x))
    y0 = max(0, int(ys.min() * sy - pad_y))
    x1 = min(img.width, int((xs.max() + 1) * sx + pad_x))
    y1 = min(img.height, int((ys.max() + 1) * sy + pad_y))
    if x1 - x0 < 64 or y1 - y0 < 64:
        return None
    if (x1 - x0) * (y1 - y0) < 0.04 * img.width * img.height:
        return None
    return x0, y0, x1, y1


def _crop(img: Image.Image, box: Iterable[float]) -> Image.Image:
    w, h = img.size
    x0, y0, x1, y1 = box
    return img.crop((int(x0 * w), int(y0 * h), int(x1 * w), int(y1 * h)))


def view_crops(img: Image.Image) -> List[Tuple[str, Image.Image]]:
    """返回带名字的视图列表：整幅、叶部框、中央、四象限。"""
    views: List[Tuple[str, Image.Image]] = [("full", img)]
    box = green_box(img)
    if box is not None:
        views.append(("leaf", img.crop(box)))
    views.append(("center", _crop(img, (0.14, 0.14, 0.86, 0.86))))
    views.extend([
        ("tl", _crop(img, (0.0, 0.0, 0.6, 0.6))),
        ("tr", _crop(img, (0.4, 0.0, 1.0, 0.6))),
        ("bl", _crop(img, (0.0, 0.4, 0.6, 1.0))),
        ("br", _crop(img, (0.4, 0.4, 1.0, 1.0))),
    ])
    return views


def letterbox(img: Image.Image, size: int) -> Image.Image:
    """等比缩放到 size×size，空白处填 0（不裁剪）。"""
    w, h = img.size
    scale = size / float(max(w, h))
    nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
    resized = img.resize((nw, nh), Image.BICUBIC)
    canvas = Image.new("RGB", (size, size), (0, 0, 0))
    canvas.paste(resized, ((size - nw) // 2, (size - nh) // 2))
    return canvas


def to_input(img: Image.Image, size: int, mean, std) -> np.ndarray:
    """生成 (1, 3, H, W) 的 float32 张量（ImageNet 归一化参数由模型元数据给出）。"""
    canvas = letterbox(img, size)
    arr = np.asarray(canvas, dtype=np.float32) / 255.0
    mean_arr = np.asarray(mean, dtype=np.float32).reshape(1, 1, 3)
    std_arr = np.asarray(std, dtype=np.float32).reshape(1, 1, 3)
    arr = (arr - mean_arr) / std_arr
    return np.ascontiguousarray(np.transpose(arr, (2, 0, 1))[None, ...])
