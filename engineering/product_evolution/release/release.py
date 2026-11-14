#!/usr/bin/env python3
"""Offline S6 release/recovery tooling. Stdlib only; never contacts a remote host.

All CLI writes require --apply. No command stops, starts or restarts a service.
Run this trusted script with python -I, outside any untrusted extraction directory.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import tarfile
import tempfile
import uuid

FORMAT = "citrusschalor-s6-v1"
SERVICE = "citrusqa-next.service"
BASE_SIF_SHA256 = "268295ac77d715a44c6867292f7a6b07d41b27fbb9a48aaa654d8e0c63900551"
EXCLUDED = {".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache",
            ".mypy_cache", ".ruff_cache", ".hf_cache", ".cache", "cache", "caches",
            "agent", "state", "workspace", "workspaces", "embedding", "embeddings", "models",
            "data", "results", "runtime", "logs", "tmp", "temp", "backups", "releases",
            "secrets", ".ssh", "dist-info"}
SOURCE_ROOTS = ("01_src", "02_config", "engineering/product_evolution")
ROOT_FILES = ("README.md", ".gitignore")
REFERENCE_COLUMN = re.compile(r"(^path$|_path$|^path_|^filepath$|^file$|_dir$|^directory$)", re.I)


class ReleaseError(RuntimeError):
    pass


def require(condition, message):
    if not condition:
        raise ReleaseError(message)


def timestamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value):
    with open(path, "xb") as stream:
        stream.write(json_bytes(value))
    os.chmod(path, 0o600)


def safe_name(name):
    require(isinstance(name, str) and name and "\\" not in name and ":" not in name,
            "Unsafe archive/manifest path")
    parts = name.split("/")
    require(not name.startswith("/") and all(p not in ("", ".", "..") for p in parts),
            "Unsafe archive/manifest path")
    require(all(not p.endswith((" ", ".")) and not re.match(
        r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.|$)", p, re.I) for p in parts),
        "Nonportable archive path")
    require(not any(ord(c) < 32 for c in name), "Control character in archive path")
    return PurePosixPath(name)


def regular_files(root):
    root = Path(root)
    require(root.is_dir() and not root.is_symlink(), f"Root must be a real directory: {root}")
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            require(not path.is_symlink(), f"Symlink refused: {path}")
        for name in sorted(files):
            path = Path(directory) / name
            require(stat.S_ISREG(path.stat().st_mode), f"Nonregular file refused: {path}")
            yield path


def inventory(root):
    return {p.relative_to(root).as_posix(): {"sha256": digest(p), "size": p.stat().st_size,
            "mode": 0o755 if p.stat().st_mode & 0o111 else 0o600}
            for p in sorted(regular_files(root))}


def source_excluded(relative):
    parts = relative.parts
    return any(p.lower() in EXCLUDED or p.lower().startswith(".env") for p in parts) or (
        relative.suffix.lower() in {".pyc", ".pyo", ".db", ".sqlite", ".sqlite3", ".sif",
                                   ".pem", ".key", ".p12", ".pfx", ".log"}) or bool(
        re.search(r"(^|[._-])(secret|credentials|token)([._-]|$)", relative.name, re.I))


def inspect_source_secret(path):
    # This is a fail-closed guard for common literal credentials, not a claim of
    # exhaustive secret detection. An operator still reviews the exact manifest.
    if path.suffix.lower() not in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg", ".py", ".js", ".sh"}:
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    require(not re.search(r"(?m)(?<![\w])(?:sk-[A-Za-z0-9_-]{24,}|AKIA[A-Z0-9]{16})|^-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----$", text),
            f"Possible literal secret; review/remove before bundling: {path}")
    if path.suffix.lower() in {".yaml", ".yml", ".json", ".toml", ".ini", ".cfg"}:
        pattern = r'''(?im)^\s*["']?(?:[\w-]*api[_-]?key|password|secret|access_token)["']?\s*[:=]\s*["']?([^\n"',#}]+)'''
        for match in re.finditer(pattern, text):
            value = match.group(1).strip()
            require(not value or value.lower() in {"null", "none", "false", "changeme"}
                    or value.startswith(("${", "<", "REPLACE_", "YOUR_")),
                    f"Nonempty credential field; source config must be sanitized: {path}")


def source_files(root, secret_review=None, scan_secrets=True):
    root = Path(root).resolve()
    paths = []
    for prefix in SOURCE_ROOTS:
        tree = root / prefix
        if not tree.exists():
            continue
        for directory, dirs, files in os.walk(tree, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not source_excluded((Path(directory) / d).relative_to(root)))
            for name in dirs + files:
                path = Path(directory) / name
                rel = path.relative_to(root)
                if source_excluded(rel):
                    continue
                require(not path.is_symlink(), f"Source symlink refused: {rel}")
                if path.is_file():
                    if prefix == "engineering/product_evolution" and path.suffix.lower() not in {
                            ".py", ".md", ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".sh",
                            ".ps1", ".service", ".conf", ".txt", ".js", ".html", ".css"}:
                        continue
                    require(stat.S_ISREG(path.stat().st_mode), f"Nonregular source: {rel}")
                    if scan_secrets:
                        review = (secret_review or {}).get(rel.as_posix())
                        if review:
                            require(review.get("sha256") == digest(path) and review.get("reason", "").strip(),
                                    f"Secret-review waiver changed or has no reason: {rel}")
                        else:
                            inspect_source_secret(path)
                    safe_name(rel.as_posix())
                    paths.append(path)
    for name in ROOT_FILES:
        path = root / name
        if path.is_file():
            require(not path.is_symlink(), f"Source symlink refused: {name}")
            paths.append(path)
    require(any(p.relative_to(root).as_posix() == "01_src/src/api/main.py" for p in paths),
            "Not a source worktree: missing 01_src/src/api/main.py")
    return sorted(paths)


def build_bundle(root, output, release_id, compatibility=None, apply=False, secret_review=None):
    root, output = Path(root).resolve(), Path(output).resolve()
    safe_name(release_id)
    require("/" not in release_id, "Release ID must be a single path component")
    require(not any(p.exists() for p in (output, Path(str(output) + ".sha256.json"), Path(str(output) + ".manifest.json"))),
            "Bundle destination/manifest/receipt already exists")
    allowed_outputs = root / "engineering/product_evolution/results/releases"
    require(not output.is_relative_to(root) or output.is_relative_to(allowed_outputs),
            "In-worktree bundles must be under excluded engineering/product_evolution/results/releases/")
    compat = read_json(compatibility) if compatibility else {"format": FORMAT, "databases": {},
        "status": "BLOCKED", "reason": "Current stopped-8030 per-DB schemas and code compatibility have not been reviewed"}
    require(compat.get("format") == FORMAT and isinstance(compat.get("databases"), dict), "Invalid compatibility manifest")
    if compatibility:
        require(compat["databases"], "Supplied compatibility manifest must be reviewed and nonempty")
    for name, hashes in compat["databases"].items():
        safe_name(name)
        require(isinstance(hashes, list) and hashes and all(re.fullmatch(r"[0-9a-f]{64}", x) for x in hashes),
                "Compatibility entries must list reviewed schema SHA256 values")
    reviews = read_json(secret_review) if secret_review else {}
    files = source_files(root, secret_review=reviews)
    records = {p.relative_to(root).as_posix(): {"sha256": digest(p), "size": p.stat().st_size,
                "mode": 0o755 if p.stat().st_mode & 0o111 else 0o644} for p in files}
    require("rollback-compatibility.json" not in records, "Reserved source path")
    payload = json_bytes(compat)
    records["rollback-compatibility.json"] = {"sha256": hashlib.sha256(payload).hexdigest(),
                                              "size": len(payload), "mode": 0o600}
    git = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    manifest = {"format": FORMAT, "kind": "source", "release_id": release_id,
                "created_utc": timestamp(), "git_head": git.stdout.strip() if git.returncode == 0 else None,
                "source": "current filesystem bytes, including uncommitted edits", "files": records,
                "included_roots": SOURCE_ROOTS, "excluded_components": sorted(EXCLUDED),
                "deployment_status": "REVIEW_REQUIRED" if compatibility else "BLOCKED",
                "blocked_apply": not bool(compatibility),
                "rollback_compatibility_reviewed": bool(compatibility), "secret_review": reviews,
                "unverified_host_inputs": ["stopped-current-8030 schema compatibility", "existing model cache files",
                    "CUDA SIF hash/Python path", "nested CPU Apptainer binds and isolation", "fresh backup and restore drill"],
                "operator_review_required": "Review all paths and source configuration for secrets before transfer"}
    if not apply:
        return {"dry_run": True, "release_id": release_id, "file_count": len(records),
                "bytes": sum(r["size"] for r in records.values()), "output": str(output)}
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = output.parent / (".bundle-" + uuid.uuid4().hex)
    manifest_temp = Path(str(temp) + ".manifest.json")
    receipt_temp = Path(str(temp) + ".sha256.json")
    published = []
    try:
        with tarfile.open(temp, "w:gz", format=tarfile.PAX_FORMAT) as archive:
            for path in files:
                name = path.relative_to(root).as_posix()
                info = tarfile.TarInfo(name)
                info.size = records[name]["size"]
                info.mode = records[name]["mode"]
                with open(path, "rb") as stream:
                    archive.addfile(info, stream)
            for name, data in (("rollback-compatibility.json", payload), ("MANIFEST.json", json_bytes(manifest))):
                info = tarfile.TarInfo(name)
                info.size, info.mode = len(data), 0o600
                archive.addfile(info, io.BytesIO(data))
        verify_bundle(temp)
        os.chmod(temp, 0o600)
        write_json(manifest_temp, manifest)
        write_json(receipt_temp, {"archive": output.name, "sha256": digest(temp),
                   "manifest_sha256": digest(manifest_temp), "release_id": release_id,
                   "manifest_file": output.name + ".manifest.json"})
        # Publish the receipt last. Exclusive hard links never overwrite an
        # existing artifact, including a race after the initial exists check.
        for source, target in ((temp, output), (manifest_temp, Path(str(output) + ".manifest.json")),
                               (receipt_temp, Path(str(output) + ".sha256.json"))):
            os.link(source, target)
            published.append((source, target))
    except Exception:
        for source, target in reversed(published):
            if target.exists() and os.path.samefile(source, target):
                target.unlink()
        raise
    finally:
        for item in (temp, manifest_temp, receipt_temp):
            item.unlink(missing_ok=True)
    return {"archive": str(output), "manifest": str(output) + ".manifest.json", "sha256": digest(output),
            "release_id": release_id, "file_count": len(records), "blocked_apply": not bool(compatibility)}


def verify_bundle(archive_path, expected_sha=None, max_bytes=8 * 1024**3):
    if expected_sha:
        require(digest(archive_path) == expected_sha, "Archive SHA256 mismatch")
    with tarfile.open(archive_path, "r:gz") as archive:
        members, seen, total = [], set(), 0
        for member in archive:
            total += member.size
            require(member.size >= 0 and len(members) < 100000 and total <= max_bytes, "Archive quota exceeded")
            safe_name(member.name)
            require(member.isfile() and not member.issym() and not member.islnk(), "Only regular archive files allowed")
            require(member.name.casefold() not in seen, "Duplicate/case-colliding archive member")
            seen.add(member.name.casefold())
            members.append(member)
        meta = next((m for m in members if m.name == "MANIFEST.json"), None)
        require(meta is not None and meta.size < 32 * 1024**2, "Missing/oversized manifest")
        manifest = json.load(archive.extractfile(meta))
        require(manifest.get("format") == FORMAT and manifest.get("kind") == "source", "Unknown bundle format")
        require(set(m.name for m in members) == set(manifest["files"]) | {"MANIFEST.json"}, "Archive manifest mismatch")
        for member in members:
            if member.name == "MANIFEST.json":
                continue
            row = manifest["files"][member.name]
            require(row["size"] == member.size and row["mode"] in (0o600, 0o644, 0o755), "Invalid file metadata")
            stream = archive.extractfile(member)
            h = hashlib.sha256()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                h.update(block)
            require(h.hexdigest() == row["sha256"], f"Bundle hash mismatch: {member.name}")
        return manifest


def publish_directory(source, target):
    """Atomic directory publication which cannot replace even an empty target."""
    if sys.platform.startswith("linux"):
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, "renameat2", None)
        require(rename is not None, "Linux libc renameat2 is required for no-overwrite publication")
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(source), -100, os.fsencode(target), 1) != 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error), str(target))
    elif os.name == "nt":
        os.rename(source, target)  # Windows rename never replaces a destination.
    else:
        raise ReleaseError("No safe no-overwrite directory publisher on this platform")


def stage_bundle(archive_path, releases, expected_sha, apply=False):
    manifest = verify_bundle(archive_path, expected_sha)
    safe_name(manifest["release_id"])
    require("/" not in manifest["release_id"], "Invalid release ID")
    releases = Path(releases).resolve()
    target = releases / manifest["release_id"]
    require(not target.exists(), "Release already exists; never overwrite it")
    if not apply:
        return {"dry_run": True, "target": str(target), "files": len(manifest["files"])}
    releases.mkdir(parents=True, exist_ok=True)
    lock = releases / ("." + manifest["release_id"] + ".lock")
    with open(lock, "xb"):
        pass
    temp = Path(tempfile.mkdtemp(prefix=".stage-", dir=releases))
    os.chmod(temp, 0o700)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            seen = set()
            for member in archive:
                safe_name(member.name)
                require(member.isfile() and member.name not in seen, "Archive changed during extraction")
                seen.add(member.name)
                require(member.name in manifest["files"] or member.name == "MANIFEST.json", "Unexpected extraction member")
                expected_size = manifest["files"].get(member.name, {}).get("size")
                require(0 <= member.size < 32 * 1024**2 if expected_size is None else member.size == expected_size,
                        "Extraction size differs from verified manifest")
                dest = temp.joinpath(*safe_name(member.name).parts)
                dest.parent.mkdir(parents=True, exist_ok=True)
                with archive.extractfile(member) as source, open(dest, "xb") as out:
                    shutil.copyfileobj(source, out)
                os.chmod(dest, manifest["files"].get(member.name, {}).get("mode", 0o600))
        staged = inventory(temp)
        staged.pop("MANIFEST.json", None)
        require(set(staged) == set(manifest["files"]), "Staged file inventory mismatch")
        require(read_json(temp / "MANIFEST.json") == manifest, "Archive manifest changed during staging")
        for name, row in manifest["files"].items():
            require(staged[name]["sha256"] == row["sha256"] and staged[name]["size"] == row["size"],
                    f"Staged hash mismatch: {name}")
        require(not target.exists(), "Release already exists")
        publish_directory(temp, target)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
        lock.unlink(missing_ok=True)
    return {"target": str(target), "release_id": manifest["release_id"]}


def db_open(path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def sqlite_file(path):
    with open(path, "rb") as stream:
        valid = stream.read(16) == b"SQLite format 3\x00"
    require(valid or Path(path).suffix.lower() not in {".db", ".sqlite", ".sqlite3"},
            f"Expected SQLite file has an invalid header: {path}")
    return valid


def db_evidence(path):
    with closing(db_open(path)) as conn:
        require(conn.execute("PRAGMA integrity_check").fetchall() == [("ok",)], f"SQLite integrity failed: {path}")
        require(not conn.execute("PRAGMA foreign_key_check").fetchall(), f"SQLite foreign keys failed: {path}")
        schema = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        schema_hash = hashlib.sha256(json_bytes({"schema": schema, "user_version": version})).hexdigest()
        return {"schema_sha256": schema_hash, "user_version": version, "integrity": "ok", "foreign_keys": "ok"}


def service_boundary():
    require(sys.platform.startswith("linux"), "Stopped-service boundary must be checked on the Linux host")
    result = subprocess.run(["systemctl", "--user", "show", SERVICE, "--property=ActiveState,SubState,MainPID,InvocationID,ExecMainExitTimestampMonotonic,FragmentPath,DropInPaths,WorkingDirectory,ExecStart,ControlGroup"],
                            capture_output=True, text=True, check=True)
    props = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    require(props.get("ActiveState") == "inactive" and props.get("MainPID") == "0",
            "Stop citrusqa-next.service explicitly before snapshot/config apply; it must be inactive with MainPID=0")
    require(props.get("FragmentPath"), "Expected service unit was not found")
    return props


def load_roots(profile):
    require(profile.get("service") == SERVICE and profile.get("port") == 8030, "Profile must target only citrusqa-next.service:8030")
    roots = {}
    for name, value in profile["roots"].items():
        safe_name(name)
        require("/" not in name and Path(value).is_absolute(), "Root must have a simple name and absolute path")
        path = Path(value).resolve()
        require(path.is_dir(), f"Missing data root: {path}")
        require(not path.is_relative_to(Path("/home/WZQ/CitrusQA").resolve()), "Old 8000 deployment data is forbidden")
        roots[name] = path
    require(roots, "No backup roots")
    for name, root in roots.items():
        for other, other_root in roots.items():
            require(name == other or not root.is_relative_to(other_root), "Backup roots overlap")
    return roots


def root_inventory(roots):
    rows = {}
    for name, root in roots.items():
        for rel, row in inventory(root).items():
            path = root / rel
            # SQLite read-only WAL connections can update SHM read marks.
            # SHM has no durable user bytes; DB and WAL remain hash-covered.
            if rel.endswith("-shm") and Path(str(path)[:-4]).is_file() and sqlite_file(Path(str(path)[:-4])):
                continue
            rows[f"{name}/{rel}"] = row
    return rows


def reference_paths(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if REFERENCE_COLUMN.search(key) and isinstance(child, str) and child.strip():
                yield child
            if isinstance(child, (dict, list)):
                yield from reference_paths(child)
    elif isinstance(value, list):
        for child in value:
            yield from reference_paths(child)


def referenced_files(db_path, roots, profile):
    """Inventory path-bearing DB fields and JSON paths. Missing paths fail closed.

    Historical deleted-file rows can be waived only with an exact per-value
    reason in the private profile. Every waiver is copied into the manifest.
    """
    refs = set()
    with closing(db_open(db_path)) as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for (table,) in tables:
            quoted = '"' + table.replace('"', '""') + '"'
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({quoted})")]
            for row in conn.execute(f"SELECT * FROM {quoted}"):
                for column, value in zip(columns, row):
                    if not isinstance(value, str) or not value.strip():
                        continue
                    if REFERENCE_COLUMN.search(column):
                        refs.add(value)
                    if value.lstrip().startswith(("{", "[")):
                        try:
                            refs.update(reference_paths(json.loads(value)))
                        except (ValueError, TypeError):
                            pass
    mapped = []
    waived = profile.get("missing_reference_waivers", {})
    for value in sorted(refs):
        require(not value.startswith(("http://", "https://", "file://")), "Path reference needs explicit local interpretation")
        candidate = Path(value)
        if not candidate.is_absolute():
            base = profile.get("relative_reference_base")
            require(base and Path(base).is_absolute(), "Relative DB path requires inspected relative_reference_base")
            candidate = Path(base) / candidate
        candidate = candidate.resolve()
        if not candidate.exists() and value in waived and waived[value].strip():
            mapped.append({"original": value, "waived_missing": waived[value]})
            continue
        require(candidate.exists(), f"Referenced bytes missing (review exact waiver if deliberately deleted): {candidate}")
        locations = [(name, root) for name, root in roots.items() if candidate.is_relative_to(root)]
        require(len(locations) == 1, f"Referenced path outside backup roots: {candidate}")
        name, root = locations[0]
        rel = candidate.relative_to(root).as_posix()
        mapped.append({"original": value, "backup_path": name + ("/" + rel if rel != "." else ""),
                       "directory": candidate.is_dir()})
    return mapped


def schema_inventory(roots):
    return {f"{name}/{p.relative_to(root).as_posix()}": db_evidence(p)
            for name, root in roots.items() for p in regular_files(root) if sqlite_file(p)}


def check_compatibility(roots, compatibility):
    actual = schema_inventory(roots)
    allowed = compatibility["databases"]
    require(actual and set(actual) == set(allowed), "Per-DB inventory differs; code rollback requires reviewed compatibility for EVERY current database")
    for name, info in actual.items():
        require(info["schema_sha256"] in allowed[name], f"Code rollback blocked by schema: {name}")
    return actual


def check_quota(parent, incoming, max_total, min_free):
    require(max_total > 0 and min_free >= 0, "Invalid quota")
    current = sum(p.stat().st_size for p in regular_files(parent))
    require(current + incoming <= max_total, "Backup retention quota exceeded; review prune plan first")
    require(shutil.disk_usage(parent).free >= incoming + min_free, "Insufficient free space/reserve")


def backup(profile, destination, boundary=service_boundary, apply=False):
    roots = load_roots(profile)
    destination = Path(destination).resolve()
    require(not destination.exists(), "Backup destination already exists")
    require(all(not destination.is_relative_to(r) for r in roots.values()), "Backup must be outside data roots")
    before_boundary = boundary()
    before = root_inventory(roots)
    databases = schema_inventory(roots)
    require(databases, "No SQLite database found; refuse suspicious empty state")
    if "expected_databases" in profile:
        require(profile["expected_databases"] and set(profile["expected_databases"]) == set(databases),
                "Inspected expected DB inventory differs; review every missing/new database")
    refs = {name: referenced_files(roots[name.split('/')[0]].joinpath(*name.split('/')[1:]), roots, profile)
            for name in databases}
    estimate = sum(r["size"] for r in before.values())
    require(destination.parent.exists(), "Create/review private backup parent first")
    # Reserve 2x input for SQLite expansion and temporary publication, plus the
    # caller's free-space reserve; quota never prunes automatically.
    check_quota(destination.parent, estimate * 2 + 1024**2,
                profile["max_total_backup_bytes"], profile["min_free_bytes"])
    if not apply:
        return {"dry_run": True, "databases": databases, "bytes": estimate, "destination": str(destination),
                "referenced_paths": sum(map(len, refs.values())), "boundary": before_boundary}
    temp = Path(tempfile.mkdtemp(prefix=".backup-", dir=destination.parent))
    os.chmod(temp, 0o700)
    lock = destination.parent / ("." + destination.name + ".lock")
    locked = False
    try:
        with open(lock, "xb"):
            pass
        locked = True
        for name, root in roots.items():
            for path in regular_files(root):
                rel = name + "/" + path.relative_to(root).as_posix()
                target = temp / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                if rel in databases:
                    with closing(db_open(path)) as source, closing(sqlite3.connect(target)) as out:
                        source.backup(out)
                        out.execute("PRAGMA journal_mode=DELETE")
                    require(db_evidence(target) == databases[rel], "SQLite snapshot schema mismatch")
                elif any(rel == db + suffix for db in databases for suffix in ("-wal", "-shm", "-journal")):
                    # Backed-up DB is self-contained, but retain original journal
                    # bytes as evidence outside the restored roots.
                    target = temp / "journal_evidence" / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(path, target)
                else:
                    shutil.copyfile(path, target)
                os.chmod(target, 0o600)
        for rows in refs.values():
            for ref in rows:
                if ref.get("directory"):
                    (temp / ref["backup_path"]).mkdir(parents=True, exist_ok=True)
        require(root_inventory(roots) == before, "Data changed during stopped boundary; snapshot rejected")
        require(boundary() == before_boundary, "Service boundary changed; snapshot rejected")
        manifest = {"format": FORMAT, "kind": "backup", "created_utc": timestamp(), "service": SERVICE, "port": 8030,
                    "roots": {name: str(root) for name, root in roots.items()}, "boundary": before_boundary,
                    "databases": databases, "references": refs, "source_files": before,
                    "files": inventory(temp), "missing_reference_waivers": profile.get("missing_reference_waivers", {})}
        write_json(temp / "BACKUP.json", manifest)
        verify_backup(temp)
        require(not destination.exists(), "Backup destination already exists")
        publish_directory(temp, destination)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
        if locked:
            lock.unlink(missing_ok=True)
    return {"backup": str(destination), "databases": databases, "manifest_sha256": digest(destination / "BACKUP.json")}


def verify_backup(directory):
    directory = Path(directory).resolve()
    manifest = read_json(directory / "BACKUP.json")
    require(manifest.get("format") == FORMAT and manifest.get("kind") == "backup" and manifest.get("port") == 8030,
            "Unknown backup format/port")
    actual = inventory(directory)
    actual.pop("BACKUP.json", None)
    require(set(actual) == set(manifest["files"]), "Backup file inventory mismatch")
    for name, row in manifest["files"].items():
        safe_name(name)
        require(actual[name]["size"] == row["size"] and actual[name]["sha256"] == row["sha256"], f"Backup hash mismatch: {name}")
    for name, evidence in manifest["databases"].items():
        safe_name(name)
        require(db_evidence(directory / name) == evidence, f"Backup database evidence mismatch: {name}")
    for refs in manifest["references"].values():
        for ref in refs:
            if "backup_path" in ref:
                safe_name(ref["backup_path"])
                require((directory / ref["backup_path"]).exists(), "Referenced bytes absent in backup")
    return manifest


def restore_drill(source, destination, apply=False):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    manifest = verify_backup(source)
    require(not destination.exists(), "Restore drill requires a new isolated directory")
    require(not source.is_relative_to(destination) and not destination.is_relative_to(source), "Restore/source overlap")
    require(all(not destination.is_relative_to(Path(r).resolve()) and not Path(r).resolve().is_relative_to(destination)
                for r in manifest["roots"].values()), "Drill must not overlap any original/live data root")
    if not apply:
        return {"dry_run": True, "destination": str(destination), "databases": list(manifest["databases"])}
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = Path(tempfile.mkdtemp(prefix=".drill-", dir=destination.parent))
    try:
        for name in manifest["files"]:
            safe_name(name)
            path = temp / name
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source / name, path)
            os.chmod(path, 0o600)
        for rows in manifest["references"].values():
            for ref in rows:
                if ref.get("directory"):
                    (temp / ref["backup_path"]).mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / "BACKUP.json", temp / "BACKUP.json")
        verify_backup(temp)
        require(not destination.exists(), "Drill destination already exists")
        publish_directory(temp, destination)
    finally:
        if temp.exists():
            shutil.rmtree(temp)
    return {"restored": str(destination), "integrity": "ok", "foreign_keys": "ok", "all_file_hashes": "ok",
            "note": "Original absolute DB references remain unchanged; evidence validates mapped bytes, no application was started"}


def prune(parent, keep, max_age_days, apply=False):
    parent = Path(parent).resolve()
    require(keep >= 2 and max_age_days >= 1, "Retain at least two backups and one day")
    candidates = []
    for item in parent.iterdir():
        require(not item.is_symlink(), "Symlink in backup parent")
        if item.is_dir() and (item / "BACKUP.json").is_file():
            manifest = verify_backup(item)
            age = (datetime.now(timezone.utc) - datetime.strptime(manifest["created_utc"], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)).days
            candidates.append((manifest["created_utc"], item, age))
    candidates.sort(reverse=True)
    selected = [p for _, p, age in candidates[keep:] if age >= max_age_days]
    if apply:
        for item in selected:
            shutil.rmtree(item)
    return {"dry_run": not apply, "remove": [str(p) for p in selected], "keep_newest": keep,
            "min_age_days": max_age_days, "unrecognized_directories": "never removed"}


def preflight(profile):
    roots = load_roots(profile)
    require(not Path(profile["source_path"]).resolve().is_relative_to(Path("/home/WZQ/CitrusQA").resolve()), "Old 8000 source forbidden")
    require((Path(profile["source_path"]) / "01_src/src/api/main.py").is_file(), "Current 8030 source missing")
    agent = Path(profile["source_path"]) / "agent"
    require(agent.resolve() in roots.values(), "src/agent must resolve to inspected existing 8030 data root")
    require(digest(profile["figure_sif"]) == BASE_SIF_SHA256, "CPU figure SIF SHA256 mismatch")
    require(Path(profile["cuda_sif"]).is_file(), "CUDA app SIF missing")
    require(re.fullmatch(r"[0-9a-f]{64}", profile["cuda_sif_sha256"] or ""), "Record the inspected CUDA SIF hash first")
    require(digest(profile["cuda_sif"]) == profile["cuda_sif_sha256"], "CUDA SIF hash mismatch")
    cache = Path(profile["model_cache_root"])
    require(cache.is_absolute() and cache.is_dir(), "Inspect real model cache root before preflight")
    for entry in profile["required_cache_files"]:
        safe_name(entry)
        require((cache / entry).is_file(), f"Missing inspected cached model file: {entry}")
    require(profile["required_cache_files"], "List concrete embedder/reranker cached files; empty cache inventory forbidden")
    dbs = schema_inventory(roots)
    require(dbs, "No state databases")
    require(profile.get("expected_databases") and set(profile["expected_databases"]) == set(dbs),
            "Fill inspected expected_databases from current8030 inventory; unknown/missing DBs block release")
    refs = {name: referenced_files(roots[name.split('/')[0]].joinpath(*name.split('/')[1:]), roots, profile) for name in dbs}
    return {"preflight": "local paths/hashes/databases/references passed", "databases": dbs,
            "reference_count": sum(map(len, refs.values())),
            "pending_host_checks": ["systemd unit/cwd/env (without printing secrets)", "listeners 8030 and untouched 8000",
                "CUDA tensor + CUDAExecutionProvider in app SIF", "CPU figure isolation availability from inside CUDA app SIF",
                "same-process loaded embedder/reranker inference readiness before listening", "private backup capacity and stopped boundary"]}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    b = sub.add_parser("bundle")
    b.add_argument("--root", required=True); b.add_argument("--output", required=True)
    b.add_argument("--release-id", required=True); b.add_argument("--compatibility")
    b.add_argument("--secret-review", help="Reviewed exact file hashes/reasons for synthetic credential scanner fixtures")
    v = sub.add_parser("verify-bundle")
    v.add_argument("--archive", required=True); v.add_argument("--sha256", required=True)
    s = sub.add_parser("stage")
    s.add_argument("--archive", required=True); s.add_argument("--releases", required=True); s.add_argument("--sha256", required=True)
    for cmd in ("preflight", "schemas", "backup", "rollback-check"):
        c = sub.add_parser(cmd); c.add_argument("--profile", required=True)
        if cmd == "backup": c.add_argument("--destination", required=True)
        if cmd == "rollback-check": c.add_argument("--compatibility", required=True)
    for cmd in ("verify-backup", "restore-drill"):
        c = sub.add_parser(cmd); c.add_argument("--backup", required=True)
        if cmd == "restore-drill": c.add_argument("--destination", required=True)
    r = sub.add_parser("prune")
    r.add_argument("--parent", required=True); r.add_argument("--keep", type=int, default=7)
    r.add_argument("--min-age-days", type=int, default=30)
    for child in (b, s, sub.choices["backup"], sub.choices["restore-drill"], r):
        child.add_argument("--apply", action="store_true", help="Perform local filesystem writes; never restart a service")
    return p


def main():
    args = parser().parse_args()
    try:
        c = args.command
        profile = read_json(args.profile) if hasattr(args, "profile") else None
        if c == "bundle": result = build_bundle(args.root, args.output, args.release_id, args.compatibility, args.apply, args.secret_review)
        elif c == "verify-bundle": result = verify_bundle(args.archive, args.sha256)
        elif c == "stage": result = stage_bundle(args.archive, args.releases, args.sha256, args.apply)
        elif c == "preflight": result = preflight(profile)
        elif c == "schemas":
            result = {"format": FORMAT, "databases": {name: [row["schema_sha256"]] for name, row in schema_inventory(load_roots(profile)).items()},
                      "review_note": "Observed schemas only. A maintainer must establish code compatibility before accepting this for rollback."}
        elif c == "backup": result = backup(profile, args.destination, apply=args.apply)
        elif c == "verify-backup": result = verify_backup(args.backup)
        elif c == "restore-drill": result = restore_drill(args.backup, args.destination, args.apply)
        elif c == "rollback-check": result = check_compatibility(load_roots(profile), read_json(args.compatibility))
        elif c == "prune": result = prune(args.parent, args.keep, args.min_age_days, args.apply)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ReleaseError, OSError, ValueError, KeyError, sqlite3.Error, subprocess.SubprocessError, tarfile.TarError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
