"""Read-only capability projections for public health and administrator diagnostics."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path


def workspace_id() -> str:
    """Opaque checkout identity for safe startup reuse; never publish a host path."""
    root = os.path.normcase(str(Path(__file__).resolve().parents[2]))
    return hashlib.sha256(root.encode("utf-8")).hexdigest()


def complete_model_evidence(status):
    """Both real model sessions must match the administrator's selected mode."""
    items = status.get('models', {})
    gpu_required = bool(status.get('require_gpu')) or status.get('mode', 'gpu') == 'gpu'
    return all(items.get(name, {}).get('loaded')
               and (not gpu_required or items[name].get('gpu'))
               and items[name].get('inference_verified')
               and not items[name].get('runtime_error')
               for name in ('embedder', 'reranker'))


def public_readiness(*, stores, retrieval, models, worker, agent, figure_skill,
                     model_configured, full=False, worker_required=False, version=""):
    """Optional capabilities do not determine basic service availability."""
    model_ready = bool(models.get("ready"))
    if full:
        model_ready = model_ready and complete_model_evidence(models)
    worker_ready = bool(worker.get("verified"))
    figure_ready = bool(figure_skill.get("prompt_loaded"))
    ready = bool(stores and agent.get("ready"))
    if full:
        ready = ready and bool(retrieval and model_ready)
    if worker_required:
        ready = ready and worker_ready and figure_ready
    capabilities = {
        "chat": {"ready": bool(agent.get("ready") and model_configured),
                 "reason_code": "ready" if model_configured else "model_credential_unconfigured"},
        "local_retrieval": {"ready": bool(retrieval and model_ready),
                            "reason_code": "ready" if retrieval and model_ready else "retrieval_unavailable"},
        "figure_worker": {"ready": worker_ready, "required": worker_required,
                          "reason_code": "ready" if worker_ready else "worker_unavailable"},
    }
    degraded = not all(item["ready"] for item in capabilities.values())
    return {
        "ready": ready, "status": ("degraded" if degraded else "ready") if ready else "unavailable",
        "version": version, "stores_ready": bool(stores), "retrieval_ready": bool(retrieval),
        "agent_runtime": {"ready": bool(agent.get("ready"))},
        "models": {"ready": model_ready, "mode": models.get("mode", "gpu"),
                   "require_gpu": bool(models.get("require_gpu"))},
        "worker": {"verified": worker_ready, "required": worker_required,
                   "image_fingerprint": hashlib.sha256(str(worker.get("image_id", "")).encode()).hexdigest(),
                   "status": "ready" if worker_ready else "unavailable"},
        "figure_skill": {"prompt_loaded": figure_ready}, "capabilities": capabilities,
        "local_runtime": ({"profile": "full-v1", "instance_id": os.getenv("CITRUS_LOCAL_INSTANCE_ID", ""),
                           "pid": os.getpid(), "workspace_id": workspace_id()} if full else None),
    }
