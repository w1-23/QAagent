"""Explicit worker refresh using only the drawing engine as build context.

Normal local startup never calls this or downloads/builds an image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "01_src"))
from src.figures.wsl_docker import WslDockerBackend

from src.figures.engine_bundle import stage_engine


def prepare_context(context: Path, base: str, install_dependencies: bool) -> None:
    digests = stage_engine(context / "engine")
    requirements = (ROOT / "01_src/docker/requirements.sandbox.txt").read_bytes()
    (context / "requirements.txt").write_bytes(requirements)
    digests["requirements.sandbox.txt"] = hashlib.sha256(requirements).hexdigest()
    # Embed source hashes in the Dockerfile, independently of copied bytes.
    # A truncated COPY must fail before the destination tag is updated.
    integrity = ("import hashlib; from pathlib import Path; "
                 "root=Path('/opt/citrusfig'); "
                 f"expected={digests!r}; "
                 "bad=[name for name,digest in expected.items() "
                 "if hashlib.sha256((root/name).read_bytes()).hexdigest()!=digest]; "
                 "assert not bad, 'Worker bundle content mismatch: '+str(bad)")
    lines = [f"FROM {base}", "COPY requirements.txt /opt/citrusfig/requirements.sandbox.txt"]
    if install_dependencies:
        lines += ["USER root", "RUN pip install --no-cache-dir -r /opt/citrusfig/requirements.sandbox.txt",
                  "USER 65534:65534"]
    # Fail the build before retagging when an old base lacks the pinned runtime.
    check = ("from importlib.metadata import version; from pathlib import Path; "
             "requirements=Path('/opt/citrusfig/requirements.sandbox.txt').read_text().splitlines(); "
             "pairs=[line.strip().split('==') for line in requirements "
             "if line.strip() and not line.lstrip().startswith('#')]; "
             "assert all(version(name)==wanted for name,wanted in pairs), "
             "'Runtime mismatch: rerun with --install-table-engines or use a matching base image'")
    lines += ["RUN " + json.dumps(["python", "-c", check]), "COPY engine/ /opt/citrusfig/",
              "RUN " + json.dumps(["python", "-I", "-c", integrity])]
    (context / "Dockerfile").write_text("\n".join(lines) + "\n", encoding="utf-8")


def context_archive(context: Path) -> bytes:
    """Stream explicit bytes instead of a WSL/DrvFS path-based build context."""
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for path in sorted(context.rglob("*")):
            if path.is_dir():
                continue
            data = path.read_bytes()
            member = tarfile.TarInfo(path.relative_to(context).as_posix())
            member.size = len(data)
            member.mode = 0o644
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--image", default="citrusqa/sandbox:1.0.0")
    parser.add_argument("--base-image", default="", help="Build a separate tag from this local base")
    parser.add_argument("--install-table-engines", action="store_true",
                        help="Explicitly install the declared pandas spreadsheet/Seaborn dependencies")
    args = parser.parse_args()
    runner = WslDockerBackend(image=args.base_image or args.image, distribution=args.distribution)
    original = runner.resolve_image()
    backup = "citrusqa/sandbox:before-local-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    runner._control(runner._docker_command("tag", original, backup))
    # Only the engine allowlist enters Docker's context; no service state or keys.
    # Dependencies stay offline unless explicitly requested for the new image.
    with tempfile.TemporaryDirectory(prefix="citrusqa-worker-build-") as temporary:
        context = Path(temporary)
        # BuildKit needs the checked local tag, not an image ID in FROM.
        prepare_context(context, backup, args.install_table_engines)
        result = subprocess.run(runner._docker_command(
            "build", "--pull=false", "--network=default" if args.install_table_engines else "--network=none",
            "--tag", args.image, "-"), input=context_archive(context), capture_output=True,
            timeout=600, env=runner._host_env())
        if result.returncode:
            raise RuntimeError("Drawing image build failed: " +
                               result.stderr.decode("utf-8", "replace")[-4000:])
        print(result.stdout.decode("utf-8", "replace"))
        print(result.stderr.decode("utf-8", "replace")[-4000:])
    print(f"Previous image: {backup} ({original})", flush=True)
    runner.image = args.image
    print(f"Updated image: {args.image} ({runner.resolve_image()})", flush=True)


if __name__ == "__main__":
    main()
