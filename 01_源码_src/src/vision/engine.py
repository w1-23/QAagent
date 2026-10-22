"""模型推理 + 跨模型融合 + 拒识规则 + 给模型的文本报告。

融合口径（来自 59 张真实照片冒烟测试的结论）：
- 三个模型各自对若干个视图（全图/中心/2×2 切片，最多 7 个）做最大池化，得到每类概率；
- 跨模型用“加权噪声或”合并：``score = 1 - Π(1 - w·p)``，再归一化；
  mnv2 权重低（0.35），因为它对柑橘样本的 top-1 只有 ~17%，只用来提示黄龙病；
- 不做“最大池化直接取最大”的朴素融合——实测那样会让未覆盖病害整片塌成“健康”。

拒识优先于硬答（这是产品的红线）：
- top-1 < ``confidence_threshold`` 或 top1-top2 < ``margin`` → ``uncertain``；
- top-1 是“健康”但存在 ≥ ``healthy_veto`` 的病害信号 → ``uncertain``，
  并提示“症状可能不在 10 类覆盖范围”（缺素/虫害/药害的典型陷阱）。
"""
from __future__ import annotations

import base64
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from src.vision import labels as label_bank
from src.vision import models as model_registry
from src.vision.preprocess import decode_image, to_input, view_crops

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE = 0.60
DEFAULT_MARGIN = 0.12
DEFAULT_HEALTHY_VETO = 0.25


#: 实际执行提供器按“硬件加速优先”展示，而不是按字母序
_PROVIDER_ORDER = ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider")


def _ordered_providers(names) -> list[str]:
    def rank(name: str) -> tuple[int, str]:
        try:
            return (_PROVIDER_ORDER.index(name), name)
        except ValueError:
            return (len(_PROVIDER_ORDER), name)
    return sorted(set(names), key=rank)


def _softmax(logits: np.ndarray) -> np.ndarray:
    arr = np.asarray(logits, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return arr
    arr = arr - float(np.max(arr))
    exp = np.exp(arr)
    total = float(exp.sum())
    if not np.isfinite(total) or total <= 0:
        return np.full(arr.shape, 1.0 / arr.size, dtype=np.float64)
    return exp / total


@dataclass
class Diagnosis:
    status: str
    top: list[tuple[str, float]] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)
    models_used: list[str] = field(default_factory=list)
    models_missing: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    views: int = 0
    elapsed_ms: int = 0
    image_size: tuple[int, int] = (0, 0)
    notes: list[str] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "top": [{"key": k, "zh": label_bank.zh_name(k), "prob": round(p, 4)}
                    for k, p in self.top],
            "models_used": list(self.models_used),
            "models_missing": list(self.models_missing),
            "providers": list(self.providers),
            "views": self.views,
            "elapsed_ms": self.elapsed_ms,
            "image_size": list(self.image_size),
            "notes": list(self.notes),
            "error": self.error,
        }


