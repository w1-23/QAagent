#!/usr/bin/env python3
"""Compatibility entry point for fail-closed judge aggregation."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "aggregate_llm_judges.py"
raise SystemExit(subprocess.call([sys.executable, str(target), *sys.argv[1:]]))
