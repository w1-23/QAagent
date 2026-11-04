#!/usr/bin/env python3
"""Strictly summarize core QA60 from real aggregated judge results."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "analyze_and_plot.py"
raise SystemExit(subprocess.call([sys.executable, str(target), "--strict", *sys.argv[1:]]))
