"""Export findings from a saved review without rescanning."""
import sys
from citrus_audit.cli import main
if __name__=='__main__':
    raise SystemExit(main(['export',*sys.argv[1:]]))
