#!/usr/bin/env python3
"""Compatibility entry point for the unified validity packet builder."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "build_validity_review_packet.py"
raise SystemExit(subprocess.call([sys.executable, str(target), *sys.argv[1:]]))
