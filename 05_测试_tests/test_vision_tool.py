# -*- coding: utf-8 -*-
"""病害图像初筛工具测试（v8.18）。

不依赖真实 ONNX 权重：用桩 session 注入 logits，验证
- 标签库覆盖口径（柑橘 10 类 + 弱参考 1 类 + 未覆盖清单）；
- 融合公式与拒识规则（阈值/边际/健康否决）；
- 报告文本必须带“覆盖范围 + 不是诊断”的免责；
- 请求级 contextvar 的取图逻辑与工具错误分支。
"""
import base64
import dataclasses
import importlib.util
import io
import os
import sys

import numpy as np
import pytest
from PIL import Image

# conftest 已把 01_源码_src 放进 sys.path；这里反推仓库根，兼容测试目录被复制到别处运行
_SRC = next((p for p in sys.path if p.endswith("01_源码_src")), None)
_ROOT = os.path.dirname(os.path.abspath(_SRC)) if _SRC else os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))

from src.vision import context as vision_context    # noqa: E402
from src.vision import engine as vision_engine      # noqa: E402
from src.vision import labels as label_bank         # noqa: E402
from src.vision import models as vision_models      # noqa: E402


def _load_tool_module():
    """按路径加载工具模块：import src.tools 会拉起整条应用依赖，测试不需要。"""
    path = os.path.join(_ROOT, "01_源码_src", "src", "tools", "disease_diagnose.py")
    spec = importlib.util.spec_from_file_location("disease_diagnose_under_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _StubSession:
    def __init__(self, logits):
        self._logits = np.asarray(logits, dtype=np.float32).reshape(1, -1)
        self._input = type("_Input", (), {"name": "pixel_values"})()

    def get_inputs(self):
        return [self._input]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, _output_names, _feeds):
        return [self._logits]


def _leaf_png(size=(512, 384)) -> bytes:
    img = Image.new("RGB", size, (190, 190, 185))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    draw.ellipse((45, 40, size[0] - 40, size[1] - 35), fill=(42, 126, 48), outline=(12, 65, 24), width=7)
    for offset in range(0, 240, 18):
        draw.line((90, 80 + offset, size[0] - 90, 50 + offset), fill=(145, 176, 92), width=3)
    for x in range(110, size[0] - 80, 31):
        for y in range(90, size[1] - 60, 27):
            draw.ellipse((x, y, x + 7, y + 7), fill=(104, 77, 34))
    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    return buffer.getvalue()


def _data_url(payload: bytes) -> str:
    return "data:image/png;base64," + base64.b64encode(payload).decode("ascii")


def _install_stub(monkeypatch, logits_by_key):
    def fake_get_session(spec, model_dir=None):
        logits = logits_by_key.get(spec.key)
        return None if logits is None else _StubSession(logits)

    monkeypatch.setattr(vision_models, "get_session", fake_get_session)


def _engine(allow_unlicensed: bool = True) -> vision_engine.VisionEngine:
    return vision_engine.VisionEngine(model_dir="/nonexistent", confidence_threshold=0.60,
                                      margin=0.12, healthy_veto=0.25,
                                      allow_unlicensed=allow_unlicensed)


#: convnext 的 index2=Canker / index4=Healthy；dima 的 index0=canker / index2=melanose
_CANKER_LOGITS = {
    "convnext": [0, 0, 6.5, 0, 0.5, 0, 0, 0],
    "dima": [5.0, 0.1, 0.2],
    "plantvillage": [0.0] * 38,
}


# ── 标签库 ──
def test_label_bank_coverage_split():
    assert len(label_bank.CITRUS_KEYS) == 10
    assert label_bank.WEAK_KEYS == ("powdery_mildew",)
    assert set(label_bank.CITRUS_KEYS) | set(label_bank.WEAK_KEYS) == set(label_bank.LABELS)
    assert "fe_deficiency" in dict(label_bank.UNCOVERED)  # 缺素必须留在“不在范围”里
    assert label_bank.zh_name("canker") == "柑橘溃疡病"


def test_softmax_and_fuse_are_normalized():
    probs = vision_engine._softmax(np.array([1.0, 1.0, 1.0, 1.0]))
    assert float(probs.sum()) == pytest.approx(1.0)

    spec = vision_models.SPECS[0]
    fused = _engine()._fuse([(spec, {"canker": 0.8, "healthy": 0.2}),
                             (spec, {"canker": 0.5})])
    assert sum(fused.values()) == pytest.approx(1.0)
    # 加权噪声或：两路都给病斑信号时只会更高（归一化后 0.82），但必须显著压过另一类
    assert fused["canker"] > 0.8
    assert fused["canker"] > fused["healthy"]


# ── 融合与拒识 ──
def test_clear_lesion_returns_diagnosis(monkeypatch):
    _install_stub(monkeypatch, _CANKER_LOGITS)
    diag = _engine().diagnose_bytes(_leaf_png())
    assert diag.status == "ok"
    assert diag.top[0][0] == "canker"
    assert diag.views >= 1
    assert diag.models_used == ["convnext", "dima", "plantvillage"]
    assert diag.providers == ["CPUExecutionProvider"]


