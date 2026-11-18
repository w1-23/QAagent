"""Citrus adapter; upstream resources stay immutable."""
from pathlib import Path
import sys
from citrus_figures.cli import main

if __name__ == "__main__":
    args = sys.argv[1:]
    if not any(arg == "--vendor-root" or arg.startswith("--vendor-root=") for arg in args):
        args += ["--vendor-root", str(Path(__file__).resolve().parents[2] / "vendor/nature-skills")]
    raise SystemExit(main(["render"] + args))
