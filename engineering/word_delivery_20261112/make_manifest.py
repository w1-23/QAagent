"""Record the exact bytes of this engineering change: git status + SHA-256.

Read-only over the working tree; writes only into this evidence directory.
"""
import csv
import hashlib
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8").stdout


def main():
    status = git("status", "--porcelain")
    rows = []
    for line in status.splitlines():
        code, path = line[:2].strip(), line[3:].strip().strip('"')
        if code not in {"M", "A", "AM"} or path.startswith(("01_src/.test", "engineering/")):
            continue
        full = ROOT / path
        if not full.is_file():
            continue
        data = full.read_bytes()
        tracked = subprocess.run(["git", "ls-files", "--error-unmatch", path], cwd=ROOT,
                                 capture_output=True).returncode == 0
        rows.append({
            "path": path,
            "change": "modified" if code == "M" else "new",
            "tracked_before": "yes" if code == "M" else "no",
            "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        })
    rows.sort(key=lambda r: r["path"])
    with (OUT / "P0_manifest.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=["path", "change", "tracked_before", "bytes", "sha256"])
        writer.writeheader()
        writer.writerows(rows)
    print(f"{len(rows)} files recorded; head={git('rev-parse', 'HEAD').strip()}")
    for row in rows:
        print(f"  {row['change']:8} {row['path']}")


if __name__ == "__main__":
    main()
