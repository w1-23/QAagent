"""Read-only server metadata probe; no account content or secret environment."""
from pathlib import Path
import hashlib
import json
import shutil
import sqlite3


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def main():
    root = Path('/home/WZQ/citrusqa/state')
    inventory = []
    if root.is_dir():
        for path in sorted(root.rglob('*')):
            if path.is_symlink() or not path.is_file() or path.suffix not in {'.db', '.sqlite', '.sqlite3'}:
                continue
            entry = {'path': str(path.relative_to(root)), 'bytes': path.stat().st_size,
                     'sha256': digest(path), 'wal_present': Path(str(path) + '-wal').exists()}
            try:
                # immutable avoids creating or modifying WAL/SHM files. Active
                # WAL is deliberately excluded; this is NOT a consistent backup.
                with sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True) as conn:
                    schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()
                    entry['schema'] = schema
                    entry['schema_sha256'] = hashlib.sha256(json.dumps(schema, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
            except sqlite3.Error as exc:
                entry['error'] = type(exc).__name__
            inventory.append(entry)
    caches = []
    for base in (Path('/home/WZQ/citrusqa'), Path('/home/WZQ/CitrusQA'), Path('/home/WZQ')):
        for relative in ('embedding', 'models', '.hf_cache', '.cache/huggingface', 'src/01_src/embedding'):
            path = base / relative
            if path.is_dir():
                caches.append({'path': str(path), 'resolved': str(path.resolve()),
                               'entries': sorted(p.name for p in path.iterdir())[:40]})
    binary = shutil.which('apptainer')
    print(json.dumps({'kind': 'read_only_metadata', 'state_root': str(root), 'databases': inventory,
                      'cache_candidates': caches, 'apptainer': binary,
                      'apptainer_resolved': str(Path(binary).resolve()) if binary else None,
                      'limitations': ['live DB files may change while hashing', 'immutable SQLite view excludes active WAL',
                                     'not a backup or rollback certification', 'cache directory presence is not model readiness']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