def test_unlicensed_models_skipped_by_default(monkeypatch):
    _install_stub(monkeypatch, _CANKER_LOGITS)
    diag = _engine(allow_unlicensed=False).diagnose_bytes(_leaf_png())
    assert diag.models_used == ["convnext", "dima"]
    assert any("\u8bb8\u53ef\u7b56\u7565" in note for note in diag.notes)


def test_flat_distribution_abstains(monkeypatch):
    _install_stub(monkeypatch, {
        "convnext": [0.0] * 8,
        "dima": [0.0] * 3,
        "plantvillage": [0.0] * 38,
    })
    diag = _engine().diagnose_bytes(_leaf_png())
    assert diag.status == "uncertain"
    assert any("\u8fc7\u4e8e\u63a5\u8fd1" in note for note in diag.notes)


def test_healthy_with_lesion_signal_is_vetoed(monkeypatch):
    _install_stub(monkeypatch, {
        "convnext": [0, 0, 3.5, 0, 4.0, 0, 0, 0],
        "dima": [0.2, 4.0, 0.1],
        "plantvillage": [0.0] * 38,
    })
    diag = _engine().diagnose_bytes(_leaf_png())
    assert diag.status == "uncertain"
    assert any("\u4e0d\u80fd\u636e\u6b64\u786e\u8ba4\u690d\u682a\u5065\u5eb7" in note for note in diag.notes)


def test_missing_models_degrade_gracefully(monkeypatch):
    _install_stub(monkeypatch, {})
    diag = _engine().diagnose_bytes(_leaf_png())
    assert diag.status == "no_model"
    assert set(diag.models_missing) == {"convnext", "dima", "plantvillage"}


def test_broken_image_reports_error():
    diag = _engine().diagnose_bytes(b"not an image")
    assert diag.status == "error"


# ── 报告 ──
def test_report_states_coverage_and_disclaimer(monkeypatch):
    _install_stub(monkeypatch, _CANKER_LOGITS)
    diag = _engine().diagnose_bytes(_leaf_png())
    report = vision_engine.format_report(diag, image_name="test.png")
    assert "\u67d1\u6a58\u6e83\u75a1\u75c5" in report
    assert "\u53ef\u8bc6\u522b\u8303\u56f4\uff0810 \u7c7b" in report
    assert "\u5f31\u53c2\u8003\u6807\u7b7e" in report
    assert "\u4e0d\u5728\u8303\u56f4\u5185" in report
    assert "\u4e0d\u662f\u8bca\u65ad\u7ed3\u8bba" in report
    assert "\u77e5\u8bc6\u5e93\u68c0\u7d22\u5efa\u8bae\uff1a\u67d1\u6a58\u6e83\u75a1\u75c5" in report


def test_report_marks_weak_label(monkeypatch):
    weak_spec = dataclasses.replace(vision_models.SPECS[2], mapping={25: "powdery_mildew"})
    monkeypatch.setattr(vision_models, "SPECS",
                        (vision_models.SPECS[0], vision_models.SPECS[1], weak_spec))
    _install_stub(monkeypatch, {
        "convnext": [0, 0, 0.1, 0, 0.1, 0, 0, 0],
        "dima": [0.1, 0.1, 0.1],
        "plantvillage": [6.0 if i == 25 else 0.0 for i in range(38)],
    })
    diag = _engine().diagnose_bytes(_leaf_png())
    report = vision_engine.format_report(diag)
    assert "\u767d\u7c89\u75c5" in report


# ── contextvar 与工具 ──
def test_request_images_are_request_scoped():
    token = vision_context.set_request_images([
        {"name": "a.png", "url": "data:image/png;base64,AAAA"},
        {"name": "b.pdf", "url": "file:///tmp/b.pdf"},
    ] + [{"name": f"c{i}", "url": "data:image/png;base64,AAAA"} for i in range(10)])
    try:
        images = vision_context.get_request_images()
        assert len(images) == vision_context.MAX_IMAGES
        assert all(img.url.startswith("data:") for img in images)
        assert images[0].name == "a.png"
    finally:
        vision_context.reset_request_images(token)
    assert vision_context.get_request_images() == ()


def test_tool_without_images_returns_marker():
    module = _load_tool_module()
    vision_context.clear_request_images()
    assert "[ERR_NO_IMAGE]" in module.disease_diagnose.invoke({"image_index": 0})


def test_tool_index_out_of_range():
    module = _load_tool_module()
    token = vision_context.set_request_images([
        {"name": "a.png", "url": _data_url(_leaf_png())}])
    try:
        assert "[ERR_IMAGE_INDEX]" in module.disease_diagnose.invoke({"image_index": 3})
    finally:
        vision_context.reset_request_images(token)


def test_tool_returns_report_for_image(monkeypatch):
    module = _load_tool_module()
    _install_stub(monkeypatch, _CANKER_LOGITS)
    monkeypatch.setattr(module, "get_engine", lambda: _engine())
    token = vision_context.set_request_images([
        {"name": "leaf.png", "url": _data_url(_leaf_png())}])
    try:
        text = module.disease_diagnose.invoke({"image_index": 0, "focus": "\u53f6\u80cc\u9ec4\u70b9"})
        assert "\u67d1\u6a58\u6e83\u75a1\u75c5" in text
        assert "\u53f6\u80cc\u9ec4\u70b9" in text
        assert "\u4e0d\u662f\u8bca\u65ad\u7ed3\u8bba" in text
    finally:
        vision_context.reset_request_images(token)

