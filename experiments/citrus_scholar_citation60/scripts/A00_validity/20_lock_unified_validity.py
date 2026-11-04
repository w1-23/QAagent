#!/usr/bin/env python3
"""Write the unified validity lock only after --check passes."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
target = ROOT / "scripts" / "verify_validity_reviews.py"
raise SystemExit(subprocess.call([sys.executable, str(target), "--lock", *sys.argv[1:]]))
