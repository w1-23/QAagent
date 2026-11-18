"""Launch the real Web/API shell with independent development state and no credentials.

This preview does not load production state, initialize an administrator, or claim
that GPU retrieval, an external model, or the drawing worker has been verified.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8164)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    runtime = args.runtime_root
    if not runtime.is_absolute():
        parser.error("runtime-root must be absolute")
    runtime = runtime.resolve()
    if not runtime.is_relative_to(root / "agent" / "acceptance"):
        parser.error("preview runtime must be beneath this checkout's agent/acceptance")
    runtime.mkdir(parents=True, exist_ok=True)
    # The marker prevents accidentally pointing this launcher at unrelated state.
    marker = runtime / ".isolated-preview"
    if not marker.exists() and any(runtime.iterdir()):
        parser.error("a new preview requires an empty runtime directory")
    marker.touch(exist_ok=True)
    for key in list(os.environ):
        if key.upper().endswith("API_KEY") or key in {"CITRUS_OPENCODE_SESSION", "CITRUS_LOCAL_FULL_STARTUP"}:
            os.environ.pop(key, None)
    os.environ.update(CITRUSQA_PROJECT_ROOT=str(runtime), CITRUSQA_ALLOW_EMPTY_DATA="1",
        CITRUSQA_MODEL_CACHE_ROOT=str(runtime / ".hf_cache"), DATA_DIR=str(runtime / "data"),
        CITRUS_ONNX_DEVICE="gpu", CITRUS_REQUIRE_GPU="0", CITRUS_REQUIRE_FIGURE_WORKER="0",
        FIGURES_SANDBOX_BACKEND="disabled", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
        PYTHONUTF8="1")
    sys.path.insert(0, str(root / "01_src"))
    import uvicorn
    uvicorn.run("src.api.main:app", host="127.0.0.1", port=args.port, reload=False)


if __name__ == "__main__":
    main()
