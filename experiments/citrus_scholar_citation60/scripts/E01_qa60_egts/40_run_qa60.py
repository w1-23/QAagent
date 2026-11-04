#!/usr/bin/env python3
"""Execute only the QA plans through the shared fail-closed runner."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "run_matrix.py"
raise SystemExit(
    subprocess.call([sys.executable, str(target), "--kind", "qa", *sys.argv[1:]])
)
