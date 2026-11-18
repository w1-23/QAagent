"""Requires installed citrusscholar-support[refcheck]==2.0.0."""
import sys
from citrus_refcheck.cli import main
if __name__ == "__main__":
    raise SystemExit(main(["export", *sys.argv[1:]]))
