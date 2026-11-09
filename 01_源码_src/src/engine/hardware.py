"""ONNX Runtime provider selection with a persisted administrator switch."""
from __future__ import annotations

import json
import logging
import os
import threading
from typing import Any

import onnxruntime as ort

from src.config import PROJECT_ROOT, _yaml_val

logger = logging.getLogger(__name__)

VALID_DEVICE_MODES = frozenset({"auto", "gpu", "cpu"})
_DEVICE_LOCK = threading.RLock()
_DEVICE_PATH = PROJECT_ROOT / "state" / "onnx_device.json"
_GPU_PROVIDERS = frozenset({
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "DmlExecutionProvider",
    "TensorrtExecutionProvider",
})
_runtime_mode: str | None = None


def _provider_priority() -> list[str]:
    configured = _yaml_val("onnx", "provider_priority", default=None)
    if configured and isinstance(configured, list):
        return [str(value) for value in configured]
    return [
        "ROCMExecutionProvider",
        "CUDAExecutionProvider",
        "DmlExecutionProvider",
        "CPUExecutionProvider",
    ]


def _read_mode() -> str:
    global _runtime_mode
    with _DEVICE_LOCK:
        if _runtime_mode in VALID_DEVICE_MODES:
            return str(_runtime_mode)
        env_mode = str(os.getenv("CITRUS_ONNX_DEVICE") or "").strip().casefold()
        if env_mode in VALID_DEVICE_MODES:
            _runtime_mode = env_mode
            return env_mode
        try:
            payload = json.loads(_DEVICE_PATH.read_text(encoding="utf-8"))
            persisted = str(payload.get("mode") or "").strip().casefold()
            if persisted in VALID_DEVICE_MODES:
                _runtime_mode = persisted
                return persisted
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.warning("Could not read ONNX device preference: %s", exc)
        _runtime_mode = "auto"
        return "auto"


def _write_mode(mode: str) -> None:
    _DEVICE_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = _DEVICE_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps({"mode": mode}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8")
    os.replace(temporary, _DEVICE_PATH)


def get_device_mode() -> str:
    return _read_mode()


def set_device_mode(mode: str) -> dict[str, Any]:
    global _runtime_mode
    selected = str(mode or "").strip().casefold()
    if selected not in VALID_DEVICE_MODES:
        raise ValueError("mode must be auto, gpu, or cpu")
    available = list(ort.get_available_providers())
    if selected == "gpu" and not any(provider in _GPU_PROVIDERS for provider in available):
        raise RuntimeError("GPU ExecutionProvider is not available in this runtime")
    with _DEVICE_LOCK:
        _write_mode(selected)
        _runtime_mode = selected
    logger.info("[Hardware] ONNX device mode changed | mode=%s | available=%s",
                selected, available)
    return get_device_status()


def get_ort_providers() -> list[str]:
    available = list(ort.get_available_providers())
    mode = _read_mode()
    logger.info("[Hardware] ONNX available providers=%s mode=%s", available, mode)
    if mode == "cpu":
        return ["CPUExecutionProvider"]
    if mode == "gpu":
        preferred = [
            provider for provider in _provider_priority()
            if provider in available and provider in _GPU_PROVIDERS
        ]
        if preferred:
            return preferred
        logger.error("[Hardware] GPU mode requested but no GPU provider is available")
        return ["CPUExecutionProvider"]
    matched = [
        provider for provider in _provider_priority()
        if provider in available
    ]
    return matched or ["CPUExecutionProvider"]


def get_device_status() -> dict[str, Any]:
    available = list(ort.get_available_providers())
    selected = get_ort_providers()
    active: dict[str, list[str]] = {}
    try:
        from src.engine.embedder import Embedder
        if Embedder._instance is not None and Embedder._instance._providers:
            active["embedder"] = list(Embedder._instance._providers)
    except Exception:
        pass
    try:
        from src.engine.reranker import Reranker
        if Reranker._instance is not None and Reranker._instance._providers:
            active["reranker"] = list(Reranker._instance._providers)
    except Exception:
        pass
    return {
        "mode": _read_mode(),
        "available_providers": available,
        "selected_providers": selected,
        "gpu_available": any(provider in _GPU_PROVIDERS for provider in available),
        "active_model_providers": active,
    }


def reconfigure_runtime_models() -> dict[str, Any]:
    """Clear loaded ONNX sessions so the next request recreates them."""
    reloaded: list[str] = []
    try:
        from src.engine.embedder import Embedder
        embedder = Embedder._instance
        if embedder is not None:
            embedder.reconfigure()
            reloaded.append("embedder")
    except Exception as exc:
        logger.warning("Embedder device reconfigure failed: %s", exc)
    try:
        from src.engine.reranker import Reranker
        reranker = Reranker._instance
        if reranker is not None:
            reranker.reconfigure()
            reloaded.append("reranker")
    except Exception as exc:
        logger.warning("Reranker device reconfigure failed: %s", exc)
    status = get_device_status()
    status["reconfigured"] = reloaded
    status["note"] = "现有请求不被中断；下一次嵌入或重排请求会重建会话。"
    return status
