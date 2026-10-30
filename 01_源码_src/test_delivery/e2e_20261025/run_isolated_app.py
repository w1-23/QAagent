"""Run the current CitrusQA source against isolated session/account state."""
from pathlib import Path
import os
import sys

SOURCE_ROOT = Path(__file__).resolve().parents[2]
RUNTIME_ROOT = Path(__file__).resolve().parent / "runtime"
RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(SOURCE_ROOT))
AGENT_DATA_ROOT = SOURCE_ROOT.parent / "agent"
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
# These are fixed to the persistent project caches before importing FastEmbed
# or the reranker. Ambient per-user cache variables must not redirect an
# isolated session into an empty temporary model directory.
os.environ["FASTEMBED_CACHE_PATH"] = str(AGENT_DATA_ROOT / ".hf_cache" / "fastembed")
os.environ["HF_HOME"] = str(AGENT_DATA_ROOT / ".hf_cache" / "hf")
os.environ["CITRUSQA_MODEL_CACHE_ROOT"] = str(AGENT_DATA_ROOT / ".hf_cache")
os.environ["HF_HUB_OFFLINE"] = "1"

# Load the existing local provider/model configuration and credentials first,
# then redirect writable session, account, audit, and artifact state to this
# E2E folder. The configured corpus/model cache stays available read-only.
import src.config as config  # noqa: E402
config.PROJECT_ROOT = RUNTIME_ROOT

from src.api.main import app  # noqa: E402
import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8011,
                log_level="warning", access_log=False, workers=1)
