#!/usr/bin/env python3
"""Host launcher for the GPU app SIF. Does not run until explicitly invoked.

The JSON profile is private host configuration. No guessed model/runtime paths.
This entrypoint replaces only the ExecStart of citrusqa-next.service (8030).
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys

BASE_SHA = "268295ac77d715a44c6867292f7a6b07d41b27fbb9a48aaa654d8e0c63900551"


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def checked_path(value, directory=False):
    path = Path(value)
    if not path.is_absolute() or any(c in str(path) for c in ":,\n\r\x00"):
        raise ValueError("Paths must be absolute and cannot contain Apptainer bind separators")
    if not (path.is_dir() if directory else path.is_file()):
        raise ValueError(f"Required host path missing: {path}")
    return path


def command(profile, release):
    if profile.get("service") != "citrusqa-next.service" or profile.get("port") != 8030:
        raise ValueError("Launcher targets only citrusqa-next.service:8030")
    release = checked_path(str(release), directory=True).resolve()
    with open(release / "MANIFEST.json", encoding="utf-8") as stream:
        manifest = json.load(stream)
    if (manifest.get("deployment_status") != "REVIEW_REQUIRED" or manifest.get("blocked_apply") is not False
            or manifest.get("rollback_compatibility_reviewed") is not True or manifest.get("release_id") != release.name):
        raise ValueError("Source-only BLOCKED/unreviewed bundle cannot launch the service")
    runtime = checked_path(profile["apptainer_executable"])
    cuda = checked_path(profile["cuda_sif"])
    figure = checked_path(profile["figure_sif"])
    data = checked_path(profile["roots"]["runtime"], directory=True).resolve()
    cache = checked_path(profile["model_cache_root"], directory=True)
    if (release / "agent").resolve() != data:
        raise ValueError("Release agent link must resolve to the existing 8030 data")
    if sha(cuda) != profile["cuda_sif_sha256"] or sha(figure) != BASE_SHA:
        raise ValueError("Pinned SIF hash mismatch")
    python = profile["cuda_python"]
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", python):
        raise ValueError("Set an inspected absolute Python executable INSIDE the CUDA SIF")
    args = [str(runtime), "exec", "--nv", "--cleanenv", "--no-home", "--pwd", str(release / "01_src")]
    binds = [(release, "ro"), (data, "rw"), (cache, "rw"), (figure, "ro")]
    # These must be inspected on the host. Nested Apptainer/systemd tooling,
    # cgroup and user-bus access cannot be inferred from the SIF's filename.
    for entry in profile["extra_binds"]:
        path = Path(entry["path"])
        checked_path(str(path), directory=path.is_dir())
        mode = entry["mode"]
        if mode not in ("ro", "rw"):
            raise ValueError("Bind mode must be ro/rw")
        binds.append((path, mode))
    for path, mode in binds:
        args.extend(["--bind", f"{path}:{path}:{mode}"])
    env = {"CITRUS_REQUIRE_GPU": "1", "CITRUS_ONNX_DEVICE": "gpu", "CITRUS_REQUIRE_FIGURE_WORKER": "1",
           "CITRUSQA_MODEL_CACHE_ROOT": str(cache), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "FIGURES_SANDBOX_BACKEND": "apptainer", "FIGURES_SANDBOX_IMAGE": str(figure),
           "FIGURES_SANDBOX_IMAGE_SHA256": BASE_SHA,
           "FIGURES_SANDBOX_RUNTIME": profile["figure_apptainer_executable"],
           "CITRUS_RELEASE_ID": release.name}
    # Explicit allowlist only. Service environment, credentials and loader state
    # never propagate wholesale through the Apptainer environment namespace.
    for key in ("CUDA_VISIBLE_DEVICES", "CITRUS_COOKIE_SECURE", "CITRUSQA_FIGURE_JOB_DIR"):
        if key in os.environ:
            env[key] = os.environ[key]
    for key, value in env.items():
        if any(c in value for c in "\n\r\x00"):
            raise ValueError("Invalid environment value")
        args.extend(["--env", f"{key}={value}"])
    args.extend([str(cuda), python, "-I", str(release / "engineering/product_evolution/release/gpu_entry.py"),
                 "--source", str(release / "01_src")])
    return args


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--execute", action="store_true", help="Execute the foreground service; otherwise print argv only")
    args = parser.parse_args()
    with open(args.profile, encoding="utf-8") as stream:
        profile = json.load(stream)
    argv = command(profile, Path(args.release))
    if args.execute:
        # ApptainerENV/BIND/etc from the shell must not weaken the fixed command.
        clean = {key: value for key, value in os.environ.items()
                 if not key.startswith(("APPTAINER", "SINGULARITY")) and key not in ("LD_PRELOAD", "PYTHONPATH", "PYTHONHOME")}
        os.execve(argv[0], argv, clean)
    print(json.dumps({"dry_run": True, "argv": argv}, indent=2))


if __name__ == "__main__":
    main()
