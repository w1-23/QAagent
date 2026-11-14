#!/usr/bin/env python3
"""Prepare/apply a local 8030 systemd drop-in transaction. Never restart.

Apply and rollback require a current stopped-service backup and exact live file
agreement with that backup. Rollback changes service configuration/code only;
it NEVER restores historical databases over newer user data.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent))
import release as r
import launch_gpu


def safe_service_path(path):
    path = Path(path).expanduser().absolute()
    r.require(path.name == "s6-release.conf" and path.parent.name == "citrusqa-next.service.d",
              "Only citrusqa-next.service.d/s6-release.conf can be changed")
    r.require(not path.is_symlink() and not any(p.is_symlink() for p in path.parents), "Symlink in service config path")
    return path


def stopped_backup(profile, backup, boundary):
    roots = r.load_roots(profile)
    before = boundary()
    manifest = r.verify_backup(backup)
    r.require(manifest["boundary"] == before, "Backup belongs to a different stopped-service boundary")
    r.require(manifest["roots"] == {name: str(root) for name, root in roots.items()}, "Backup roots differ from current 8030 profile")
    r.require(r.root_inventory(roots) == manifest["source_files"], "Live state changed since backup; take a NEW stopped backup")
    return roots, before


def release_files(path):
    manifest = r.read_json(path / "MANIFEST.json")
    r.require(manifest["format"] == r.FORMAT and manifest["kind"] == "source", "Unknown staged release")
    for name, row in manifest["files"].items():
        r.safe_name(name)
        target = path / name
        r.require(target.is_file() and not target.is_symlink() and r.digest(target) == row["sha256"],
                  f"Staged release hash mismatch: {name}")
    r.require(path.name == manifest["release_id"], "Staged directory/release ID mismatch")
    return manifest


def code_records(source):
    return {p.relative_to(source).as_posix(): r.digest(p) for p in r.source_files(source, scan_secrets=False)}


def systemd_quote(value):
    r.require(re.fullmatch(r"/[A-Za-z0-9_./-]+", str(value)), "Service paths must be absolute without whitespace or special characters")
    return str(value)


def unit_text(profile, target):
    python = systemd_quote(profile["host_python"])
    private_profile = systemd_quote(profile["private_profile_path"])
    target = Path(systemd_quote(target))
    launcher = target / "engineering/product_evolution/release/launch_gpu.py"
    return ("# S6 release transaction; affects 8030 only. No automatic restart.\n[Service]\n"
            f"WorkingDirectory={target}/01_src\nExecStart=\n"
            f"ExecStart={python} -I {launcher} --profile {private_profile} --release {target} --execute\n"
            "Environment=CITRUS_REQUIRE_GPU=1 CITRUS_ONNX_DEVICE=gpu CITRUS_REQUIRE_FIGURE_WORKER=1\n"
            "Restart=no\nTimeoutStartSec=600\n").encode()


def atomic_replace(path, data):
    fd, temp = tempfile.mkstemp(prefix=".s6-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def configure(profile, target, backup, transaction, previous_compatibility, dropin, apply=False,
              boundary=r.service_boundary, capture_unit=None):
    target, transaction = Path(target).resolve(), Path(transaction).resolve()
    dropin = safe_service_path(dropin)
    r.require(not transaction.exists(), "Transaction already exists; never overwrite")
    r.require(all(not transaction.is_relative_to(root) for root in r.load_roots(profile).values()), "Transaction must be outside data")
    roots, before = stopped_backup(profile, backup, boundary)
    manifest = release_files(target)
    r.require(manifest.get("rollback_compatibility_reviewed") is True and manifest.get("deployment_status") == "REVIEW_REQUIRED",
              "Source-only BLOCKED bundle cannot be configured for deployment; rebuild with reviewed compatibility")
    r.preflight(profile)
    r.check_compatibility(roots, r.read_json(target / "rollback-compatibility.json"))
    compatibility = r.read_json(previous_compatibility)
    r.check_compatibility(roots, compatibility)
    old_source = Path(profile["source_path"]).resolve()
    if "WorkingDirectory" in before:
        r.require(Path(before["WorkingDirectory"]).resolve() == old_source / "01_src",
                  "Profile previous source differs from effective 8030 WorkingDirectory")
    old_code = code_records(old_source)
    data = roots["runtime"]
    agent = target / "agent"
    r.require(not agent.exists() and not agent.is_symlink(), "New release already has an agent entry")
    # launch_gpu.command requires the runtime link. Validate all other launch
    # inputs now; no CUDA/SIF or server execution happens during configuration.
    launch_gpu.checked_path(profile["host_python"])
    launch_gpu.checked_path(profile["apptainer_executable"])
    launch_gpu.checked_path(profile["model_cache_root"], directory=True)
    r.require(r.digest(profile["figure_sif"]) == r.BASE_SIF_SHA256, "Figure image hash mismatch")
    r.require(r.digest(profile["cuda_sif"]) == profile["cuda_sif_sha256"], "CUDA image hash mismatch")
    r.require(Path(profile["private_profile_path"]).resolve().is_file(), "Private launch profile missing")
    r.require(r.read_json(profile["private_profile_path"]) == profile, "Private launch profile differs from reviewed input")
    desired = unit_text(profile, target)
    original = dropin.read_bytes() if dropin.exists() else None
    plan = {"release": str(target), "dropin": str(dropin), "previous_source": str(old_source),
            "transaction": str(transaction), "restart": False, "dry_run": not apply}
    if not apply:
        return plan | {"proposed_dropin": desired.decode()}
    transaction.parent.mkdir(parents=True, exist_ok=True)
    transaction.mkdir(mode=0o700)
    linked = False
    replaced = False
    try:
        if capture_unit is None:
            capture_unit = lambda: subprocess.run(["systemctl", "--user", "cat", r.SERVICE],
                                                  capture_output=True, check=True).stdout
        r.write_json(transaction / "rollback-compatibility.json", compatibility)
        (transaction / "previous-effective-unit.txt").write_bytes(capture_unit())
        os.chmod(transaction / "previous-effective-unit.txt", 0o600)
        if original is not None:
            (transaction / "previous-dropin.conf").write_bytes(original)
            os.chmod(transaction / "previous-dropin.conf", 0o600)
        unchanged_unit_files = {}
        unit_paths = [before.get("FragmentPath", ""), *before.get("DropInPaths", "").split()]
        for index, value in enumerate(filter(None, unit_paths)):
            path = Path(value).absolute()
            r.require(path.is_absolute() and path.is_file(), "Effective service config file missing")
            snapshot = transaction / f"unit-file-{index}.snapshot"
            shutil.copyfile(path, snapshot)
            os.chmod(snapshot, 0o600)
            if path != dropin:
                unchanged_unit_files[str(path)] = r.digest(path)
        record = {"format": r.FORMAT, "kind": "switch", "created_utc": r.timestamp(), "plan": plan,
                  "unchanged_unit_files": unchanged_unit_files,
                  "boundary": before, "backup": str(Path(backup).resolve()),
                  "backup_manifest_sha256": r.digest(Path(backup) / "BACKUP.json"),
                  "previous_code": old_code, "had_dropin": original is not None,
                  "previous_dropin_sha256": None if original is None else __import__("hashlib").sha256(original).hexdigest(),
                  "applied_dropin_sha256": __import__("hashlib").sha256(desired).hexdigest(),
                  "previous_compatibility_sha256": r.digest(transaction / "rollback-compatibility.json")}
        r.write_json(transaction / "TRANSACTION.json", record)
        r.require(boundary() == before, "Service boundary changed")
        r.require(r.root_inventory(roots) == r.verify_backup(backup)["source_files"], "Live state changed before switch")
        agent.symlink_to(data, target_is_directory=True)
        linked = True
        launch_gpu.command(profile, target)  # Full argv validation, still no execution.
        dropin.parent.mkdir(parents=True, exist_ok=True)
        r.require((dropin.read_bytes() if dropin.exists() else None) == original, "Service configuration changed concurrently")
        atomic_replace(dropin, desired)
        replaced = True
        r.require(boundary() == before, "Service boundary changed during switch")
    except Exception:
        if replaced:
            if original is None:
                dropin.unlink(missing_ok=True)
            else:
                atomic_replace(dropin, original)
        if linked:
            agent.unlink(missing_ok=True)
        # Keep the private transaction as evidence; it is never reusable.
        raise
    return plan | {"next": "Review transaction, then explicitly daemon-reload/start and verify /ready"}


def rollback(profile, transaction, fresh_backup, apply=False, boundary=r.service_boundary):
    transaction = Path(transaction).resolve()
    record = r.read_json(transaction / "TRANSACTION.json")
    r.require(record["format"] == r.FORMAT and record["kind"] == "switch", "Invalid transaction")
    roots, before = stopped_backup(profile, fresh_backup, boundary)
    for name, expected in record.get("unchanged_unit_files", {}).items():
        path = Path(name)
        r.require(path.is_file() and r.digest(path) == expected, "Other effective service configuration changed; manual rollback review required")
    if before.get("FragmentPath"):
        current_files = {str(Path(p).absolute()) for p in [before["FragmentPath"], *before.get("DropInPaths", "").split()]
                         if str(Path(p).absolute()) != record["plan"]["dropin"]}
        r.require(current_files == set(record["unchanged_unit_files"]), "Effective service config inventory changed")
    compatibility = transaction / "rollback-compatibility.json"
    r.require(r.digest(compatibility) == record["previous_compatibility_sha256"], "Rollback compatibility was modified")
    r.check_compatibility(roots, r.read_json(compatibility))
    old_source = Path(record["plan"]["previous_source"])
    r.require(code_records(old_source) == record["previous_code"], "Previous source changed; cannot silently roll back to different code")
    dropin = safe_service_path(record["plan"]["dropin"])
    r.require(dropin.exists() and r.digest(dropin) == record["applied_dropin_sha256"], "Active release config changed; manual review required")
    original = None
    if record["had_dropin"]:
        path = transaction / "previous-dropin.conf"
        r.require(r.digest(path) == record["previous_dropin_sha256"], "Rollback config hash mismatch")
        original = path.read_bytes()
    if apply:
        r.require(boundary() == before, "Service boundary changed")
        if original is None:
            dropin.unlink()
        else:
            atomic_replace(dropin, original)
    return {"dry_run": not apply, "restart": False, "code": str(old_source),
            "data": "current 8030 data preserved; no database/file snapshot was restored",
            "next": "Explicit daemon-reload/start only after operator review; verify the previous release readiness contract"}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("configure", "rollback"):
        p = sub.add_parser(name)
        p.add_argument("--profile", required=True); p.add_argument("--transaction", required=True)
        p.add_argument("--backup", required=True); p.add_argument("--apply", action="store_true")
        if name == "configure":
            p.add_argument("--release", required=True); p.add_argument("--previous-compatibility", required=True)
            p.add_argument("--dropin", required=True)
    args = parser.parse_args()
    try:
        profile = r.read_json(args.profile)
        if args.command == "configure":
            result = configure(profile, args.release, args.backup, args.transaction, args.previous_compatibility,
                               args.dropin, args.apply)
        else:
            result = rollback(profile, args.transaction, args.backup, args.apply)
        print(json.dumps(result, indent=2))
    except (r.ReleaseError, OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
