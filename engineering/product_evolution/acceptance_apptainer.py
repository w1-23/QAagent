"""Opt-in isolated Linux runtime acceptance; never starts or changes a service.

Run from a freshly staged minimal source tree, with the existing operator SIF.
All generated artifacts are synthetic engineering fixtures, not research data.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--results", required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(args.source_root).resolve()))
    from src.core.cancel import CancelToken, ToolCancelled, bound_token
    from src.figures.apptainer import ApptainerBackend, MAX_LOG_BYTES, _host_env
    from src.figures.sandbox import SandboxJob

    destination = Path(args.results).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    image = Path(args.image)
    digest = hashlib.sha256()
    with image.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    image_hash = digest.hexdigest()
    evidence = {"image": str(image), "image_sha256": image_hash,
                "synthetic_fixtures_only": True, "checks": {}, "units": []}

    class ObservedBackend(ApptainerBackend):
        def build_command(self, *a, **kw):
            evidence["units"].append(kw["unit"])
            command = super().build_command(*a, **kw)
            (destination / "last-launch.json").write_text(json.dumps({
                "argv": command, "host_environment": _host_env()}, indent=2))
            return command

    runner = ObservedBackend(image=str(image), image_sha256=image_hash)

    def check(name, condition, detail=None):
        evidence["checks"][name] = {"ok": bool(condition), "detail": detail}
        (destination / "acceptance.json").write_text(json.dumps(evidence, indent=2))
        if not condition:
            raise AssertionError(name + ": " + str(detail))

    check("isolated_runtime_available", runner.available())
    invalid = ApptainerBackend(image=str(image), image_sha256="0" * 64)
    check("invalid_image_pin_refused", not invalid.available())
    outputs = {"svg": "figure.svg", "png": "figure.png", "pdf": "figure.pdf",
               "manifest": "objects.json"}
    scripts = {
        "matplotlib": (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots()\n"
            "ax.plot([0, 1, 2], [1, 3, 2], label='Synthetic fixture')\n"
            "ax.set_title('Synthetic engineering fixture')\nax.legend()\n"),
        "seaborn": (
            "import matplotlib.pyplot as plt\nimport seaborn as sns\nimport pandas as pd\n"
            "frame = pd.DataFrame({'x': [0, 1, 2], 'y': [1, 3, 2]})\n"
            "ax = sns.lineplot(data=frame, x='x', y='y', label='Synthetic fixture')\n"
            "ax.set_title('Synthetic engineering fixture')\n"),
    }
    for name, script in scripts.items():
        original = SandboxJob(script=script, outputs=outputs, image_sha256=image_hash)
        created = runner.run(original)
        check(name + "_create", created.ok, {"kind": created.failure_kind, "stderr": created.stderr,
                                           "message": created.message})
        manifest = json.loads(created.outputs["manifest"])
        target = next(item for item in manifest["objects"] if "color" in item.get("editable", [])
                      and item.get("role") in {"series", "line"})
        override = {"gid": target["gid"], "prop": "color", "value": "#2a78d6"}
        replay = runner.run(dataclasses.replace(original, replay_overrides=True,
                                                overrides=[override],
                                                outputs={**outputs, "overrides": "overrides.json"}))
        check(name + "_override_replay", replay.ok,
              {"stderr": replay.stderr, "message": replay.message})
        accepted = json.loads(replay.outputs["overrides"])
        check(name + "_override_persisted", any(item == override for item in accepted), accepted)
        updated = json.loads(replay.outputs["manifest"])
        changed = next(item for item in updated["objects"] if item["gid"] == target["gid"])
        check(name + "_manifest_color", changed.get("color") == "#2a78d6", changed)
        check(name + "_export_signatures", b"<svg" in replay.outputs["svg"]
              and replay.outputs["png"].startswith(b"\x89PNG\r\n\x1a\n")
              and replay.outputs["pdf"].startswith(b"%PDF"))
        for logical, blob in replay.outputs.items():
            (destination / f"{name}-{outputs.get(logical, 'overrides.json')}").write_bytes(blob)
        check(name + "_svg_color", b"#2a78d6" in replay.outputs["svg"].lower())

    canary = Path(args.source_root).resolve() / "host-canary.txt"
    canary.write_text("engineering sandbox visibility canary; no secret")
    isolation = (
        "import os\n"
        "sock = __import__('socket').socket()\n"
        "code = sock.connect_ex(('127.0.0.1', 8030))\n"
        "assert code != 0, 'host service became reachable'\n"
        f"assert not os.path.exists({str(canary)!r}), 'host path became visible'\n"
        "print('NETWORK_AND_HOST_HOME_DENIED')\n" + scripts["matplotlib"])
    isolated = runner.run(SandboxJob(script=isolation, outputs=outputs))
    check("network_and_home_denied", isolated.ok and "NETWORK_AND_HOST_HOME_DENIED" in isolated.stdout,
          {"stdout": isolated.stdout, "stderr": isolated.stderr})

    chatty = runner.run(SandboxJob(script=(
        f"import os\nos.write(1, b'x' * {MAX_LOG_BYTES + 8192})\n" + scripts["matplotlib"]),
        outputs=outputs))
    check("bounded_stdout_refused", not chatty.ok and chatty.failure_kind == "output",
          {"kind": chatty.failure_kind, "stdout_length": len(chatty.stdout)})

    # One sleeping child and a sleeping parent verify cleanup across setsid;
    # there is no process-count, CPU, memory or fork-exhaustion stress test.
    sleeper = (
        "import sys\n"
        "child = __import__('subprocess').Popen([sys.executable, '-c', "
        "'import time; time.sleep(30)'], start_new_session=True)\n"
        "__import__('time').sleep(30)\n")
    timeout = runner.run(SandboxJob(script=sleeper, timeout_sec=8))
    check("timeout_terminates_unit", timeout.timed_out, {"kind": timeout.failure_kind,
                                                        "duration_ms": timeout.duration_ms})
    token = CancelToken()
    timer = threading.Timer(4, lambda: token.cancel("engineering_acceptance"))
    timer.start()
    cancelled = False
    started = time.monotonic()
    try:
        with bound_token(token):
            runner.run(SandboxJob(script=sleeper, timeout_sec=30))
    except ToolCancelled:
        cancelled = True
    finally:
        timer.cancel()
    check("request_cancellation_terminates_unit", cancelled and time.monotonic() - started < 15)
    unit_states = {}
    for unit in evidence["units"]:
        completed = subprocess.run(["/usr/bin/systemctl", "--user", "show", unit,
                                    "--property=LoadState", "--property=ActiveState"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   timeout=5, env=_host_env(), text=True)
        unit_states[unit] = {"returncode": completed.returncode, "state": completed.stdout.strip()}
    check("no_surviving_services", all(item["returncode"] in {0, 1}
                                     and ("LoadState=not-found" in item["state"]
                                          or "ActiveState=inactive" in item["state"]
                                          or "ActiveState=failed" in item["state"])
                                     for item in unit_states.values()), unit_states)
    evidence["status"] = "passed"
    (destination / "acceptance.json").write_text(json.dumps(evidence, indent=2))
    print(json.dumps(evidence))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
