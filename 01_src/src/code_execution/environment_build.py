"""Trusted offline installer, copied as a snapshot and run ONLY by the Code Worker.

No import-time work. pip owns resolution/installation; this wrapper restricts the
artifact set and emits a portable pure-Python overlay. It is not a package index
client or a general wheel installer. Native extensions/.pth/scripts need a later
filesystem overlay backend and are explicitly unsupported by this first backend.
"""
from __future__ import annotations


def main():
    import hashlib
    import importlib.metadata
    import json
    import os
    from pathlib import Path, PurePosixPath
    import platform
    import re
    import stat
    import subprocess
    import sys
    import zipfile

    lock_path = Path(sys.argv[1])
    lock_bytes = lock_path.read_bytes()
    lock = json.loads(lock_bytes)
    runtime = {"python": platform.python_version(), "platform": sys.platform,
               "machine": platform.machine(), "pip": importlib.metadata.version("pip")}
    if runtime != lock["runtime"]:
        raise ValueError("environment_runtime_mismatch")
    wheelhouse = lock_path.parent / "wheels"
    packages = lock["packages"]
    requirements = []
    expanded = 0
    for package in packages:
        wheel = wheelhouse / package["filename"]
        data = wheel.read_bytes()
        if len(data) != package["size_bytes"] or hashlib.sha256(data).hexdigest() != package["sha256"]:
            raise ValueError("environment_wheel_integrity_mismatch")
        with zipfile.ZipFile(wheel) as archive:
            entries = archive.infolist()
            if len(entries) > 4096:
                raise ValueError("environment_wheel_entry_limit")
            for item in entries:
                name = item.filename
                parts = PurePosixPath(name).parts
                if (not name or "\\" in name or "\x00" in name or name.startswith("/")
                        or any(p in ("", ".", "..") for p in name.rstrip("/").split("/"))
                        or any(":" in p for p in parts)
                        or stat.S_ISLNK(item.external_attr >> 16)
                        or item.flag_bits & 1):
                    raise ValueError("environment_wheel_entry_denied")
                if not item.is_dir():
                    expanded += item.file_size
                    if expanded > 16 * 1024**2:
                        raise ValueError("environment_expanded_byte_limit")
                if (name.lower().endswith((".pth", ".so", ".pyd", ".dll", ".dylib"))
                        or ".data/scripts/" in name):
                    raise ValueError("environment_pure_python_overlay_required")
        requirements.append(package["name"] + "==" + package["version"]
                            + " --hash=sha256:" + package["sha256"])
    requirements_path = Path("/workspace/environment-requirements.txt")
    requirements_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    target = Path("/outputs/environment-site")
    report_path = Path("/outputs/environment-pip-report.json")
    environment = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": "/workspace",
                   "LANG": "C.UTF-8", "TMPDIR": "/tmp", "PIP_CONFIG_FILE": "/dev/null"}
    # Deliberately keep dependency resolution ON. --no-deps would conceal an
    # incomplete transitive lock; --ignore-installed excludes accidental use of
    # the base image's installed distributions to satisfy a missing wheel.
    subprocess.run([sys.executable, "-I", "-m", "pip", "--isolated",
        "--disable-pip-version-check", "--no-cache-dir", "install", "--no-input",
        "--no-index", "--find-links", str(wheelhouse), "--only-binary=:all:",
        "--require-hashes", "--ignore-installed", "--no-compile", "--target", str(target),
        "--report", str(report_path), "-r", str(requirements_path)],
        check=True, env=environment, stdin=subprocess.DEVNULL)
    normalize = lambda name: re.sub(r"[-_.]+", "-", name).lower()
    expected = {p["name"]: p["version"] for p in packages}
    report = json.loads(report_path.read_bytes())
    installed = {normalize(p["metadata"]["name"]): p["metadata"]["version"] for p in report["install"]}
    if report.get("version") != "1" or installed != expected or len(report["install"]) != len(packages):
        raise ValueError("environment_install_report_mismatch")
    for item in report["install"]:
        name = normalize(item["metadata"]["name"])
        package = next(p for p in packages if p["name"] == name)
        if item["download_info"]["archive_info"]["hashes"].get("sha256") != package["sha256"]:
            raise ValueError("environment_install_report_hash_mismatch")
    actual = {normalize(p.metadata["Name"]): p.version
              for p in importlib.metadata.distributions(path=[str(target)])}
    if actual != expected:
        raise ValueError("environment_installed_set_mismatch")
    paths = []
    total = 0
    for directory, dirs, files in os.walk(target, followlinks=False):
        for name in (*dirs, *files):
            path = Path(directory) / name
            info = path.lstat()
            relative = path.relative_to(target).as_posix()
            if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)) or info.st_nlink != 1 and not path.is_dir():
                raise ValueError("environment_installed_entry_denied")
            if (relative.startswith("bin/") or relative.lower().endswith((".pth", ".so", ".pyd", ".dll", ".dylib"))):
                raise ValueError("environment_pure_python_overlay_required")
            if path.is_file():
                total += info.st_size
                paths.append((relative, path))
            if total > 16 * 1024**2 or len(paths) > 4096:
                raise ValueError("environment_installed_limit")
    marker = {"schema": "citrus.environment-overlay/1",
              "lock_sha256": hashlib.sha256(lock_bytes).hexdigest(),
              "runtime": runtime, "packages": expected}
    output = Path(sys.argv[2])
    output.parent.mkdir(parents=True, exist_ok=True)
    directories = {str(parent) + "/" for name, _ in paths for parent in PurePosixPath(name).parents if str(parent) != "."}
    if len(directories) + len(paths) > 4096:
        raise ValueError("environment_installed_entry_limit")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        # zipimport needs explicit directory entries for implicit namespaces.
        for name in sorted(directories):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = (stat.S_IFDIR | 0o555) << 16
            archive.writestr(entry, b"")
        for name, path in sorted(paths):
            entry = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            entry.external_attr = (stat.S_IFREG | 0o444) << 16
            archive.writestr(entry, path.read_bytes())
        entry = zipfile.ZipInfo("citrus-environment.json", date_time=(1980, 1, 1, 0, 0, 0))
        entry.external_attr = (stat.S_IFREG | 0o444) << 16
        archive.writestr(entry, json.dumps(marker, sort_keys=True, separators=(",", ":")))
    # Import checks execute only in a fresh sandbox child, never on the host.
    # Use the very archive that the next execution will mount, not its build dir.
    check = """import importlib, json, pathlib, sys
sys.path.insert(0, sys.argv[1])
completed = []
try:
    for name in sys.argv[3:]:
        module = importlib.import_module(name)
        locations = [module.__file__] if getattr(module, '__file__', None) else list(module.__path__)
        if not locations or any(not location.startswith(sys.argv[1] + '/') for location in locations):
            raise RuntimeError('environment_import_did_not_use_overlay')
        completed.append(name)
except BaseException as exc:
    raise RuntimeError('environment_import_check_failed') from exc
pathlib.Path(sys.argv[2]).write_text(json.dumps(completed), encoding='utf-8')
"""
    completion = Path("/tmp/environment-checks-completed.json")
    completion.unlink(missing_ok=True)
    subprocess.run([sys.executable, "-I", "-c", check, str(output), str(completion), *lock["checks"]],
                   env=environment, check=True, stdin=subprocess.DEVNULL)
    # Exit 0 alone is insufficient (e.g. a module calls os._exit(0)). This is
    # completion evidence for the checks, not an attestation of package safety.
    if not completion.is_file() or completion.stat().st_size > 4096 or json.loads(completion.read_bytes()) != lock["checks"]:
        raise ValueError("environment_import_checks_incomplete")
    print(json.dumps({"environment_build": "succeeded", "packages": expected,
                      "lock_sha256": marker["lock_sha256"], "archive_sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
