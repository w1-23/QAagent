#!/usr/bin/env python3
"""Loopback recording proxy for the isolated experiment Agent only.

The production service is never modified. The isolated experiment instance
points its Agent provider base URL at this loopback proxy, while the companion
sitecustomize shim adds request-context IDs as non-secret headers. Raw provider
bodies and a parsed response envelope are written below the protected capture
root so B2 can bind one real provider response to one app request.

Authorization and Cookie values are forwarded in memory and are never written
to request archives, response archives, the index or logs.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import os
import re
import socket
import threading
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit


ID_HEADERS = {
    "run_id": "x-citrus-run-id",
    "client_request_id": "x-citrus-client-request-id",
    "server_request_id": "x-citrus-server-request-id",
    "session_id": "x-citrus-session-id",
    "job_id": "x-citrus-job-id",
}
SAFE_RESPONSE_HEADERS = {
    "content-type",
    "cache-control",
    "retry-after",
    "x-request-id",
    "request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
}
SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_identifier(value: str) -> str:
    value = str(value or "").strip()
    return value if SAFE_REQUEST_ID.fullmatch(value) else ""


def _write_once(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as handle:
        handle.write(data)


def _write_json_once(path: Path, payload: Any) -> None:
    data = (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    _write_once(path, data)


def _response_id_from_headers(headers: dict[str, str]) -> str:
    for key in ("x-request-id", "request-id", "x-dashscope-request-id"):
        value = str(headers.get(key) or "").strip()
        if value:
            return value
    return ""


def _content_from_choice(choice: dict[str, Any]) -> str:
    candidates: list[Any] = []
    message = choice.get("message")
    if isinstance(message, dict):
        candidates.append(message.get("content"))
    delta = choice.get("delta")
    if isinstance(delta, dict):
        candidates.append(delta.get("content"))
    candidates.append(choice.get("text"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            return candidate
        if isinstance(candidate, list):
            joined = "".join(
                str(part.get("text") or "")
                for part in candidate
                if isinstance(part, dict)
            )
            if joined:
                return joined
    return ""


def response_envelope(
    raw_body: bytes,
    content_type: str,
    response_headers: dict[str, str],
) -> tuple[dict[str, Any] | None, str, str]:
    """Return (envelope, stream_format, error)."""
    text = raw_body.decode("utf-8-sig", errors="replace")
    lower_type = content_type.lower()
    if "text/event-stream" not in lower_type and not text.lstrip().startswith(
        "data:"
    ):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None, "invalid", "response_not_json"
        if not isinstance(payload, dict):
            return None, "invalid", "response_not_object"
        choice = (payload.get("choices") or [{}])[0]
        if not isinstance(choice, dict):
            return None, "json", "choice_not_object"
        content = _content_from_choice(choice)
        envelope = dict(payload)
        envelope.setdefault("id", _response_id_from_headers(response_headers))
        envelope.setdefault(
            "model", str(payload.get("model") or response_headers.get("model") or "")
        )
        envelope["choices"] = [
            {
                "index": int(choice.get("index") or 0),
                "message": {"role": "assistant", "content": content},
                "finish_reason": choice.get("finish_reason"),
            }
        ]
        envelope["_capture"] = {
            "stream_format": "json",
            "raw_body_sha256": sha256_bytes(raw_body),
        }
        return envelope, "json", ""

    response_id = _response_id_from_headers(response_headers)
    model = ""
    content_parts: list[str] = []
    usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    event_count = 0
    data_event_count = 0
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("data:"):
            continue
        event_count += 1
        data_text = line[5:].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            event = json.loads(data_text)
        except json.JSONDecodeError:
            return None, "sse", f"sse_event_json_invalid:{event_count}"
        if not isinstance(event, dict):
            return None, "sse", f"sse_event_not_object:{event_count}"
        data_event_count += 1
        response_id = str(
            event.get("id") or event.get("request_id") or response_id
        )
        model = str(event.get("model") or model)
        if isinstance(event.get("usage"), dict):
            usage = event["usage"]
        choices = event.get("choices")
        if not isinstance(choices, list):
            continue
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            part = _content_from_choice(choice)
            if part:
                content_parts.append(part)
            if choice.get("finish_reason"):
                finish_reason = str(choice["finish_reason"])
    if not data_event_count:
        return None, "sse", "sse_no_data_events"
    content = "".join(content_parts)
    if not content:
        return None, "sse", "sse_content_empty"
    envelope = {
        "id": response_id,
        "object": "chat.completion",
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage or {},
        "_capture": {
            "stream_format": "sse",
            "raw_body_sha256": sha256_bytes(raw_body),
            "data_event_count": data_event_count,
        },
    }
    return envelope, "sse", ""


def _upstream_target(upstream_base: str, incoming_path: str) -> tuple[str, str, int]:
    parsed = urlsplit(upstream_base)
    if parsed.scheme == "https":
        connection_class = "https"
    elif parsed.scheme == "http" and parsed.hostname in {
        "127.0.0.1",
        "localhost",
        "::1",
    }:
        connection_class = "http"
    else:
        raise ValueError("upstream must use HTTPS unless it is loopback")
    if not parsed.hostname:
        raise ValueError("upstream hostname is missing")
    base_path = parsed.path.rstrip("/")
    suffix = incoming_path
    if base_path.endswith("/v1") and suffix.startswith("/v1/"):
        suffix = suffix[3:]
    target_path = base_path + "/" + suffix.lstrip("/")
    return connection_class, target_path, parsed.port or (
        443 if parsed.scheme == "https" else 80
    )


class CaptureStore:
    def __init__(self, root: Path, index_path: Path):
        self.root = root.resolve()
        self.index_path = index_path.resolve()
        if not self.root.is_dir():
            raise ValueError("capture root must already exist")
        if not self.index_path.is_relative_to(self.root):
            raise ValueError("capture index must be below the capture root")
        self.lock = threading.Lock()

    def append(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n"
        with self.lock:
            self.index_path.parent.mkdir(parents=True, exist_ok=True)
            with self.index_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(line)
                handle.flush()
                os.fsync(handle.fileno())


class CaptureServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(
        self,
        address: tuple[str, int],
        *,
        upstream_base: str,
        store: CaptureStore,
        timeout: float,
    ):
        super().__init__(address, CaptureHandler)
        self.upstream_base = upstream_base
        self.store = store
        self.upstream_timeout = timeout


class CaptureHandler(BaseHTTPRequestHandler):
    server: CaptureServer

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _forward(self) -> None:
        if self.path == "/__capture_health":
            self._send_json(200, {"status": "ok"})
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        request_body = self.rfile.read(length) if length else b""
        incoming_headers = {key.lower(): value for key, value in self.headers.items()}
        capture_id = uuid.uuid4().hex
        capture_dir = self.server.store.root / "provider_capture" / capture_id
        request_path = capture_dir / "request_body.json"
        raw_http_path = capture_dir / "response.raw"
        envelope_path = capture_dir / "response_envelope.json"
        _write_once(request_path, request_body)

        try:
            connection_class, target_path, port = _upstream_target(
                self.server.upstream_base,
                self.path,
            )
            upstream = urlsplit(self.server.upstream_base)
            if connection_class == "https":
                connection = http.client.HTTPSConnection(
                    upstream.hostname,
                    port,
                    timeout=self.server.upstream_timeout,
                )
            else:
                connection = http.client.HTTPConnection(
                    upstream.hostname,
                    port,
                    timeout=self.server.upstream_timeout,
                )
            forwarded_headers = {
                key: value
                for key, value in self.headers.items()
                if key.lower()
                not in {
                    "host",
                    "content-length",
                    "connection",
                    "accept-encoding",
                    "transfer-encoding",
                }
            }
            forwarded_headers["Accept-Encoding"] = "identity"
            connection.request(
                self.command,
                target_path,
                body=request_body if self.command in {"POST", "PUT", "PATCH"} else None,
                headers=forwarded_headers,
            )
            upstream_response = connection.getresponse()
        except Exception:
            self._send_json(502, {"error": "capture_proxy_upstream_failure"})
            return

        response_headers = {
            key.lower(): value for key, value in upstream_response.getheaders()
        }
        self.send_response(upstream_response.status)
        for key, value in upstream_response.getheaders():
            if key.lower() in SAFE_RESPONSE_HEADERS:
                self.send_header(key, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Connection", "close")
        self.end_headers()
        captured_chunks: list[bytes] = []
        try:
            while True:
                chunk = upstream_response.read1(64 * 1024)
                if not chunk:
                    break
                captured_chunks.append(chunk)
                self.wfile.write(f"{len(chunk):X}\r\n".encode("ascii"))
                self.wfile.write(chunk)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        finally:
            connection.close()
        raw_body = b"".join(captured_chunks)
        _write_once(raw_http_path, raw_body)
        envelope, stream_format, parse_error = response_envelope(
            raw_body,
            response_headers.get("content-type", ""),
            response_headers,
        )
        if envelope is not None:
            _write_json_once(envelope_path, envelope)
        identifiers = {
            name: _safe_identifier(incoming_headers.get(header, ""))
            for name, header in ID_HEADERS.items()
        }
        try:
            request_payload = json.loads(request_body.decode("utf-8-sig"))
        except Exception:
            request_payload = {}
        requested_model = (
            str(request_payload.get("model") or "")
            if isinstance(request_payload, dict)
            else ""
        )
        provider_response_id = ""
        response_model = ""
        if isinstance(envelope, dict):
            provider_response_id = str(
                envelope.get("id") or _response_id_from_headers(response_headers)
            )
            response_model = str(envelope.get("model") or "")
        record = {
            "capture_id": capture_id,
            "completed_utc": utc_now(),
            **identifiers,
            "http_status": int(upstream_response.status),
            "provider_response_id": provider_response_id,
            "requested_model": requested_model,
            "response_model": response_model,
            "request_body_path": request_path.relative_to(
                self.server.store.root
            ).as_posix(),
            "request_sha256": sha256_bytes(request_body),
            "raw_response_path": envelope_path.relative_to(
                self.server.store.root
            ).as_posix()
            if envelope is not None
            else "",
            "raw_response_sha256": sha256_file(envelope_path)
            if envelope is not None
            else "",
            "raw_http_path": raw_http_path.relative_to(
                self.server.store.root
            ).as_posix(),
            "raw_http_sha256": sha256_bytes(raw_body),
            "stream_format": stream_format,
            "parse_error": parse_error,
            "status": (
                "CAPTURED"
                if envelope is not None
                and all(identifiers.values())
                and provider_response_id
                else "CAPTURED_INCOMPLETE"
            ),
        }
        self.server.store.append(record)

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()


def _parse_address(bind: str, port: int) -> tuple[str, int]:
    if bind not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("capture proxy must bind to loopback")
    return bind, int(port)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--bind",
        default=os.environ.get("CITRUS_PROVIDER_CAPTURE_BIND", "127.0.0.1"),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("CITRUS_PROVIDER_CAPTURE_PORT", "8013")),
    )
    parser.add_argument(
        "--upstream",
        default=os.environ.get("CITRUS_PROVIDER_CAPTURE_UPSTREAM", ""),
    )
    parser.add_argument(
        "--capture-root",
        default=os.environ.get("CITRUS_EXPERIMENT_CAPTURE_ROOT", ""),
    )
    parser.add_argument(
        "--index",
        default=os.environ.get(
            "CITRUS_EXPERIMENT_CAPTURE_INDEX",
            "",
        ),
    )
    parser.add_argument("--timeout", type=float, default=600)
    args = parser.parse_args()
    if not args.upstream:
        parser.error("--upstream or CITRUS_PROVIDER_CAPTURE_UPSTREAM is required")
    if not args.capture_root:
        parser.error("--capture-root or CITRUS_EXPERIMENT_CAPTURE_ROOT is required")
    capture_root = Path(args.capture_root).expanduser().resolve()
    index_path = (
        Path(args.index).expanduser().resolve()
        if args.index
        else capture_root / "provider_capture" / "index.jsonl"
    )
    store = CaptureStore(capture_root, index_path)
    server = CaptureServer(
        _parse_address(args.bind, args.port),
        upstream_base=args.upstream,
        store=store,
        timeout=args.timeout,
    )
    print(
        json.dumps(
            {
                "status": "READY",
                "bind": args.bind,
                "port": args.port,
                "capture_index": str(index_path),
            }
        ),
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
