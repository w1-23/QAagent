import sys
from citrus_retrieval.cli import main
if __name__=='__main__':
    raise SystemExit(main(['read',*sys.argv[1:]]))
