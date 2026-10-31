# -*- coding: utf-8 -*-
"""Compatibility entry point for the manifest-validated, transactional corpus importer.

Use --corpus-id and --family-id. A new corpus requires --display-name.
The index is staged beside the active generation and only switched with --activate.
"""
from pathlib import Path
import sys

SRC_ROOT = Path(__file__).resolve().parents[1] / "01_源码_src"
sys.path.insert(0, str(SRC_ROOT))


def main():
    from src.retrieval.corpus_admin import ingest_main
    return ingest_main()


if __name__ == "__main__":
    raise SystemExit(main())