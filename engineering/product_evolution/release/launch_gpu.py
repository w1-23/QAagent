#!/usr/bin/env python3
"""Host launcher for the GPU app, using explicit Apptainer or Conda mode.

Defaults to Apptainer. No automatic fallback and no service execution without
--execute. Conda mode requires an independently provisioned genuine prefix.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

BASE_SHA = "268295ac77d715a44c6867292f7a6b07d41b27fbb9a48aaa654d8e0c63900551"
UNSAFE_ENV = {"LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
              "PYTHONUSERBASE", "PYTHONINSPECT", "VIRTUAL_ENV", "BASH_ENV", "ENV"}
OPTIONAL_ENV = ("CUDA_VISIBLE_DEVICES", "CITRUS_COOKIE_SECURE", "CITRUSQA_FIGURE_JOB_DIR")


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


def runtime_mode(profile):
    mode = profile.get("runtime_mode", "apptainer")
    if mode not in {"apptainer", "conda"}:
        raise ValueError("runtime_mode must be explicitly apptainer or conda")
    return mode


def clean_environment(inherited=None):
    inherited = os.environ if inherited is None else inherited
    return {key: value for key, value in inherited.items()
            if not key.startswith(("APPTAINER", "SINGULARITY", "CONDA")) and key not in UNSAFE_ENV}


def strict_environment(profile, release, cache, figure, inherited=None):
    """One strict app/CPU-worker contract, shared by both runtime modes."""
    inherited = os.environ if inherited is None else inherited
    env = {"CITRUS_REQUIRE_GPU": "1", "CITRUS_ONNX_DEVICE": "gpu", "CITRUS_REQUIRE_FIGURE_WORKER": "1",
           "CITRUSQA_MODEL_CACHE_ROOT": str(cache), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
           "FIGURES_SANDBOX_BACKEND": "apptainer", "FIGURES_SANDBOX_IMAGE": str(figure),
           "FIGURES_SANDBOX_IMAGE_SHA256": BASE_SHA, "FIGURES_SANDBOX_ALLOW_LOCAL": "0",
           "FIGURES_SANDBOX_RUNTIME": profile["figure_apptainer_executable"],
           "CITRUS_RELEASE_ID": release.name, "CITRUS_RUNTIME_MODE": runtime_mode(profile)}
    for key in OPTIONAL_ENV:
        if key in inherited:
            env[key] = inherited[key]
    for value in env.values():
        if not isinstance(value, str) or any(c in value for c in "\n\r\x00"):
            raise ValueError("Invalid environment value")
    return env


def conda_interpreter(profile, inherited=None):
    """Check disk metadata AND the actual interpreter identity, never activate.

    A venv with a cosmetic conda-meta marker must still fail sys.prefix/base
    checks. No Python probe runs before prefix metadata and path checks pass.
    """
    python = checked_path(profile["conda_python"])
    if python.parent.name != "bin":
        raise ValueError("conda_python must be the bin/python executable in a Conda prefix")
    prefix = python.parent.parent.resolve()
    if prefix.is_relative_to(Path("/home/WZQ/CitrusQA/.venv").resolve()):
        raise ValueError("Old 8000 Conda environment is protected; use a new independent GPU prefix")
    if not python.resolve().is_relative_to(prefix) or not os.access(python, os.X_OK):
        raise ValueError("Conda interpreter must be executable and remain inside its prefix")
    history = prefix / "conda-meta/history"
    if ((prefix / "conda-meta").is_symlink() or history.is_symlink() or not history.is_file() or not history.stat().st_size
            or (prefix / "pyvenv.cfg").exists()):
        raise ValueError("A genuine Conda prefix with conda-meta/history is required; venvs are refused")
    records = list((prefix / "conda-meta").glob("python-*.json"))
    if not any(not record.is_symlink() and json.loads(record.read_text(encoding="utf-8")).get("name") == "python"
               for record in records):
        raise ValueError("Conda prefix has no Python package metadata")
    probe_env = clean_environment(inherited)
    probe_env["PATH"] = str(prefix / "bin") + os.pathsep + "/usr/local/bin:/usr/bin:/bin"
    probe = subprocess.run([str(python), "-I", "-c",
        "import json,sys; print(json.dumps({'prefix':sys.prefix,'base_prefix':sys.base_prefix,'executable':sys.executable}))"],
        capture_output=True, text=True, check=True, timeout=30, env=probe_env)
    identity = json.loads(probe.stdout)
    if (Path(identity["prefix"]).resolve() != prefix or Path(identity["base_prefix"]).resolve() != prefix
            or Path(identity["executable"]).resolve() != python.resolve()):
        raise ValueError("Interpreter identity does not match a standalone genuine Conda prefix")
    return python, prefix


def launch_plan(profile, release, inherited=None):
    mode = runtime_mode(profile)
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
    strict = strict_environment(profile, release, cache, figure, inherited)
    environment = clean_environment(inherited)
    environment.update(strict)
    entry_args = ["-I", str(release / "engineering/product_evolution/release/gpu_entry.py"),
                  "--source", str(release / "01_src")]
    if mode == "conda":
        # The app runs on the host; untrusted CPU jobs STILL use Apptainer.
        checked_path(profile["figure_apptainer_executable"])
        python, prefix = conda_interpreter(profile, inherited)
        environment.update(CONDA_PREFIX=str(prefix), CONDA_DEFAULT_ENV=prefix.name,
                           PATH=str(prefix / "bin") + os.pathsep + "/usr/local/bin:/usr/bin:/bin")
        return [str(python), *entry_args], environment
    python = profile["cuda_python"]
    if not re.fullmatch(r"/[A-Za-z0-9_./-]+", python):
        raise ValueError("Set an inspected absolute Python executable INSIDE the CUDA SIF")
    args = [str(runtime), "exec", "--nv", "--cleanenv", "--no-home", "--pwd", str(release / "01_src")]
    binds = [(release, "ro"), (data, "rw"), (cache, "rw"), (figure, "ro")]
    for entry in profile["extra_binds"]:
        path = Path(entry["path"])
        checked_path(str(path), directory=path.is_dir())
        if entry["mode"] not in ("ro", "rw"):
            raise ValueError("Bind mode must be ro/rw")
        binds.append((path, entry["mode"]))
    for path, mode in binds:
        args.extend(["--bind", f"{path}:{path}:{mode}"])
    for key, value in strict.items():
        args.extend(["--env", f"{key}={value}"])
    return [*args, str(cuda), python, *entry_args], environment


def command(profile, release):
    """Compatibility API for configuration validation; never executes runtime."""
    return launch_plan(profile, release)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--release", required=True)
    parser.add_argument("--execute", action="store_true", help="Execute the foreground service; otherwise print argv only")
    args = parser.parse_args()
    with open(args.profile, encoding="utf-8") as stream:
        profile = json.load(stream)
    argv, environment = launch_plan(profile, Path(args.release))
    if args.execute:
        os.execve(argv[0], argv, environment)
    print(json.dumps({"dry_run": True, "runtime_mode": runtime_mode(profile), "argv": argv}, indent=2))


if __name__ == "__main__":
    main()
