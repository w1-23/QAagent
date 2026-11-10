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
# A provider can be *listed* as available and still be unusable on this host: the
# DirectML provider, for instance, completes a handful of inferences and then stops
# making progress, which hangs every request that touches the reranker.  Selection
# therefore probes the candidate once and demotes it permanently for this process.
_PROBE_MODEL = "ep_probe.onnx"
_PROBE_RUNS = 10
_PROBE_TIMEOUT_SEC = 60.0
_probe_cache: dict[str, tuple[bool, str]] = {}


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


def _probe_model_path() -> "os.PathLike[str] | None":
    """Build a tiny Gemm model once; it is only used to exercise an EP."""
    path = PROJECT_ROOT / "state" / _PROBE_MODEL
    if path.is_file():
        return path
    try:
        import numpy as np
        import onnx
        from onnx import TensorProto, helper, numpy_helper
    except Exception as exc:  # pragma: no cover - optional dependency
        logger.warning("[Hardware] Cannot build EP probe model: %s", exc)
        return None
    weight = numpy_helper.from_array(
        np.eye(8, dtype=np.float32) * 0.5, name="w")
    node = helper.make_node("Gemm", ["x", "w"], ["y"], alpha=1.0, beta=0.0)
    graph = helper.make_graph(
        [node], "ep_probe",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [4, 8])],
        [helper.make_tensor_value_info("y", TensorProto.FLOAT, [4, 8])],
        [weight])
    model = helper.make_model(
        graph, opset_imports=[helper.make_opsetid("", 17)],
        producer_name="citrus-qa-ep-probe")
    model.ir_version = 9
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(model.SerializeToString())
    return path


def probe_provider(provider: str) -> tuple[bool, str]:
    """Return (usable, reason). Runs the probe in a watchdog thread so a hung EP
    is reported as a failure instead of blocking startup forever."""
    if provider in _probe_cache:
        return _probe_cache[provider]
    if provider == "CPUExecutionProvider":
        _probe_cache[provider] = (True, "cpu")
        return _probe_cache[provider]
    path = _probe_model_path()
    if path is None:
        _probe_cache[provider] = (True, "probe-unavailable")
        return _probe_cache[provider]

    result: dict[str, object] = {}

    def _run() -> None:
        try:
            import numpy as np
            session = ort.InferenceSession(
                str(path), providers=[provider, "CPUExecutionProvider"])
            data = np.ones((4, 8), dtype=np.float32)
            for _ in range(_PROBE_RUNS):
                session.run(None, {"x": data})
            result["ok"] = True
            result["reason"] = str(session.get_providers()[0])
        except BaseException as exc:  # noqa: BLE001 - report, never raise
            result["ok"] = False
            result["reason"] = f"{type(exc).__name__}: {exc}"

    thread = threading.Thread(target=_run, name=f"ep-probe-{provider}", daemon=True)
    thread.start()
    thread.join(_PROBE_TIMEOUT_SEC)
    if thread.is_alive():
        usable, reason = False, f"no progress after {_PROBE_RUNS} runs (>{_PROBE_TIMEOUT_SEC:.0f}s)"
    else:
        usable = bool(result.get("ok"))
        reason = str(result.get("reason") or "probe failed")
    _probe_cache[provider] = (usable, reason)
    if not usable:
        logger.error(
            "[Hardware] Execution provider %s is advertised but unusable (%s); "
            "falling back to CPU. Set onnx.provider_priority to override.",
            provider, reason)
    else:
        logger.info("[Hardware] Execution provider %s passed the probe (%s)", provider, reason)
    return _probe_cache[provider]


def _usable_provider_order(providers: list[str]) -> list[str]:
    """Keep the configured order but drop GPU providers that fail the probe."""
    kept: list[str] = []
    for provider in providers:
        if provider in _GPU_PROVIDERS:
            ok, _reason = probe_provider(provider)
            if not ok:
                continue
        kept.append(provider)
    return kept or ["CPUExecutionProvider"]


def mark_provider_unusable(provider: str, reason: str) -> None:
    """Record at runtime that a provider is broken, so later sessions skip it."""
    if not provider or provider == "CPUExecutionProvider":
        return
    _probe_cache[provider] = (False, reason)
    logger.error("[Hardware] Marking %s unusable for this process: %s", provider, reason)
    try:
        from src.engine.embedder import Embedder
        if Embedder._instance is not None:
            Embedder._instance.reconfigure()
    except Exception:
        pass


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
        preferred = _usable_provider_order(preferred)
        if preferred and preferred[0] in _GPU_PROVIDERS:
            return preferred
        logger.error("[Hardware] GPU mode requested but no usable GPU provider is available")
        return ["CPUExecutionProvider"]
    matched = [
        provider for provider in _provider_priority()
        if provider in available
    ]
    return _usable_provider_order(matched)


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
        "provider_probe": {name: {"usable": ok, "reason": reason}
                           for name, (ok, reason) in _probe_cache.items()},
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
