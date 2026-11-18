"""Trusted private service for Code Apptainer; never imported by user Python.

Runs only after the systemd cgroup guard and unshare's user/mount isolation.
The socket and control data are outside every guest bind. Scratch exists solely
in this private mount namespace; killing the cgroup destroys it.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import sys
import threading


def atomic_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def validate_sif(c, prefix='sif'):
    import hashlib
    path = Path(c[prefix + '_path'])
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("linked_sif_denied")
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_mode & 0o022:
        raise ValueError("immutable_regular_sif_required")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise ValueError("sif_changed")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
        after = os.fstat(stream.fileno())
    if (digest.hexdigest() != c[prefix + '_sha256'] or not os.path.samestat(after, path.stat())
            or (before.st_size, before.st_mtime_ns, before.st_ctime_ns)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns)):
        raise ValueError("sif_pin_mismatch")


def mount_scratch(root, budgets):
    libc = ctypes.CDLL(None, use_errno=True)
    # Private mount propagation, including the new tmpfs mounts. No host mount
    # can receive a guest write or a mount created in this namespace.
    if libc.mount(None, b"/", None, (1 << 18) | (1 << 14), None):
        raise OSError(ctypes.get_errno(), "private_mount_namespace_required")
    for name, size in budgets.items():
        path = root / name
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or path.is_symlink() or path.is_mount():
            raise ValueError("private_scratch_directory_required")
        options = f"size={size},nr_inodes=4096,mode=0700,uid={os.getuid()},gid={os.getgid()}".encode()
        if libc.mount(b"tmpfs", os.fsencode(path), b"tmpfs", 2 | 4 | 8, options):
            raise OSError(ctypes.get_errno(), "bounded_tmpfs_required")
        fs = os.statvfs(path)
        if fs.f_blocks * fs.f_frsize > size or fs.f_files > 4096:
            raise ValueError("tmpfs_budget_unconfirmed")


def runtime_identity(c):
    relative = next(line[3:] for line in Path("/proc/self/cgroup").read_text().splitlines()
                    if line.startswith("0::"))
    if ".." in relative.split("/") or not relative.endswith("/" + c["unit"]):
        raise ValueError("service_cgroup_identity_mismatch")
    cg = Path("/sys/fs/cgroup") / relative.lstrip("/")
    info = cg.stat()
    show = subprocess.run(["/usr/bin/systemctl", "--user", "show", c["unit"],
        "--property=InvocationID", "--value"], env=c["host_env"], stdin=subprocess.DEVNULL,
        capture_output=True, timeout=10, check=True)
    invocation = show.stdout.decode("ascii").strip()
    import re
    if not re.fullmatch(r"[a-f0-9]{32}", invocation):
        raise ValueError("service_invocation_identity_required")
    identity = {"backend": "apptainer-v1", "unit": c["unit"], "invocation": invocation,
                "cgroup": relative, "device": info.st_dev, "inode": info.st_ino,
                "handle_sha256": c["handle_sha256"]}
    return identity


def main():
    bootstrap = sys.argv[1] == "--bootstrap"
    settings_path = Path(sys.argv[2] if bootstrap else sys.argv[1])
    c = json.loads(settings_path.read_text(encoding="utf-8"))
    root = settings_path.parent
    identity = runtime_identity(c)
    if bootstrap:
        # Persist before the cgroup guard/unshare: denied namespace or mount
        # support must still have an exact stopped invocation to reconcile.
        atomic_json(root / "identity.json", identity)
        if c.get('namespace_sif_path'):
            validate_sif(c, 'namespace_sif')
        os.execv(c["guard_command"][0], c["guard_command"])
        raise AssertionError("exec_returned")
    if json.loads((root / "identity.json").read_text(encoding="utf-8")) != identity:
        raise ValueError("service_runtime_identity_changed")
    validate_sif(c)
    mount_scratch(root / "scratch", c["budgets"])
    process = subprocess.Popen(c["command"], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=c["host_env"], close_fds=True)
    buffers = [bytearray(), bytearray()]
    overflow = threading.Event()
    lock = threading.Lock()
    def drain(stream, index):
        while block := stream.read1(8192):
            with lock:
                remaining = c["summary_bytes"] - sum(map(len, buffers))
                buffers[index].extend(block[:max(0, remaining)])
                if len(block) > remaining:
                    overflow.set()
        stream.close()
    for index, stream in enumerate((process.stdout, process.stderr)):
        threading.Thread(target=drain, args=(stream, index), daemon=True).start()
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(str(root / "control.sock"))
    os.chmod(root / "control.sock", 0o600)
    listener.listen(2)
    while True:
        connection, _ = listener.accept()
        with connection:
            connection.settimeout(10)
            try:
                data = bytearray()
                while not data.endswith(b"\n"):
                    block = connection.recv(4097 - len(data))
                    if not block or len(data) + len(block) > 4096:
                        raise ValueError("bounded_control_request_required")
                    data.extend(block)
                request = json.loads(data)
                if (request.get("identity") != identity or request.get("operation") not in ("inspect", "collect")
                        or type(request.get("outputs", False)) is not bool):
                    raise ValueError("foreign_control_request")
                if overflow.is_set():
                    raise ValueError("supervisor_record_overflow")
                with lock:
                    payload = bytes(buffers[0])
                response = {"identity": identity, "phase": "running"}
                # An exited Apptainer cannot retain a collecting supervisor.
                if process.poll() is not None:
                    raise ValueError("supervisor_exited")
                if payload.endswith(b"\n"):
                    summary = json.loads(payload)
                    response.update(phase="retained", summary=summary)
                if request["operation"] == "collect":
                    if response["phase"] != "retained":
                        raise ValueError("retained_supervisor_required")
                    response["outputs"] = {}
                    if request.get("outputs") and c["outputs"]:
                        # Existing NOFOLLOW + inode/link/type/size/change checks,
                        # running as trusted service code over private tmpfs.
                        collected = subprocess.run([sys.executable, "-I", "-c", c["collector"],
                            json.dumps({"paths": c["outputs"], "bytes": c["output_bytes"]})],
                            stdin=subprocess.DEVNULL, capture_output=True, timeout=10,
                            env=c["host_env"], check=True)
                        if len(collected.stdout) > c["collection_bytes"]:
                            raise ValueError("collection_record_overflow")
                        response["outputs"] = json.loads(collected.stdout)
                encoded = json.dumps(response, separators=(",", ":")).encode()
                if len(encoded) > c["collection_bytes"] + c["summary_bytes"] + 16384:
                    raise ValueError("control_record_overflow")
                connection.sendall(encoded)
            except Exception as exc:
                # Operator diagnostics contain only type and process state, never
                # the guest output, exception text, input bytes or credentials.
                print(json.dumps({'event': 'code_observation_unconfirmed',
                    'error_type': type(exc).__name__, 'child_exit': process.poll()}), file=sys.stderr, flush=True)
                try:
                    connection.sendall(b'{"error":"service_observation_unconfirmed"}')
                except OSError:
                    pass


if __name__ == "__main__":
    main()
