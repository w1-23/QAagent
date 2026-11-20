"""HTTP boundary for a same-host, authenticated IPv4 TLS reverse proxy."""
from __future__ import annotations

from dataclasses import dataclass, field
from hmac import compare_digest
from ipaddress import IPv4Address
import os
from pathlib import Path
import re
import stat

from fastapi import Request
from fastapi.responses import JSONResponse

LOOPBACK = '127.0.0.1'
READ_METHODS = frozenset({'GET', 'HEAD', 'OPTIONS'})
LOCAL_HEALTH_PATHS = frozenset({'/health', '/ready'})


def _denied(status: int, detail: str) -> JSONResponse:
    return JSONResponse({'detail': detail}, status_code=status, headers={'Cache-Control': 'no-store'})


@dataclass(frozen=True)
class LanIngress:
    authority: str
    health_authority: str
    proxy_token: bytes = field(repr=False)

    def check(self, request: Request) -> JSONResponse | None:
        hosts = request.headers.getlist('host')
        peer = request.client.host if request.client else None
        if (peer == LOOPBACK and hosts == [self.health_authority]
                and request.method in {'GET', 'HEAD'} and request.scope['path'] in LOCAL_HEALTH_PATHS):
            return None
        if hosts != [self.authority]:
            return _denied(400, '请求地址无效。')
        tokens = request.headers.getlist('x-citrus-proxy-auth')
        if (peer != LOOPBACK or len(tokens) != 1
                or not compare_digest(tokens[0].encode('latin-1'), self.proxy_token)):
            return _denied(403, '请通过局域网 HTTPS 入口访问。')
        addresses = request.headers.getlist('x-citrus-client-ip')
        try:
            client = IPv4Address(addresses[0]) if len(addresses) == 1 else None
        except ValueError:
            client = None
        if client is None or client.is_unspecified or client.is_multicast or client.is_reserved:
            return _denied(403, '无法确认客户端地址。')
        origins = request.headers.getlist('origin')
        if request.method not in READ_METHODS and origins != [f'https://{self.authority}']:
            return _denied(403, '跨站请求已拒绝。')
        request.state.lan_client_ip = str(client)
        request.scope['scheme'] = 'https'
        return None


def load_lan_ingress() -> LanIngress | None:
    authority = os.getenv('CITRUS_LAN_AUTHORITY')
    token_path = os.getenv('CITRUS_LAN_TOKEN_FILE')
    local_port = os.getenv('CITRUS_LAN_UPSTREAM_PORT')
    if authority is None and token_path is None and local_port is None:
        return None
    try:
        host, port = (authority or '').split(':')
        ip = IPv4Address(host)
        if (ip.is_unspecified or ip.is_multicast or ip.is_reserved or ip.is_link_local
                or not port.isascii() or not port.isdecimal() or not 1024 <= int(port) <= 65535
                or str(int(port)) != port
                or not local_port or not local_port.isascii() or not local_port.isdecimal()
                or not 1024 <= int(local_port) <= 65535 or not token_path
                or not Path(token_path).is_absolute()):
            raise ValueError
    except ValueError:
        raise RuntimeError('Invalid LAN ingress configuration') from None

    try:
        flags = os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW
        with os.fdopen(os.open(token_path, flags), 'rb') as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077
                    or metadata.st_uid != os.geteuid() or metadata.st_nlink != 1):
                raise ValueError
            token = stream.read(67).removesuffix(b'\n')
        if not re.fullmatch(rb'[0-9a-f]{64}', token):
            raise ValueError
    except (OSError, ValueError):
        raise RuntimeError('Invalid LAN proxy credential file') from None
    return LanIngress(f'{ip}:{port}', f'{LOOPBACK}:{int(local_port)}', token)