class VisionEngine:
    def __init__(self, model_dir: Path | str | None = None,
                 confidence_threshold: float = DEFAULT_CONFIDENCE,
                 margin: float = DEFAULT_MARGIN,
                 healthy_veto: float = DEFAULT_HEALTHY_VETO,
                 allow_unlicensed: bool = False):
        self.model_dir = Path(model_dir) if model_dir else None
        self.confidence_threshold = float(confidence_threshold)
        self.margin = float(margin)
        self.healthy_veto = float(healthy_veto)
        self.allow_unlicensed = bool(allow_unlicensed)

    # ── 单模型 ──
    def _model_scores(self, spec, crops) -> dict[str, float] | None:
        session = model_registry.get_session(spec, self.model_dir)
        if session is None:
            return None
        input_name = session.get_inputs()[0].name
        pooled: dict[str, float] = {}
        for _view_name, crop in crops:
            tensor = to_input(crop, spec.input_size, spec.mean, spec.std)
            try:
                outputs = session.run(None, {input_name: tensor})
            except Exception as exc:  # noqa: BLE001
                logger.warning("[Vision] %s 推理失败: %s", spec.key, exc)
                return None
            probs = _softmax(np.asarray(outputs[0]).reshape(-1))
            for index, key in spec.mapping.items():
                if index < probs.size:
                    pooled[key] = max(pooled.get(key, 0.0), float(probs[index]))
        return pooled

    # ── 融合 ──
    def _fuse(self, per_model: list[tuple[object, dict[str, float]]]) -> dict[str, float]:
        fused: dict[str, float] = {}
        for spec, scores in per_model:
            weight = getattr(spec, "weight", 1.0)
            for key, prob in scores.items():
                contribution = max(0.0, min(1.0, weight * float(prob)))
                fused[key] = 1.0 - (1.0 - fused.get(key, 0.0)) * (1.0 - contribution)
        total = sum(fused.values())
        if total <= 0:
            return {}
        return {key: value / total for key, value in fused.items()}

    # ── 对外 ──
    def diagnose_image(self, img) -> Diagnosis:
        started = time.monotonic()
        per_model: list[tuple[object, dict[str, float]]] = []
        used: list[str] = []
        missing: list[str] = []
        providers: set[str] = set()
        crops = view_crops(img)
        specs = model_registry.enabled_specs(self.allow_unlicensed)
        skipped = [spec.key for spec in model_registry.SPECS if spec not in specs]
        for spec in specs:
            scores = self._model_scores(spec, crops)
            if scores is None:
                missing.append(spec.key)
                continue
            session = model_registry.get_session(spec, self.model_dir)
            if session is not None:
                providers.update(session.get_providers())
            per_model.append((spec, scores))
            used.append(spec.key)
        if not per_model:
            return Diagnosis(
                status="no_model",
                models_missing=missing,
                providers=_ordered_providers(providers),
                elapsed_ms=int((time.monotonic() - started) * 1000),
                image_size=tuple(img.size),
                error="未找到可用的病害识别模型文件",
            )

        fused = self._fuse(per_model)
        ranked = sorted(fused.items(), key=lambda kv: -kv[1])
        top = ranked[:3]
        top1_key, top1_prob = ranked[0] if ranked else ("", 0.0)
        top2_prob = ranked[1][1] if len(ranked) > 1 else 0.0
        margin = top1_prob - top2_prob
        disease_signal = max([value for key, value in fused.items()
                              if key != label_bank.HEALTHY] or [0.0])

        notes: list[str] = []
        if skipped:
            notes.append(
                "按许可策略未加载：" + ", ".join(skipped) +
                "（权重许可不明；可在 config.yaml 的 vision.allow_unlicensed_models 打开）")
        status = "ok"
        if top1_prob < self.confidence_threshold:
            status = "uncertain"
            notes.append(
                f"最高置信度只有 {top1_prob:.2f}（阈值 {self.confidence_threshold:.2f}），"
                "图像证据不足以支撑单一结论")
        if margin < self.margin:
            status = "uncertain"
            notes.append(
                f"最高分与次高分过于接近（{top1_prob:.2f} vs {top2_prob:.2f}），"
                "三个模型之间没有形成一致判断")
        if top1_key == label_bank.HEALTHY and disease_signal >= self.healthy_veto:
            status = "uncertain"
            notes.append(
                f"模型倾向“健康”，但仍有 {disease_signal:.2f} 的病害信号；"
                "缺素、虫害、药害的照片常被误判为健康")
        if top1_key and label_bank.is_weak(top1_key):
            notes.append(label_bank.label(top1_key).note)

        return Diagnosis(
            status=status,
            top=top,
            scores=fused,
            models_used=used,
            models_missing=missing,
            providers=_ordered_providers(providers),
            views=len(crops),
            elapsed_ms=int((time.monotonic() - started) * 1000),
            image_size=tuple(img.size),
            notes=notes,
        )

    def diagnose_bytes(self, payload: bytes) -> Diagnosis:
        try:
            img = decode_image(payload)
        except Exception as exc:  # noqa: BLE001
            return Diagnosis(status="error", error=str(exc))
        try:
            return self.diagnose_image(img)
        except Exception as exc:  # noqa: BLE001
            logger.exception("[Vision] 推理异常")
            return Diagnosis(status="error", error=f"{type(exc).__name__}: {exc}")

    def diagnose_data_url(self, data_url: str) -> Diagnosis:
        payload = data_url_to_bytes(data_url)
        if payload is None:
            return Diagnosis(status="error", error="图片数据格式无法解析（需要 data: URL）")
        return self.diagnose_bytes(payload)


