"""Explicit worker refresh using only the drawing engine as build context.

Normal local startup never calls this or downloads/builds an image.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "01_src"))
from src.figures.wsl_docker import WslDockerBackend

from src.figures.engine_bundle import stage_engine


def prepare_context(context: Path, base: str, install_dependencies: bool) -> None:
    stage_engine(context / "engine")
    shutil.copyfile(ROOT / "01_src/docker/requirements.sandbox.txt", context / "requirements.txt")
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
    import json
    lines += ["RUN " + json.dumps(["python", "-c", check]), "COPY engine/ /opt/citrusfig/"]
    (context / "Dockerfile").write_text("\n".join(lines) + "\n", encoding="utf-8")


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
        mapped = runner._control(runner._wsl_command("/usr/bin/wslpath", "-a", "-u", str(context)))
        print(runner._control(runner._docker_command(
            "build", "--pull=false", "--network=default" if args.install_table_engines else "--network=none",
            "--tag", args.image, mapped), timeout=600))
    print(f"Previous image: {backup} ({original})", flush=True)
    runner.image = args.image
    print(f"Updated image: {args.image} ({runner.resolve_image()})", flush=True)


if __name__ == "__main__":
    main()
