"""Task-owned immutable offline environment revisions, using existing file authority.

This first backend supports bounded pure-Python wheel overlays. Resolution and
installation belong to pip IN the existing isolated Worker, never to this host
module. Queue success, artifact acceptance and environment publication are three
separate facts. Network provisioning and model tools remain separate gates.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import zipfile

from .protocol import ExecutionRequest, bounded_integer, json_object, logical_path, token

MAX_BYTES = 16 * 1024**2
MAX_ENTRIES = 4096
_PREPARATIONS = """CREATE TABLE IF NOT EXISTS task_environment_preparations (
 workspace TEXT NOT NULL, fingerprint TEXT NOT NULL, snapshot TEXT NOT NULL,
 PRIMARY KEY(workspace,fingerprint))"""
_PREPARED_FIELDS = ("workspace_id", "task_id", "workspace_revision", "script", "inputs", "arguments",
                    "outputs", "environment_id", "environment_revision", "network_policy")


def preparation_fingerprint(snapshot):
    return sha256(json_object({key: snapshot[key] for key in _PREPARED_FIELDS}).encode())


_SCHEMA = """CREATE TABLE IF NOT EXISTS task_environment_revisions (
 workspace TEXT NOT NULL, environment TEXT NOT NULL, revision INTEGER NOT NULL,
 execution TEXT NOT NULL, fence INTEGER NOT NULL, fingerprint TEXT NOT NULL,
 manifest TEXT NOT NULL,
 PRIMARY KEY(workspace,environment,revision), UNIQUE(workspace,execution))"""


class EnvironmentConflict(ValueError):
    pass


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def installer_bytes():
    return Path(__file__).with_name("environment_build.py").read_bytes()


def parse_lock(data: bytes) -> dict:
    """Strict JSON data only; no URLs, requirements syntax, paths or pip options."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("environment_duplicate_json_key")
            result[key] = value
        return result
    if type(data) is not bytes or len(data) > 32768:
        raise ValueError("environment_lock_size")
    lock = json.loads(data, object_pairs_hook=unique)
    if not isinstance(lock, dict) or set(lock) != {"schema", "image_id", "runtime", "variant", "packages", "checks"}:
        raise ValueError("environment_lock_fields")
    if (lock["schema"] != "citrus.wheel-lock/1" or not isinstance(lock["image_id"], str)
            or not re.fullmatch(r"sha256:[a-f0-9]{64}", lock["image_id"])
            or lock["variant"] != "cpu"):
        raise ValueError("environment_lock_identity")
    runtime = lock["runtime"]
    if (not isinstance(runtime, dict) or set(runtime) != {"python", "platform", "machine", "pip"}
            or runtime["platform"] != "linux" or runtime["machine"] not in ("x86_64", "aarch64")
            or any(not isinstance(runtime[p], str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,2}", runtime[p])
                   for p in ("python", "pip"))):
        raise ValueError("environment_lock_runtime")
    packages, checks = lock["packages"], lock["checks"]
    if (not isinstance(packages, list) or not 1 <= len(packages) <= 32
            or not isinstance(checks, list) or not 1 <= len(checks) <= 8
            or any(not isinstance(p, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", p)
                   or len(p) > 128 for p in checks) or len(set(checks)) != len(checks)):
        raise ValueError("environment_lock_packages_or_checks")
    names, filenames, used = set(), set(), 0
    for package in packages:
        if not isinstance(package, dict) or set(package) != {"name", "version", "filename", "sha256", "size_bytes"}:
            raise ValueError("environment_package_fields")
        name, version, filename, digest = (package[k] for k in ("name", "version", "filename", "sha256"))
        if (not isinstance(name, str) or not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name)
                or name in names or len(name) > 100
                or not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_]{0,100}", version)
                or not isinstance(filename, str) or not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.+\-]{0,108}-none-any\.whl", filename)
                or ".." in filename or filename.lower() in filenames
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise ValueError("environment_package_identity")
        used += bounded_integer(package["size_bytes"], 1, MAX_BYTES, "wheel_size")
        names.add(name)
        filenames.add(filename.lower())
    if used > MAX_BYTES:
        raise ValueError("environment_wheel_total_limit")
    return lock


