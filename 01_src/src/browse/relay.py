"""Trusted HTTPS transport. The renderer has no network sockets to the host or internet."""
from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException, HTTPSConnection
import socket
import ssl
import time
from urllib.parse import urljoin, urlsplit, urlunsplit

from src.code_execution.egress_policy import PolicyDenied, normalize_https_url, public_ip

_REDIRECT = {301, 302, 303, 307, 308}
_RESPONSE_HEADERS = {
    'content-type', 'content-disposition', 'content-language', 'location',
    'content-security-policy', 'x-content-type-options', 'referrer-policy',
    'access-control-allow-origin', 'cross-origin-resource-policy',
    'cross-origin-embedder-policy', 'cross-origin-opener-policy', 'x-frame-options',
}


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


class Relay:
    def __init__(self, hosts: frozenset[str]):
        self.hosts = hosts
        self.remaining_bytes = 40 * 1024 * 1024
        self.remaining_requests = 100
        self.deadline = time.monotonic() + 150

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

    def fetch(self, url: str, method: str, *, limit: int = 4 * 1024 * 1024, max_seconds: int = 8) -> Exchange:
        destination = self.authorize(url)
        if method not in {'GET', 'HEAD'}:
            raise BrowseError('browse_method_denied')
        timeout = min(self.deadline - time.monotonic(), max_seconds)
        limit = min(self.remaining_bytes, limit)
        if timeout <= 0 or not self.remaining_requests or limit <= 0:
            raise BrowseError('browse_network_budget')
        self.remaining_requests -= 1
        connection = None
        try:
            records = socket.getaddrinfo(destination.host, 443, type=socket.SOCK_STREAM)
            peers = {public_ip(record[4][0]) for record in records}
            if not 1 <= len(peers) <= 24:
                raise BrowseError('browse_dns_denied')
            # Prefer IPv4. The service also validates every returned IPv6 answer.
            peer = min(peers, key=lambda address: (':' in address, address))
            connection = _PinnedHTTPS(destination.host, peer, timeout=timeout)
            parts = urlsplit(destination.url)
            path = urlunsplit(('', '', parts.path or '/', parts.query, ''))
            connection.request(method, path, headers={
                'Accept-Encoding': 'identity', 'User-Agent': 'CitrusScholarResearchBrowse/1',
                'Connection': 'close',
            })
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()
                       if key.lower() in _RESPONSE_HEADERS and len(value) <= 8192}
            if response.status in _REDIRECT:
                if 'location' not in headers:
                    raise BrowseError('browse_redirect_missing')
                self.authorize(urljoin(destination.url, headers['location']))
                return Exchange(destination.url, response.status, headers, b'')
            if response.getheader('Content-Encoding', 'identity').lower() != 'identity':
                raise BrowseError('browse_encoding_unsupported')
            if method == 'HEAD':
                return Exchange(destination.url, response.status, headers, b'')
            if response.length is not None and response.length > limit:
                raise BrowseError('browse_response_limit')
            deadline = min(self.deadline, time.monotonic() + timeout)
            body = bytearray()
            declared_size = response.length
            while True:
                if time.monotonic() >= deadline:
                    raise BrowseError('browse_network_timeout')
                chunk = response.read1(min(65536, limit - len(body) + 1))
                if not chunk:
                    break
                self.remaining_bytes = max(0, self.remaining_bytes - len(chunk))
                if len(body) + len(chunk) > limit:
                    raise BrowseError('browse_response_limit')
                body.extend(chunk)
            if declared_size is not None and len(body) != declared_size:
                raise BrowseError('browse_incomplete_response')
            return Exchange(destination.url, response.status, headers, bytes(body))
        except PolicyDenied as exc:
            raise BrowseError('browse_dns_denied') from exc
        except (OSError, HTTPException, UnicodeError) as exc:
            raise BrowseError('browse_network_error') from exc
        finally:
            if connection is not None:
                connection.close()

    def download(self, url: str) -> tuple[Exchange, list[str]]:
        redirects = []
        for _ in range(6):
            exchange = self.fetch(url, 'GET', limit=8 * 1024 * 1024, max_seconds=15)
            if exchange.status not in _REDIRECT:
                if exchange.status != 200 or not exchange.body:
                    raise BrowseError('browse_download_unavailable')
                return exchange, redirects
            redirects.append(exchange.url)
            url = urljoin(exchange.url, exchange.headers['location'])
        raise BrowseError('browse_redirect_limit')