def data_url_to_bytes(data_url: str) -> bytes | None:
    text = str(data_url or "")
    if not text.startswith("data:") or "," not in text:
        return None
    _header, encoded = text.split(",", 1)
    try:
        return base64.b64decode(encoded, validate=False)
    except Exception:  # noqa: BLE001
        return None


_ENGINE: VisionEngine | None = None


def get_engine() -> VisionEngine:
    """进程级单例（会话缓存本身在 models 层）。"""
    global _ENGINE
    if _ENGINE is None:
        model_dir = None
        confidence = DEFAULT_CONFIDENCE
        margin = DEFAULT_MARGIN
        veto = DEFAULT_HEALTHY_VETO
        allow_unlicensed = False
        try:
            from src.config import settings
            model_dir = str(getattr(settings, "VISION_MODEL_DIR", "") or "") or None
            confidence = float(getattr(settings, "VISION_CONFIDENCE_THRESHOLD", confidence))
            margin = float(getattr(settings, "VISION_MARGIN_THRESHOLD", margin))
            veto = float(getattr(settings, "VISION_HEALTHY_VETO", veto))
            allow_unlicensed = bool(getattr(settings, "VISION_ALLOW_UNLICENSED", False))
        except Exception:  # noqa: BLE001
            pass
        _ENGINE = VisionEngine(model_dir=model_dir, confidence_threshold=confidence,
                               margin=margin, healthy_veto=veto,
                               allow_unlicensed=allow_unlicensed)
    return _ENGINE


def reset_engine() -> None:
    global _ENGINE
    _ENGINE = None
    model_registry.clear_cache()


# ── 报告 ──
def format_report(diag: Diagnosis, *, image_name: str = "图片") -> str:
    """把诊断结果写成模型可读的中文报告（工具返回值）。"""
    head = (f"【图像病害初筛】{image_name} · {diag.image_size[0]}×{diag.image_size[1]} · "
            f"{diag.elapsed_ms} ms · 模型 {len(diag.models_used)} 个"
            f"（{', '.join(diag.models_used) or '无'}）")
    if diag.providers:
        head += f" · EP {diag.providers[0]}"

    if diag.status == "error":
        return f"{head}\n状态：识别失败（{diag.error}）。请提示用户换一张更清晰的叶片近景照片。"

    if diag.status == "no_model":
        return (f"{head}\n状态：本机未安装病害识别模型（{', '.join(diag.models_missing)}），"
                "无法给出图像结论。请说明“图像识别模型未就绪”，不要凭外观下诊断。")

    lines = [head]
    if diag.status == "ok":
        key, prob = diag.top[0]
        lines.append(f"结论：疑似「{label_bank.zh_name(key)}」（置信度 {prob:.0%}）")
    else:
        lines.append("结论：无法确定（图像证据不足或模型间不一致），不给单一病名")

    if diag.top:
        ranking = "；".join(f"{label_bank.zh_name(k)} {p:.0%}" for k, p in diag.top)
        lines.append(f"候选排序：{ranking}")

    lines.append(f"可识别范围（{len(label_bank.CITRUS_KEYS)} 类，柑橘样本训练）："
                 f"{label_bank.covered_zh_list(label_bank.CITRUS_KEYS)}")
    if label_bank.WEAK_KEYS:
        lines.append("弱参考标签（非柑橘样本训练，仅供参考，不可当作初筛结论）："
                     f"{label_bank.covered_zh_list(label_bank.WEAK_KEYS)}")
    lines.append(f"不在范围内（需人工判断）：{label_bank.uncovered_zh_list()}")

    for note in diag.notes:
        lines.append(f"注意：{note}")

    if diag.status == "ok":
        key = diag.top[0][0]
        if label_bank.is_weak(key):
            lines.append("提示：命中的是弱参考标签，只能作为“有没有这类病斑迹象”的弱提示，"
                         "必须请用户补充症状描述或做人工复核。")
        if key != label_bank.HEALTHY:
            terms = "、".join(label_bank.kb_terms(key)[:3])
            lines.append(f"知识库检索建议：{terms}")
        else:
            lines.append("知识库检索建议：健康叶片管理、柑橘缺素症鉴别")

    lines.append("使用要求：这是图像初筛，不是诊断结论；必须说明“需结合症状描述、"
                 "栽培环境与专家复核”，不得写成确诊，也不得据此推荐农药用量。")
    return "\n".join(lines)
