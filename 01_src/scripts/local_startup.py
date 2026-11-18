"""Start the complete Windows workspace, including the WSL drawing sandbox."""
from __future__ import annotations

import argparse
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import sys
import time
from urllib.request import ProxyHandler, build_opener
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "01_src"))
PROFILE = "full-v1"


def log(message):
    print(f"[CitrusScholar startup] {message}", flush=True)


def listening(port):
    with socket.socket() as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def reuse_existing(port, distribution, image, record_path):
    """A port alone does not identify our application or a healthy profile."""
    import psutil
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
        process = psutil.Process(record["pid"])
        if (abs(process.create_time() - record["created"]) > 0.01
                or Path(process.exe()).resolve() != Path(sys.executable).resolve()
                or record["distribution"] != distribution or record["image"] != image):
            return False
        owners = {item.pid for item in psutil.net_connections(kind="tcp")
                  if item.status == psutil.CONN_LISTEN and item.laddr.port == port}
        if owners != {process.pid}:
            return False
        opener = build_opener(ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/ready", timeout=3) as response:
            ready = json.load(response)
        identity = ready.get("local_runtime") or {}
        matches = bool(ready.get("ready") and ready.get("status") == "ready"
                       and ready.get("worker", {}).get("verified")
                       and identity.get("profile") == PROFILE
                       and identity.get("instance_id") == record["instance_id"]
                       and identity.get("pid") == process.pid
                       and identity.get("workspace_id") == hashlib.sha256(
                           os.path.normcase(str(ROOT.resolve())).encode("utf-8")).hexdigest())
        if not matches:
            return False
        # A cached HTTP receipt alone cannot prove an idle Docker daemon is
        # still running. Recheck/restart it without launching probe containers.
        from src.figures.wsl_docker import WslDockerBackend
        runner = WslDockerBackend(image=image, distribution=distribution)
        prepare_docker(runner)
        return hashlib.sha256(str(runner.image_id).encode()).hexdigest() == ready["worker"].get("image_fingerprint")
    except (OSError, ValueError, KeyError, psutil.Error):
        return False


@contextmanager
def startup_lock(path):
    import msvcrt
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


def verify_support_installation():
    """Fail before touching Docker/state when the selected interpreter is stale."""
    from importlib.metadata import version
    if version("citrusscholar-support") != "2.0.0":
        raise RuntimeError("Install the citrusscholar-support 2.0.0 wheel in the selected Conda environment first.")
    from citrus_figures.nature_runtime import validate_vendor
    validate_vendor(ROOT / "skills/nature-figure/vendor/nature-skills")


def configure(distribution, image):
    verify_support_installation()
    agent = ROOT / "agent"
    cache = agent / ".hf_cache"
    if not (agent / "data/active_index.json").is_file() or not cache.is_dir():
        raise RuntimeError("Existing local index/model cache is missing; startup will not rebuild or download it.")
    stage = agent / "tmp/local-figure-jobs"
    stage.mkdir(parents=True, exist_ok=True)
    os.environ.update({
        "HF_HOME": str(cache / "hf"), "FASTEMBED_CACHE_PATH": str(cache / "fastembed"),
        "CITRUSQA_MODEL_CACHE_ROOT": str(cache), "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1", "FIGURES_SANDBOX_BACKEND": "wsl-docker",
        "FIGURES_SANDBOX_WSL_DISTRO": distribution, "FIGURES_SANDBOX_IMAGE": image,
        "FIGURES_SANDBOX_RUNTIME": "", "FIGURES_SANDBOX_ALLOW_LOCAL": "false",
        "CITRUSQA_FIGURE_JOB_DIR": str(stage), "CITRUS_REQUIRE_FIGURE_WORKER": "1",
        "CITRUS_LOCAL_FULL_STARTUP": "1", "CITRUS_LOCAL_INSTANCE_ID": uuid.uuid4().hex,
    })
    # Import after establishing the profile; do not import the app/models yet.
    from src.figures.wsl_docker import validate_staging, WslDockerBackend
    validate_staging()
    return WslDockerBackend(image=image, distribution=distribution)


def prepare_docker(runner):
    log(f"Checking WSL {runner.distribution} and its local Docker daemon")
    runner._control(runner._wsl_command("/usr/bin/true"), timeout=45)
    try:
        runner._control(runner._docker_command("version", "--format", "{{.Server.Version}}"), timeout=10)
    except Exception:
        # Never prompt for a password or change sudo policy. A previously
        # configured noninteractive authorization may start the existing daemon.
        log("Docker is stopped; requesting its existing systemd service")
        try:
            runner._control(runner._wsl_command("/usr/bin/sudo", "-n",
                            "/usr/bin/systemctl", "start", "docker"), timeout=45)
        except Exception as exc:
            raise RuntimeError("Docker could not start noninteractively in the selected WSL distribution. "
                               "Start its docker service in WSL, then run this launcher again. " + str(exc)) from exc
        deadline = time.monotonic() + 30
        while True:
            try:
                runner._control(runner._docker_command("version", "--format", "{{.Server.Version}}"), timeout=5)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise RuntimeError("WSL Docker did not become ready within 30 seconds.")
                time.sleep(0.5)
    log(f"Drawing image: {runner.resolve_image()}; no pull or rebuild requested")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--distribution", default="Ubuntu-24.04")
    parser.add_argument("--image", default="citrusqa/sandbox:1.0.0")
    args = parser.parse_args(argv)
    if os.name != "nt":
        parser.error("This local entrypoint requires Windows Python and an installed WSL distribution.")
    if not 1024 <= args.port <= 65535:
        parser.error("Port must be between 1024 and 65535.")
    os.chdir(ROOT)
    state = ROOT / "agent/state"
    state.mkdir(parents=True, exist_ok=True)
    record_path = state / f"local-service-{args.port}.json"
    if listening(args.port):
        if reuse_existing(args.port, args.distribution, args.image, record_path):
            log(f"Reusing healthy complete workspace at http://127.0.0.1:{args.port}")
            return 0
        raise RuntimeError(f"Port {args.port} is occupied by another service, an older startup profile, "
                           "or an unhealthy instance; no second application was started.")
    lock = startup_lock(state / f"local-service-{args.port}.lock")
    try:
        lock.__enter__()
    except OSError:
        deadline = time.monotonic() + 180
        log("This workspace is already starting; waiting for its readiness")
        while time.monotonic() < deadline:
            if reuse_existing(args.port, args.distribution, args.image, record_path):
                log(f"Reusing healthy complete workspace at http://127.0.0.1:{args.port}")
                return 0
            time.sleep(1)
        raise RuntimeError("The existing startup did not become ready within 180 seconds.")
    instance = None
    try:
        runner = configure(args.distribution, args.image)
        prepare_docker(runner)
        import psutil
        instance = os.environ["CITRUS_LOCAL_INSTANCE_ID"]
        record = dict(pid=os.getpid(), created=psutil.Process().create_time(), instance_id=instance,
                      workspace=str(ROOT), distribution=args.distribution, image=args.image, profile=PROFILE)
        temporary = record_path.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(record), encoding="utf-8")
        temporary.replace(record_path)
        log("Starting Windows models, retrieval and actual drawing/edit/export verification")
        import uvicorn
        uvicorn.run("src.api.main:app", host="127.0.0.1", port=args.port, workers=1,
                    timeout_graceful_shutdown=30)
        return 0
    finally:
        try:
            if instance and json.loads(record_path.read_text(encoding="utf-8")).get("instance_id") == instance:
                record_path.unlink()
        except (OSError, ValueError):
            pass
        lock.__exit__(None, None, None)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"FAILED: {exc}")
        raise SystemExit(1)
