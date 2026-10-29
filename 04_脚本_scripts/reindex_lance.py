# -*- coding: utf-8 -*-
"""Compatibility entry point for a full, side-by-side E5 passage index rebuild.

Build and validate with --all. Activate the generation only after review with --activate.
The prior active index remains available for rollback.
"""
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[1] / "01_源码_src"
sys.path.insert(0, str(SRC_ROOT))


def main():
    from src.retrieval.corpus_admin import reindex_main
    return reindex_main()


if __name__ == "__main__":
    raise SystemExit(main())