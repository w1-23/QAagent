"""Stage the common worker allowlist from an installed support wheel; never build an image."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parents[1]
    # Only this service-owned build tool bootstraps the host facade source.
    # Support implementations are always read from the installed wheel RECORD.
    sys.path.insert(0, str(root / '01_src'))
    from src.figures.engine_bundle import stage_engine
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError('Worker bundle output must be new or empty')
    digests = stage_engine(args.output)
    raw = json.dumps({'schema': 'citrus.worker-bundle/1', 'members': digests},
                     sort_keys=True).encode()
    (args.output / 'worker-manifest.json').write_bytes(raw)
    print(json.dumps({'status': 'staged', 'members': len(digests),
                      'manifest_sha256': hashlib.sha256(raw).hexdigest()}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