def inspect_overlay(data: bytes, lock: dict, lock_sha256: str):
    """Bounded passive ZIP inspection; NEVER extract or import package code here."""
    if type(data) is not bytes or len(data) > MAX_BYTES + 1024**2:
        raise ValueError("environment_overlay_byte_limit")
    # Our bounded writer emits no ZIP64, comment or split archive. Inspect the
    # fixed EOCD BEFORE ZipFile allocates one object per central-directory entry.
    import struct
    if len(data) < 22:
        raise ValueError("environment_overlay_invalid_zip")
    signature, disk, start_disk, disk_count, count, cd_size, cd_offset, comment = struct.unpack("<4s4H2LH", data[-22:])
    if (signature != b"PK\x05\x06" or disk or start_disk or comment or disk_count != count
            or not 1 <= count <= MAX_ENTRIES + 1 or cd_size > 1024**2
            or cd_offset + cd_size + 22 != len(data)):
        raise ValueError("environment_overlay_directory_limit")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        items = archive.infolist()
        if len(items) != count:
            raise ValueError("environment_overlay_entry_limit")
        seen, total = set(), 0
        for item in items:
            name = item.filename
            normalized = name[:-1] if item.is_dir() else name
            kind = stat.S_IFDIR if item.is_dir() else stat.S_IFREG
            if (not normalized or len(name) > 1024 or "\\" in name or "\x00" in item.orig_filename or name.startswith("/")
                    or any(p in ("", ".", "..") or ":" in p for p in normalized.split("/"))
                    or normalized.casefold() in seen or stat.S_IFMT(item.external_attr >> 16) not in (0, kind)
                    or item.is_dir() and item.file_size != 0
                    or item.flag_bits & 1 or item.compress_type != zipfile.ZIP_STORED
                    or normalized.lower().endswith((".pth", ".so", ".pyd", ".dll", ".dylib"))
                    or normalized == "bin" or normalized.startswith("bin/")):
                raise ValueError("environment_overlay_entry_denied")
            total += item.file_size
            if total > MAX_BYTES + 32768 or name == "citrus-environment.json" and item.file_size > 32768:
                raise ValueError("environment_overlay_expanded_limit")
            seen.add(normalized.casefold())
            # Validate CRC/actual bytes without delegating extraction to ZIP.
            if len(archive.read(item)) != item.file_size:
                raise ValueError("environment_overlay_corrupt")
        marker = json.loads(archive.read("citrus-environment.json"))
        expected = {"schema": "citrus.environment-overlay/1", "lock_sha256": lock_sha256,
                    "runtime": lock["runtime"], "packages": {p["name"]: p["version"] for p in lock["packages"]}}
        if marker != expected:
            raise ValueError("environment_overlay_marker_mismatch")


def prepare_install(workspace, *, workspace_id, task_id, expected_revision, lock_path):
    """Stage the fixed installer; return exact arguments for ExecutionService.submit.

    This is preparation, not submission or publication. The caller must persist
    the returned request in its existing operation journal before submitting it.
    A preparation conflict never silently overwrites a user-edited script.
    """
    from src.core.account_leases import account_lease
    from src.session import files
    logical_path(lock_path)
    if not lock_path.startswith("/input/"):
        raise ValueError("environment_lock_input_required")
    with account_lease(workspace.owner):
        snapshot = workspace.snapshot(workspace_id, task_id=task_id, expected_revision=expected_revision)
        entries = {p["path"]: p for p in snapshot["files"]}
        if lock_path not in entries:
            raise LookupError("environment_lock_not_bound")
        entry = entries[lock_path]
        data = workspace.read_bytes(workspace_id, path=lock_path, version=entry["version"], expected_sha256=entry["sha256"])
        lock = parse_lock(data)
        prefix = str(PurePosixPath(lock_path).parent)
        inputs = [lock_path]
        for package in lock["packages"]:
            path = prefix + "/wheels/" + package["filename"]
            logical_path(path)
            item = entries.get(path)
            if item is None or (item["sha256"], item["size_bytes"]) != (package["sha256"], package["size_bytes"]):
                raise ValueError("environment_wheel_not_bound_or_mismatched")
            inputs.append(path)
        code = installer_bytes()
        script_path = "/workspace/environment_" + sha256(data + code)[:24] + ".py"
        prior = entries.get(script_path)
        if prior is not None:
            if prior["sha256"] != sha256(code):
                raise EnvironmentConflict("environment_installer_path_conflict")
        else:
            script = files.register_file(owner_user_id=workspace.owner, session_id=workspace.session,
                filename="environment_installer.py", content=code, visibility="internal",
                producer_tool="environment_install", client_request_id=sha256((workspace_id + script_path).encode()))
            workspace.bind(workspace_id, path=script_path, file_id=script["file_id"],
                expected_revision=expected_revision, expected_sha256=script["sha256"])
            expected_revision += 1
        output = "/outputs/environment_" + sha256((lock_path + sha256(data)).encode())[:24] + ".zip"
        args = {"workspace_id": workspace_id, "task_id": task_id, "expected_revision": expected_revision,
                "script_path": script_path, "arguments": (lock_path, output), "inputs": tuple(inputs),
                "outputs": (output,), "environment_id": "base", "environment_revision": 1, "network_policy": "offline"}
        current = workspace.snapshot(workspace_id, task_id=task_id, expected_revision=expected_revision)
        entries = {p["path"]: p for p in current["files"]}
        approved = {key: args[key] for key in ("workspace_id", "task_id", "arguments", "outputs",
                    "environment_id", "environment_revision", "network_policy")}
        approved.update(workspace_revision=expected_revision, script=entries[script_path],
                        inputs=[entries[path] for path in inputs])
        # Persist the trusted preparation decision, not a hash recomputed from
        # whatever installer happens to be deployed when publication is retried.
        with workspace.repository._transaction(workspace.owner, workspace.session) as (conn, _):
            work = workspace.repository._workspace(conn, workspace.owner, workspace.session, workspace_id)
            workspace.repository._cas(work, expected_revision)
            conn.execute(_PREPARATIONS)
            fingerprint = preparation_fingerprint(approved)
            if (conn.execute("SELECT COUNT(*) FROM task_environment_preparations WHERE workspace=?", (workspace_id,)).fetchone()[0] >= 128
                    and not conn.execute("SELECT 1 FROM task_environment_preparations WHERE workspace=? AND fingerprint=?",
                                         (workspace_id, fingerprint)).fetchone()):
                raise ValueError("environment_preparation_quota_exceeded")
            conn.execute("INSERT OR IGNORE INTO task_environment_preparations VALUES(?,?,?)",
                         (workspace_id, fingerprint, json_object(approved)))
        return args


