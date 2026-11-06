#!/usr/bin/env bash
# Isolated CitrusScholar experiment service launcher.
# Binds loopback only (127.0.0.1:8012). Uses its own data root, state, sessions,
# uploads, artifacts and logs. Never touches the production 8000 instance.
set -euo pipefail

ROOT="/home/WZQ/citrus_experiment"
REPO="$ROOT/checkout"
DATA_ROOT="$REPO/agent"

mkdir -p "$DATA_ROOT/state" "$DATA_ROOT/logs" "$DATA_ROOT/workspace" "$DATA_ROOT/tmp" \
         "$ROOT/logs" "$ROOT/uploads" "$ROOT/artifacts" "$ROOT/tmp"
chmod 700 "$DATA_ROOT/state" "$DATA_ROOT/logs" "$ROOT/tmp" 2>/dev/null || true

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export TMPDIR="$ROOT/tmp"
export TEMP="$ROOT/tmp"
export TMP="$ROOT/tmp"
export XDG_CACHE_HOME="$DATA_ROOT/.cache"
export HF_HOME="$DATA_ROOT/.hf_cache/hf"
export HF_HUB_OFFLINE=1
export FASTEMBED_CACHE_PATH="$DATA_ROOT/.hf_cache/fastembed"
export CITRUSQA_CPU_THREADS="${CITRUSQA_CPU_THREADS:-8}"
export CITRUSQA_HOST="${CITRUSQA_HOST:-127.0.0.1}"
export CITRUSQA_PORT="${CITRUSQA_PORT:-8012}"
export PYTHONPATH="$REPO/01_src${PYTHONPATH:+:$PYTHONPATH}"

cd "$REPO/01_src"
exec "$ROOT/venv/bin/python" -m uvicorn src.api.main:app \
  --host "$CITRUSQA_HOST" \
  --port "$CITRUSQA_PORT" \
  --proxy-headers \
  --forwarded-allow-ips=127.0.0.1 \
  --workers 1
