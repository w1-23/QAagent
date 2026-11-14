#!/usr/bin/env python3
"""Shared GPU runtime entrypoint: require actual models before binding 8030.

No model download/index build. Minimal cached-model inference is an operator's
release readiness check, never invoked by the local synthetic tooling tests.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys


def require_ready(evidence):
    models = evidence.get("models", {})
    if (evidence.get("ready") is not True or evidence.get("require_gpu") is not True
            or evidence.get("mode") != "gpu" or set(models) != {"embedder", "reranker"}):
        raise RuntimeError("Strict loaded-model GPU readiness failed")
    for name, model in models.items():
        if (not model.get("loaded") or not model.get("gpu") or not model.get("inference_verified")
                or model.get("runtime_error")):
            raise RuntimeError(f"{name} has no verified loaded GPU inference")
    return evidence


def require_cuda_runtime(ort, probe_provider):
    """The inference stack is ORT: require numeric Gemm AND profiled CUDA kernels."""
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDAExecutionProvider is required for the ONNX inference stack")
    preload = getattr(ort, "preload_dlls", None)
    if callable(preload):
        # ORT 1.21+: use installed nvidia site-packages, never a shell loader path.
        preload(directory="")
    usable, reason = probe_provider("CUDAExecutionProvider")
    if usable is not True:
        raise RuntimeError(f"Actual ONNX CUDA kernel/numeric probe failed: {reason}")
    return reason


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    if not (source / "src/api/main.py").is_file():
        raise RuntimeError("Not the staged application source")
    if (os.environ.get("CITRUS_REQUIRE_GPU") != "1" or os.environ.get("CITRUS_ONNX_DEVICE") != "gpu"
            or os.environ.get("CITRUS_REQUIRE_FIGURE_WORKER") != "1"):
        raise RuntimeError("Service must explicitly require GPU and a verified CPU figure worker")
    mode = os.environ.get("CITRUS_RUNTIME_MODE", "apptainer")
    if mode not in {"apptainer", "conda"}:
        raise RuntimeError("Unknown GPU application runtime mode")
    sys.path.insert(0, str(source))
    os.chdir(source)
    import onnxruntime as ort
    from src.engine.hardware import probe_provider, runtime_readiness
    cuda_probe = require_cuda_runtime(ort, probe_provider)
    from src.config import settings
    from src.figures.sandbox import select_backend
    if (settings.FIGURES_SANDBOX_BACKEND != "apptainer"
            or settings.FIGURES_SANDBOX_ALLOW_LOCAL
            or settings.FIGURES_SANDBOX_IMAGE != "/home/WZQ/CitrusQA/container/citrusqa.sif"
            or settings.FIGURES_SANDBOX_IMAGE_SHA256 != "268295ac77d715a44c6867292f7a6b07d41b27fbb9a48aaa654d8e0c63900551"):
        raise RuntimeError("CPU figure sandbox must use the separately pinned base SIF")
    runner = select_backend(backend=settings.FIGURES_SANDBOX_BACKEND, image=settings.FIGURES_SANDBOX_IMAGE,
                            image_sha256=settings.FIGURES_SANDBOX_IMAGE_SHA256,
                            runtime=settings.FIGURES_SANDBOX_RUNTIME, allow_local=False)
    if runner is None:
        raise RuntimeError("CPU figure sandbox isolation/cgroup probe failed in the application runtime")
    from src.engine.embedder import Embedder
    from src.engine.reranker import Reranker
    Embedder().embed_query("CitrusSchalor startup readiness")
    Reranker().rerank("startup readiness", [{"text": "CitrusSchalor startup readiness"}], top_k=1)
    evidence = require_ready(runtime_readiness())
    print(json.dumps({"event": "S6_GPU_READY", "runtime_mode": mode, "release_id": os.environ.get("CITRUS_RELEASE_ID"),
                      "cuda_kernel_probe": cuda_probe, "models": evidence["models"]}), flush=True)
    from src.api.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8030,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", workers=1)


if __name__ == "__main__":
    main()
