"""Windows transport for the existing restricted Linux Docker figure worker.

Only the two per-job mounts cross WSL. No shell command, application mount,
Docker TCP listener, credential environment, or local-script fallback is used.
"""
from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import threading
import time
import uuid

from src.core.cancel import raise_if_cancelled
from .sandbox import (
    DockerBackend, SandboxError, SandboxJob, SandboxResult, SandboxUnavailable,
    EXIT_OK, EXIT_TIMEOUT, EXIT_OUTPUT_TOO_LARGE, FAILURE_OUTPUT,
    FAILURE_MESSAGES, _interpret, _job_directory, _read_outputs, _write_job_files,
)

MAX_LOG_BYTES = 64 * 1024


class CleanupUnconfirmed(SandboxUnavailable):
    """Keep staging when Docker cannot confirm that its mount user stopped."""


class _WslJobDirectory(_job_directory):
    def __exit__(self, *exc_info):
        if isinstance(exc_info[1], CleanupUnconfirmed):
            return
        super().__exit__(*exc_info)


def windows_env() -> dict[str, str]:
    # wsl.exe needs the Windows loader environment, not the application's keys.
    return {key: os.environ[key] for key in ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP")
            if key in os.environ}


def validate_staging() -> Path:
    value = os.environ.get("CITRUSQA_FIGURE_JOB_DIR", "").strip()
    if not value:
        raise SandboxUnavailable("WSL drawing requires an explicit managed staging directory.")
    root = Path(value)
    if not root.is_absolute() or not root.is_dir():
        raise SandboxUnavailable("The configured WSL drawing staging directory is unavailable.")
    for path in (root, *root.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise SandboxUnavailable("WSL drawing staging must not traverse links or reparse points.")
    # Commas/newlines cannot be represented safely in Docker's mount grammar.
    if any(char in str(root) for char in ",\r\n\0"):
        raise SandboxUnavailable("Unsupported character in WSL drawing staging path.")
    return root.resolve()


class WslDockerBackend(DockerBackend):
    name = "wsl-docker"

    def __init__(self, *, image: str = "", runtime: str = "", distribution: str = "Ubuntu-24.04"):
        super().__init__(image=image, runtime=runtime)
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", distribution):
            raise SandboxUnavailable("Invalid WSL distribution name.")
        self.distribution = distribution
        self.wsl = shutil.which("wsl.exe") or shutil.which("wsl") or ""
        self.image_id = ""

    def _wsl_command(self, *arguments: str) -> list[str]:
        if not self.wsl:
            raise SandboxUnavailable("Windows WSL executable is unavailable.")
        return [self.wsl, "--distribution", self.distribution, "--exec", *arguments]

    def _docker_command(self, *arguments: str) -> list[str]:
        return self._wsl_command(
            "/usr/bin/env", "-u", "DOCKER_HOST", "-u", "DOCKER_CONTEXT",
            "-u", "DOCKER_TLS_VERIFY", "-u", "DOCKER_CERT_PATH", "-u", "DOCKER_CONFIG",
            "/usr/bin/docker", "--host", "unix:///var/run/docker.sock", *arguments)

    def _host_env(self) -> dict[str, str]:
        return windows_env()

    def _control(self, arguments: list[str], timeout: int = 20) -> str:
        result = subprocess.run(arguments, stdin=subprocess.DEVNULL, capture_output=True,
                                timeout=timeout, env=self._host_env())
        if result.returncode:
            raise SandboxUnavailable(result.stderr.decode("utf-8", "replace")[-2000:]
                                     or "WSL Docker control command failed.")
        return result.stdout.decode("utf-8", "strict").strip()

    def resolve_image(self) -> str:
        if not self.image:
            raise SandboxUnavailable("No local drawing image is configured.")
        identity = self._control(self._docker_command("image", "inspect", "--format", "{{.Id}}", self.image))
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", identity):
            raise SandboxUnavailable("The local drawing image has no valid immutable identity.")
        self.image_id = identity
        return identity

    def available(self) -> bool:
        try:
            if os.name != "nt" or not self.wsl:
                return False
            validate_staging()
            self._control(self._docker_command("version", "--format", "{{.Server.Version}}"))
            self.resolve_image()
            return True
        except (OSError, ValueError, UnicodeError, SandboxError, SandboxUnavailable, subprocess.SubprocessError) as exc:
            from .readiness import invalidate_worker
            invalidate_worker(str(exc))
            return False

    def _mount_source(self, path: Path) -> str:
        root = validate_staging()
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved.name not in {"in", "out"}:
            raise SandboxUnavailable("Only the managed job input/output directories may be mounted.")
        for item in (path, path.parent):
            info = item.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise SandboxUnavailable("Linked job mount refused.")
        mapped = self._control(self._wsl_command("/usr/bin/wslpath", "-a", "-u", str(resolved)))
        if not mapped.startswith("/") or any(c in mapped for c in ",\r\n\0"):
            raise SandboxUnavailable("Invalid WSL mount mapping.")
        returned = self._control(self._wsl_command("/usr/bin/wslpath", "-a", "-w", mapped))
        if Path(returned).resolve() != resolved:
            raise SandboxUnavailable("WSL mount mapping does not round-trip to the managed directory.")
        return mapped

    def _container_name(self, job: SandboxJob) -> str:
        return "citrusqa-fig-" + uuid.uuid4().hex

    def build_command(self, job: SandboxJob, *, workdir: Path, name: str = "") -> list[str]:
        command = super().build_command(job, workdir=workdir, name=name)
        command.insert(command.index(job.image or self.image), "--pull=never")
        return command

    def _remove_container(self, name: str) -> None:
        try:
            result = subprocess.run(self._docker_command("rm", "--force", name),
                                    stdin=subprocess.DEVNULL, capture_output=True,
                                    timeout=15, env=self._host_env())
        except (OSError, subprocess.SubprocessError) as exc:
            raise CleanupUnconfirmed("Could not confirm drawing container cleanup; staging retained.") from exc
        error = result.stderr.decode("utf-8", "replace").lower()
        if result.returncode and not any(text in error for text in ("no such container", "no such object")):
            raise CleanupUnconfirmed("Could not confirm drawing container cleanup; staging retained: " + error[-1000:])

    def _execute(self, command: list[str], name: str, timeout: int) -> tuple[int, str, str, int]:
        started = time.monotonic()
        streams = [bytearray(), bytearray()]
        overflow = threading.Event()
        process = None
        readers = []

        def drain(stream, index):
            try:
                while block := stream.read(4096):
                    remaining = MAX_LOG_BYTES - len(streams[index])
                    streams[index].extend(block[:max(0, remaining)])
                    if len(block) > remaining:
                        overflow.set()
            finally:
                stream.close()

        try:
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       env=self._host_env())
            for index, stream in enumerate((process.stdout, process.stderr)):
                thread = threading.Thread(target=drain, args=(stream, index), daemon=True)
                thread.start()
                readers.append(thread)
            while process.poll() is None:
                raise_if_cancelled("wsl_docker_wait")
                if overflow.wait(0.05):
                    code = EXIT_OUTPUT_TOO_LARGE
                    break
                if time.monotonic() - started > timeout:
                    code = EXIT_TIMEOUT
                    break
            else:
                code = process.returncode
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=5)
            finally:
                # A stopped Windows client does not mean the Linux container stopped.
                self._remove_container(name)
                for reader in readers:
                    reader.join(timeout=5)
        if overflow.is_set():
            code = EXIT_OUTPUT_TOO_LARGE
        return code, *(bytes(data).decode("utf-8", "replace") for data in streams), int((time.monotonic() - started) * 1000)

    def run(self, job: SandboxJob) -> SandboxResult:
        raise_if_cancelled("wsl_docker_start")
        from .readiness import check_image_identity, invalidate_worker
        try:
            validate_staging()
            identity = self.resolve_image()
            check_image_identity(identity)
            if job.image and job.image not in {self.image, identity}:
                raise SandboxUnavailable("A job cannot replace the configured drawing image.")
            name = self._container_name(job)
            with _WslJobDirectory() as workdir:
                _write_job_files(workdir, job)
                pinned = replace(job, image=identity)
                code, stdout, stderr, duration = self._execute(
                    self.build_command(pinned, workdir=workdir, name=name), name,
                    int(job.timeout_sec) + 30)
                raise_if_cancelled("wsl_docker_collect")
                if code == EXIT_OUTPUT_TOO_LARGE:
                    return SandboxResult(exit_code=code, failure_kind=FAILURE_OUTPUT,
                        message=FAILURE_MESSAGES[FAILURE_OUTPUT], duration_ms=duration, backend=self.name)
                if code == 125:
                    invalidate_worker("Docker could not start the drawing container: " + stderr[-1000:])
                try:
                    outputs = _read_outputs(workdir, job) if code == EXIT_OK else {}
                except SandboxError as exc:
                    # Safe collection must still fail closed, but retain the
                    # worker's explanation when it failed to produce a slot.
                    detail = f'WSL drawing output collection failed (worker exit {code}): {exc}'
                    if exc.__cause__ is not None:
                        detail += f'; cause: {exc.__cause__}'
                    detail += f'; stdout: {stdout[-500:]}; stderr: {stderr[-1500:]}'
                    raise SandboxError(detail) from exc
                return _interpret(code, stdout, stderr, outputs, duration, self.name,
                                  timeout_sec=int(job.timeout_sec))
        except (OSError, SandboxError, SandboxUnavailable, subprocess.SubprocessError) as exc:
            invalidate_worker(str(exc))
            raise
