#!/usr/bin/env python3
"""Compatibility entry point for the two-judge dispatch workflow."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "A01_llm_judge" / "20_dispatch_judge_1_and_2.py"
raise SystemExit(subprocess.call([sys.executable, str(target), *sys.argv[1:]]))
