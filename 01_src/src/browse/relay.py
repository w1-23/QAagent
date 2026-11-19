"""Trusted HTTPS transport. The renderer has no network sockets to the host or internet."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
import json
from pathlib import Path
import socket
import ssl
import sys
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from src.code_execution.egress_policy import Destination, PolicyDenied, normalize_https_url, public_ip
from src.core.cancel import raise_if_cancelled

_REDIRECT = {301, 302, 303, 307, 308}
_RESPONSE_HEADERS = {
    'content-type', 'content-disposition', 'content-language', 'location',
    'content-security-policy', 'x-content-type-options', 'referrer-policy',
    'access-control-allow-origin', 'cross-origin-resource-policy',
    'cross-origin-embedder-policy', 'cross-origin-opener-policy', 'x-frame-options',
}
_REQUEST_LIMIT = 32768
_RECEIPT_LIMIT = len(_RESPONSE_HEADERS) * (8192 * 6 + 64) + 1024  # JSON escaping
_CANCEL_POLL_SECONDS = 0.05
_WORKER_ENTRY = ('import sys; sys.path.insert(0, sys.argv[1]); '
                 'from src.browse.relay import _worker; _worker()')
_SOURCE_ROOT = str(Path(__file__).resolve().parents[2])


class BrowseError(RuntimeError):
    """A bounded, model-visible browse reason without upstream error text."""


@dataclass(frozen=True)
class Exchange:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes


class _PinnedHTTPS(HTTPSConnection):
    def __init__(self, host: str, peer: str, *, timeout: float):
        super().__init__(host, timeout=timeout, context=ssl.create_default_context())
        self._create_connection = self._pin_connection
        self.peer = peer

    def _pin_connection(self, _address, timeout, source_address):
        family = socket.AF_INET6 if ':' in self.peer else socket.AF_INET
        connection = socket.socket(family, socket.SOCK_STREAM)
        try:
            connection.settimeout(timeout)
            connection.connect((self.peer, 443))
            if public_ip(connection.getpeername()[0]) != self.peer:
                raise BrowseError('browse_peer_mismatch')
            return connection
        except BaseException:
            connection.close()
            raise


def _time_left(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BrowseError('browse_network_timeout')
    return remaining


def _exchange(destination: Destination, method: str, limit: int, deadline: float,
              body: bytearray) -> tuple[int, dict[str, str]]:
    """Perform one blocking exchange in the killable child, never in the host."""
    _time_left(deadline)
    records = socket.getaddrinfo(destination.host, 443, type=socket.SOCK_STREAM)
    peers = {public_ip(record[4][0]) for record in records}
    if not 1 <= len(peers) <= 24:
        raise BrowseError('browse_dns_denied')
    # Prefer IPv4. The service also validates every returned IPv6 answer.
    peer = min(peers, key=lambda address: (':' in address, address))
    connection = _PinnedHTTPS(destination.host, peer, timeout=_time_left(deadline))
    try:
        parts = urlsplit(destination.url)
        path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
        connection.request(method, path, headers={
            'Accept-Encoding': 'identity', 'User-Agent': 'CitrusScholarResearchBrowse/1',
            'Connection': 'close',
        })
        _time_left(deadline)
        with connection.getresponse() as response:
            _time_left(deadline)
            headers = {key.lower(): value for key, value in response.getheaders()
                       if key.lower() in _RESPONSE_HEADERS and len(value) <= 8192}
            if response.status in _REDIRECT:
                if 'location' not in headers:
                    raise BrowseError('browse_redirect_missing')
                return response.status, headers
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise BrowseError('browse_encoding_unsupported')
            if method == 'HEAD':
                return response.status, headers
            declared_size = response.length
            if declared_size is not None and declared_size > limit:
                raise BrowseError('browse_response_limit')
            while True:
                _time_left(deadline)
                chunk = response.read1(min(65536, limit - len(body) + 1))
                body.extend(chunk)
                if len(body) > limit:
                    raise BrowseError('browse_response_limit')
                if not chunk:
                    break
            if declared_size is not None and len(body) != declared_size:
                raise BrowseError('browse_incomplete_response')
            return response.status, headers
    finally:
        connection.close()


def _worker():
    # Only the host writes this pipe. Neither the URL nor the body is a command-line argument.
    url, method, limit, deadline = json.loads(sys.stdin.buffer.read(_REQUEST_LIMIT))
    body = bytearray()
    status, headers, error = 0, {}, ''
    try:
        status, headers = _exchange(normalize_https_url(url), method, limit, deadline, body)
        _time_left(deadline)
    except BrowseError as exc:
        error = str(exc)
    except PolicyDenied:
        error = 'browse_dns_denied'
    except TimeoutError:
        error = 'browse_network_timeout'
    except (OSError, HTTPException, UnicodeError):
        error = 'browse_network_error'
    # Even clean errors carry the actual body charge, but never the partial body.
    metadata = [0, {}, len(body), error] if error else [status, headers, len(body), '']
    sys.stdout.buffer.write(json.dumps(metadata, ensure_ascii=True).encode('ascii') + b'\n')
    if not error:
        sys.stdout.buffer.write(body)


async def _spawn_worker():
    return await asyncio.create_subprocess_exec(
        sys.executable, '-I', '-S', '-B', '-c', _WORKER_ENTRY, _SOURCE_ROOT,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL, limit=_RECEIPT_LIMIT,
    )


async def _read_exchange(process, request: bytes, destination: Destination, limit: int):
    try:
        process.stdin.write(request)
        await process.stdin.drain()
        process.stdin.close()
        line = await process.stdout.readline()
        if not line.endswith(b'\n') or len(line) > _RECEIPT_LIMIT:
            raise ValueError
        status, headers, consumed, error = json.loads(line)
        if (type(consumed) is not int or not 0 <= consumed <= limit + 1
                or type(error) is not str or len(error) > 64 or not isinstance(headers, dict)
                or any(key not in _RESPONSE_HEADERS or not isinstance(value, str) or len(value) > 8192
                       for key, value in headers.items())):
            raise ValueError
        if error:
            if status != 0 or headers or not error.startswith('browse_'):
                raise ValueError
            exchange = None
        else:
            if type(status) is not int or not 100 <= status <= 999 or consumed > limit:
                raise ValueError
            body = await process.stdout.readexactly(consumed)
            exchange = Exchange(destination.url, status, headers, body)
        if await process.stdout.read(1) or await process.wait() != 0:
            raise ValueError
        return exchange, consumed, error
    except (OSError, ValueError, TypeError, asyncio.IncompleteReadError) as exc:
        raise BrowseError('browse_network_error') from exc


async def _reap_worker(spawn: asyncio.Task, reader: asyncio.Task | None):
    try:
        process = await spawn
    except OSError:
        return  # No worker could be started.
    if process.returncode is None:
        with suppress(ProcessLookupError):
            process.kill()
    process.stdin.close()
    if reader is not None:
        with suppress(BrowseError):
            await reader
    # Drain a paused pipe too: asyncio's process wait can depend on pipe EOF.
    with suppress(OSError):
        while await process.stdout.read(65536):
            pass
    await process.wait()


async def _finish_worker(spawn: asyncio.Task, reader: asyncio.Task | None):
    cleanup = asyncio.create_task(_reap_worker(spawn, reader))
    cancelled = False
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            # A second cancellation must not release the held slot or lose the child handle.
            cancelled = True
    cleanup.result()
    if cancelled:
        raise asyncio.CancelledError


class Relay:
    def __init__(self, hosts: frozenset[str]):
        self.hosts = hosts
        self.remaining_bytes = 40 * 1024 * 1024
        self.remaining_requests = 100
        self.deadline = time.monotonic() + 150
        self._lock = asyncio.Lock()
        self._closed = False

    def authorize(self, url: str):
        try:
            if not isinstance(url, str) or len(url) > 4096:
                raise ValueError
            parts = urlsplit(url)
            destination = normalize_https_url(urlunsplit(parts._replace(fragment='')))
            if destination.host not in self.hosts:
                raise BrowseError('browse_host_not_allowed')
            return destination
        except (ValueError, UnicodeError) as exc:
            raise BrowseError('browse_url_denied') from exc

    def _check_active(self):
        raise_if_cancelled('browse_network')
        if self._closed:
            raise BrowseError('browse_session_expired')

    async def close(self):
        self._closed = True
        async with self._lock:
            pass  # Wait for the in-flight child to be killed and reaped before releasing the slot.

    async def fetch(self, url: str, method: str, *, limit: int = 4 * 1024 * 1024,
                    max_seconds: float = 8) -> Exchange:
        deadline = min(self.deadline, time.monotonic() + max_seconds)
        return await self._fetch_before(url, method, limit, deadline)

    async def _fetch_before(self, url: str, method: str, limit: int, deadline: float) -> Exchange:
        self._check_active()
        destination = self.authorize(url)
        if method not in ('GET', 'HEAD'):
            raise BrowseError('browse_method_denied')
        if self._lock.locked():
            raise BrowseError('browse_network_busy')
        async with self._lock:
            limit = min(self.remaining_bytes, limit)
            if deadline <= time.monotonic() or not self.remaining_requests or limit <= 0:
                raise BrowseError('browse_network_budget')
            self.remaining_requests -= 1
            # Reserve the entire allowance until a trusted terminal receipt is available.
            # A killed child may already have consumed bytes not reported over IPC.
            reserved = min(self.remaining_bytes, limit + 1)  # Include the overflow probe.
            self.remaining_bytes -= reserved
            exchange, consumed, error = await self._fetch(destination, method, limit, deadline)
            self.remaining_bytes += max(0, reserved - consumed)
            if error:
                raise BrowseError(error)
            if exchange.status in _REDIRECT:
                self.authorize(urljoin(destination.url, exchange.headers['location']))
            self._check_active()
            _time_left(deadline)
            return exchange

    async def _fetch(self, destination: Destination, method: str, limit: int, deadline: float):
        request = json.dumps([destination.url, method, limit, deadline], ensure_ascii=False).encode('utf-8')
        _time_left(deadline)
        spawn = asyncio.create_task(_spawn_worker())
        reader = None
        finished = False
        try:
            async with asyncio.timeout(_time_left(deadline)):
                process = await self._await_worker(spawn, deadline)
                reader = asyncio.create_task(_read_exchange(process, request, destination, limit))
                receipt = await self._await_worker(reader, deadline)
                finished = True  # A terminal receipt already entailed EOF and process exit.
                return receipt
        except TimeoutError as exc:
            raise BrowseError('browse_network_timeout') from exc
        except OSError as exc:
            raise BrowseError('browse_network_error') from exc
        finally:
            if not finished:
                await _finish_worker(spawn, reader)

    async def _await_worker(self, task: asyncio.Task, deadline: float):
        # Wait without cancelling the spawn (which owns the PID) or the pipe reader
        # (which can be needed to drain subprocess pipes after a kill).
        while not task.done():
            self._check_active()
            await asyncio.wait({task}, timeout=_CANCEL_POLL_SECONDS)
        self._check_active()
        _time_left(deadline)
        return task.result()

    async def download(self, url: str) -> tuple[Exchange, list[str]]:
        redirects = []
        deadline = min(self.deadline, time.monotonic() + 15)
        for hop in range(6):
            if hop:
                _time_left(deadline)
            exchange = await self._fetch_before(url, 'GET', 8 * 1024 * 1024, deadline)
            if exchange.status not in _REDIRECT:
                if exchange.status != 200 or not exchange.body:
                    raise BrowseError('browse_download_unavailable')
                return exchange, redirects
            redirects.append(exchange.url)
            url = urljoin(exchange.url, exchange.headers['location'])
        raise BrowseError('browse_redirect_limit')
