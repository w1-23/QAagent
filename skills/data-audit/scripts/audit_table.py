"""Review bytes using the installed support distribution."""
import sys
from citrus_audit.cli import main
if __name__=='__main__':
    raise SystemExit(main(['review',*sys.argv[1:]]))
