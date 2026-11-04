#!/usr/bin/env python3
"""Generate the core-QA run plan from the locked question file."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "make_run_plan.py"
raise SystemExit(
    subprocess.call([sys.executable, str(target), "--locked", *sys.argv[1:]])
)
