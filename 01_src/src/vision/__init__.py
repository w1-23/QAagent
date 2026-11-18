"""Vision (image-based citrus disease screening) subpackage.

Runtime pieces:
- ``context``:   request-scoped image attachments (set by the API entry point)
- ``preprocess``: decode / leaf-crop / letterbox views (PIL + numpy only)
- ``models``:    ONNX model specs, session cache, provider chain
- ``engine``:    per-model inference, cross-model fusion, abstain rules, report text

The package never trains or downloads anything at import time; the ONNX files are
shipped by ``04_scripts/prepare_models.py --vision``.
"""
__all__ = ["VisionEngine", "get_engine"]


def __getattr__(name):
    # Text-only chat still imports the lightweight request context. Load the
    # optional inference stack only when its public engine is actually requested.
    if name in __all__:
        from src.vision import engine
        return getattr(engine, name)
    raise AttributeError(name)
