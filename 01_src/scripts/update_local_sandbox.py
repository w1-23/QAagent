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

ENGINE_FILES = ("__init__.py", "spec.py", "styling.py", "core.py", "runtime.py",
                "manifest.py", "overrides.py", "exporter.py", "worker.py",
                "script_outputs.py", "dataset_codec.py")


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
        for filename in ENGINE_FILES:
            shutil.copyfile(ROOT / "01_src/src/figures" / filename, context / filename)
        # BuildKit treats an image ID after FROM as a registry name. Use the
        # just-created local preservation tag, whose identity we checked.
        lines = [f"FROM {backup}"]
        if args.install_table_engines:
            lines += ["USER root", 'RUN pip install --no-cache-dir "seaborn>=0.13" "openpyxl>=3.1" "xlrd>=2.0" "pyxlsb>=1.0"',
                      "USER 65534:65534"]
        lines += [f"COPY {name} /opt/citrusfig/src/figures/{name}"
                  for name in ENGINE_FILES if name != "worker.py"]
        lines += ["COPY worker.py /opt/citrusfig/worker.py"]
        (context / "Dockerfile").write_text("\n".join(lines) + "\n", encoding="utf-8")
        mapped = runner._control(runner._wsl_command("/usr/bin/wslpath", "-a", "-u", str(context)))
        print(runner._control(runner._docker_command(
            "build", "--pull=false", "--network=default" if args.install_table_engines else "--network=none",
            "--tag", args.image, mapped), timeout=600))
    print(f"Previous image: {backup} ({original})", flush=True)
    runner.image = args.image
    print(f"Updated image: {args.image} ({runner.resolve_image()})", flush=True)


if __name__ == "__main__":
    main()
