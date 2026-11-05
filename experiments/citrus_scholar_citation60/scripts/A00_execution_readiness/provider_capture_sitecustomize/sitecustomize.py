"""Opt-in HTTPX shim for the isolated experiment process only.

It does not capture bodies. It adds non-secret request-context identifiers so
the loopback provider_capture proxy can bind provider calls to the app request.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit


def _origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def _ids() -> dict[str, str]:
    try:
        from src.core.tracing import (  # type: ignore
            get_client_request_id,
            get_job_id,
            get_request_id,
            get_session_id,
        )
    except Exception:
        return {}
    client_request_id = str(get_client_request_id() or "").strip()
    values = {
        "x-citrus-server-request-id": str(get_request_id() or "").strip(),
        "x-citrus-client-request-id": client_request_id,
        "x-citrus-session-id": str(get_session_id() or "").strip(),
        # B2 deliberately uses the immutable run ID as client_request_id.
        "x-citrus-run-id": client_request_id,
        "x-citrus-job-id": str(get_job_id() or "").strip(),
    }
    return {key: value for key, value in values.items() if value and value != "-"}


def install() -> None:
    proxy_value = os.environ.get("CITRUS_PROVIDER_CAPTURE_PROXY_ORIGIN", "").strip()
    if not proxy_value:
        return
    proxy_origin = _origin(proxy_value)
    try:
        import httpx
    except Exception:
        return
    if getattr(httpx.Client.send, "_citrus_experiment_shim", False):
        return
    original_sync = httpx.Client.send
    original_async = httpx.AsyncClient.send

    def add_headers(request: "httpx.Request") -> None:
        if _origin(str(request.url)) != proxy_origin:
            return
        for key, value in _ids().items():
            request.headers.setdefault(key, value)

    def sync_send(self, request, *args, **kwargs):
        add_headers(request)
        return original_sync(self, request, *args, **kwargs)

    async def async_send(self, request, *args, **kwargs):
        add_headers(request)
        return await original_async(self, request, *args, **kwargs)

    sync_send._citrus_experiment_shim = True  # type: ignore[attr-defined]
    httpx.Client.send = sync_send
    httpx.AsyncClient.send = async_send


install()
