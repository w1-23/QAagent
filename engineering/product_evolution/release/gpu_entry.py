#!/usr/bin/env python3
"""GPU SIF entrypoint: fail before binding 8030 unless actual models are ready.

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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    args = parser.parse_args()
    source = Path(args.source).resolve()
    if not (source / "src/api/main.py").is_file():
        raise RuntimeError("Not the staged application source")
    if os.environ.get("CITRUS_REQUIRE_GPU") != "1" or os.environ.get("CITRUS_ONNX_DEVICE") != "gpu":
        raise RuntimeError("Service must explicitly require GPU")
    sys.path.insert(0, str(source))
    os.chdir(source)
    import torch
    import onnxruntime as ort
    if not torch.cuda.is_available() or "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDA tensor runtime and CUDAExecutionProvider are required")
    tensor = torch.tensor([2.0, 3.0], device="cuda")
    if (tensor * tensor).sum().item() != 13.0:
        raise RuntimeError("Actual CUDA tensor calculation failed")
    torch.cuda.synchronize()
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
        raise RuntimeError("CPU figure sandbox isolation/cgroup probe failed INSIDE the GPU app SIF")
    from src.engine.embedder import Embedder
    from src.engine.reranker import Reranker
    from src.engine.hardware import runtime_readiness
    Embedder().embed_query("CitrusSchalor startup readiness")
    Reranker().rerank("startup readiness", [{"text": "CitrusSchalor startup readiness"}], top_k=1)
    evidence = require_ready(runtime_readiness())
    print(json.dumps({"event": "S6_GPU_READY", "release_id": os.environ.get("CITRUS_RELEASE_ID"),
                      "models": evidence["models"]}), flush=True)
    from src.api.main import app
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8030,
                proxy_headers=True, forwarded_allow_ips="127.0.0.1", workers=1)


if __name__ == "__main__":
    main()