class EnvironmentStore:
    """Authority resides in the existing authorized workspace/files transaction."""
    def __init__(self, workspace):
        self.workspace = workspace

    def _transaction(self):
        return self.workspace.repository._transaction(self.workspace.owner, self.workspace.session)

    def get(self, workspace_id, *, environment_id, revision):
        token(environment_id)
        bounded_integer(revision, 1, 2**31 - 1, "environment_revision")
        with self._transaction() as (conn, _):
            self.workspace.repository._workspace(conn, self.workspace.owner, self.workspace.session, workspace_id)
            conn.execute(_SCHEMA)
            row = conn.execute("SELECT manifest FROM task_environment_revisions WHERE workspace=? AND environment=? AND revision=?",
                (workspace_id, environment_id, revision)).fetchone()
            if row is None:
                raise LookupError("environment_revision_not_found")
            return json.loads(row["manifest"])

    def read_overlay(self, request):
        if (request.identity.owner_user_id, request.identity.session_id) != (self.workspace.owner, self.workspace.session):
            raise PermissionError("environment_execution_scope_mismatch")
        work = self.workspace.open(request.workspace_id)
        if work["task_id"] != request.task_id:
            raise PermissionError("environment_task_mismatch")
        manifest = self.get(request.workspace_id, environment_id=request.environment_id, revision=request.environment_revision)
        ref = manifest["archive"]
        data = self.workspace.read_bytes(request.workspace_id, path=ref["path"], version=ref["version"], expected_sha256=ref["sha256"])
        if len(data) != ref["size_bytes"]:
            raise ValueError("environment_archive_size_mismatch")
        inspect_overlay(data, manifest["lock"], manifest["lock_sha256"])
        return manifest, data

    def publish(self, executions, *, execution_id, environment_id, expected_revision):
        """Only a successful fixed-installer attempt may publish; CAS + pins commit together."""
        from src.core.account_leases import acquire_account_lease
        from src.session import files
        token(environment_id)
        if environment_id == "base":
            raise ValueError("environment_base_is_immutable")
        bounded_integer(expected_revision, 0, 2**31 - 2, "environment_expected_revision")
        if (executions.identity.owner_user_id, executions.identity.session_id) != (self.workspace.owner, self.workspace.session):
            raise PermissionError("environment_execution_scope_mismatch")
        row = executions._owned(execution_id)
        request = ExecutionRequest.parse(row["request"])
        lease = acquire_account_lease(self.workspace.owner, expected_epoch=request.account_epoch)
        try:
            with self._transaction() as (conn, _):
                self.workspace.repository._workspace(conn, self.workspace.owner, self.workspace.session, request.workspace_id)
                conn.execute(_SCHEMA)
                prior = conn.execute("SELECT environment,revision,manifest FROM task_environment_revisions WHERE workspace=? AND execution=?",
                                     (request.workspace_id, execution_id)).fetchone()
                if prior is not None:
                    if prior["environment"] != environment_id or prior["revision"] != expected_revision + 1:
                        raise EnvironmentConflict("environment_publication_conflict")
                    return json.loads(prior["manifest"])
                conn.execute(_PREPARATIONS)
                approved = conn.execute("SELECT 1 FROM task_environment_preparations WHERE workspace=? AND fingerprint=?",
                    (request.workspace_id, preparation_fingerprint(request.as_dict()))).fetchone()
            receipt = row["receipt"] or {}
            if (not approved or row["status"] != "succeeded" or receipt.get("process_stopped") is not True
                    or receipt.get("request_fingerprint") != request.fingerprint
                    or (request.environment_id, request.environment_revision, request.network_policy) != ("base", 1, "offline")
                    or len(request.arguments) != 2 or request.outputs != (request.arguments[1],)):
                raise ValueError("environment_successful_installer_required")
            lock_path = request.arguments[0]
            entry = next((p for p in request.inputs if p.path == lock_path), None)
            if entry is None:
                raise ValueError("environment_lock_snapshot_missing")
            lock_data = self.workspace.read_bytes(request.workspace_id, path=entry.path, version=entry.version, expected_sha256=entry.sha256)
            lock = parse_lock(lock_data)
            prefix = str(PurePosixPath(lock_path).parent)
            actual = {p.path: (p.sha256, p.size_bytes) for p in request.inputs if p.path != lock_path}
            expected = {prefix + "/wheels/" + p["filename"]: (p["sha256"], p["size_bytes"]) for p in lock["packages"]}
            if actual != expected:
                raise ValueError("environment_wheel_snapshot_mismatch")
            # Container identity is the controller's private persisted handle,
            # never a hash/image claim printed by package code on stdout.
            from .handles import parse_handle
            with executions.repository._connection() as conn:
                attempt = conn.execute("SELECT handle FROM code_worker_attempts WHERE execution_id=? AND fence=?",
                                       (execution_id, row["fence"])).fetchone()
            handle = parse_handle(json.loads(attempt["handle"])) if attempt else None
            if handle is None or handle.image_id != lock["image_id"] or handle.request.fingerprint != request.fingerprint:
                raise ValueError("environment_base_image_mismatch")
            refs = receipt.get("files", [])
            if len(refs) != 1 or refs[0]["path"] != request.outputs[0]:
                raise ValueError("environment_accepted_archive_required")
            ref = refs[0]
            data = self.workspace.read_bytes(request.workspace_id, path=ref["path"], version=ref["version"], expected_sha256=ref["sha256"])
            if len(data) != ref["size_bytes"]:
                raise ValueError("environment_archive_size_mismatch")
            inspect_overlay(data, lock, sha256(lock_data))
            manifest = {"schema": "citrus.task-environment/1", "environment_id": environment_id,
                "revision": expected_revision + 1, "workspace_id": request.workspace_id, "task_id": request.task_id,
                "lock": lock, "lock_sha256": sha256(lock_data), "archive": ref,
                "execution_id": execution_id, "fence": row["fence"], "request_fingerprint": request.fingerprint}
            with self._transaction() as (conn, _):
                self.workspace.repository._workspace(conn, self.workspace.owner, self.workspace.session, request.workspace_id)
                conn.execute(_SCHEMA)
                prior = conn.execute("SELECT environment,revision,manifest FROM task_environment_revisions WHERE workspace=? AND execution=?",
                    (request.workspace_id, execution_id)).fetchone()
                if prior is not None:
                    if prior["environment"] != environment_id or prior["revision"] != expected_revision + 1:
                        raise EnvironmentConflict("environment_publication_conflict")
                    return json.loads(prior["manifest"])
                latest = conn.execute("SELECT COALESCE(MAX(revision),0) FROM task_environment_revisions WHERE workspace=? AND environment=?",
                    (request.workspace_id, environment_id)).fetchone()[0]
                if latest != expected_revision:
                    raise EnvironmentConflict("environment_revision_conflict")
                if conn.execute("SELECT COUNT(*) FROM task_environment_revisions WHERE workspace=?",
                                (request.workspace_id,)).fetchone()[0] >= 32:
                    raise ValueError("environment_revision_quota_exceeded")
                # A revision independently pins its exact archive, installer,
                # lock and wheel versions, without copying them to another store.
                pin_id = sha256((request.workspace_id + ":" + environment_id + ":" + str(expected_revision + 1)).encode())
                files._replace_resource_references_locked(conn, self.workspace.owner, "task_environment", pin_id,
                    [ref["file_id"], request.script.file_id, *(p.file_id for p in request.inputs)])
                conn.execute("INSERT INTO task_environment_revisions VALUES(?,?,?,?,?,?,?)",
                    (request.workspace_id, environment_id, expected_revision + 1, execution_id,
                     row["fence"], request.fingerprint, json_object(manifest)))
            return manifest
        finally:
            lease.release()
