#!/usr/bin/env python3
"""Record credential-free TCP, SSH banner, and HTTP health transport probes."""
from __future__ import annotations

import argparse
import hashlib
import json
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def endpoint_sha256(host: str, port: int) -> str:
    return sha256_text(f"{host}:{port}")


def safe_error(exc: BaseException) -> dict[str, str]:
    return {
        "error_type": type(exc).__name__,
        "error": str(exc)[:300],
    }


def tcp_connect(host: str, port: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return {
                "status": "PASS",
                "elapsed_ms": int((time.monotonic() - started) * 1000),
            }
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            **safe_error(exc),
        }


def ssh_banner(host: str, port: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            banner = sock.recv(512)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        valid = banner.startswith(b"SSH-")
        return {
            "status": "PASS" if valid else "BLOCKED",
            "elapsed_ms": elapsed_ms,
            "banner_received": bool(banner),
            "banner_valid": valid,
            "banner_sha256": sha256_text(banner.decode("utf-8", errors="replace"))
            if banner
            else None,
            "blockers": [] if valid else ["ssh_banner_missing_or_invalid"],
        }
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "banner_received": False,
            "banner_valid": False,
            "blockers": ["ssh_banner_transport_error"],
            **safe_error(exc),
        }


def http_health(host: str, port: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    request = (
        f"GET /health HTTP/1.1\r\nHost: {host}:{port}\r\n"
        "User-Agent: CitrusScholar-Audit/4.4.0\r\n"
        "Accept: application/json\r\nConnection: close\r\n\r\n"
    ).encode("ascii")
    response = bytearray()
    try:
        with socket.create_connection((host, port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(request)
            while len(response) < 64 * 1024:
                block = sock.recv(8192)
                if not block:
                    break
                response.extend(block)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        head, separator, body = bytes(response).partition(b"\r\n\r\n")
        status_line = head.split(b"\r\n", 1)[0].decode(
            "latin-1", errors="replace"
        )
        http_status = None
        if len(status_line.split()) >= 2:
            try:
                http_status = int(status_line.split()[1])
            except ValueError:
                pass
        parsed = None
        if separator:
            try:
                parsed = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                parsed = None
        healthy = http_status == 200 and (
            not isinstance(parsed, dict) or parsed.get("status") == "ok"
        )
        return {
            "status": "PASS" if healthy else "BLOCKED",
            "elapsed_ms": elapsed_ms,
            "http_status": http_status,
            "response_bytes": len(response),
            "response_sha256": sha256_text(
                bytes(response).decode("latin-1", errors="replace")
            )
            if response
            else None,
            "parsed_json": parsed,
            "blockers": [] if healthy else ["http_health_not_ok"],
        }
    except OSError as exc:
        return {
            "status": "BLOCKED",
            "elapsed_ms": int((time.monotonic() - started) * 1000),
            "http_status": None,
            "response_bytes": len(response),
            "response_sha256": None,
            "parsed_json": None,
            "blockers": ["http_health_transport_error"],
            **safe_error(exc),
        }


def build_report(
    host: str,
    *,
    ssh_port: int,
    http_port: int,
    timeout: float,
) -> dict[str, Any]:
    ssh_tcp = tcp_connect(host, ssh_port, timeout)
    ssh = ssh_banner(host, ssh_port, timeout)
    http_tcp = tcp_connect(host, http_port, timeout)
    health = http_health(host, http_port, timeout)
    services = {
        "ssh": {
            "endpoint_sha256": endpoint_sha256(host, ssh_port),
            "tcp": ssh_tcp,
            "banner": ssh,
        },
        "experiment_http": {
            "endpoint_sha256": endpoint_sha256(host, http_port),
            "tcp": http_tcp,
            "health": health,
        },
    }
    blockers: list[str] = []
    if ssh_tcp["status"] != "PASS":
        blockers.append("ssh_tcp_unreachable")
    blockers.extend(f"ssh:{item}" for item in ssh.get("blockers", []))
    if http_tcp["status"] != "PASS":
        blockers.append("experiment_http_tcp_unreachable")
    blockers.extend(f"experiment_http:{item}" for item in health.get("blockers", []))
    return {
        "schema_version": "1.0",
        "protocol_version": "4.4.0",
        "artifact": "STAGE1_TRANSPORT_PREFLIGHT",
        "generated_utc": utc_now(),
        "target_sha256": sha256_text(host),
        "target_recorded": False,
        "credentials_used": False,
        "authorization_headers_used": False,
        "services": services,
        "status": "PASS" if not blockers else "BLOCKED",
        "blockers": sorted(set(blockers)),
    }


def write_new_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", required=True)
    parser.add_argument("--ssh-port", type=int, required=True)
    parser.add_argument("--http-port", type=int, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=8.0)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser().resolve()
    if out.exists():
        raise SystemExit(f"output path already exists: {out}")
    report = build_report(
        args.host,
        ssh_port=args.ssh_port,
        http_port=args.http_port,
        timeout=args.timeout_seconds,
    )
    write_new_json(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
