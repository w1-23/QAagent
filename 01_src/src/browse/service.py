"""Host-owned lifetime and IPC for networkless Chromium."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

from src.code_execution.egress_policy import normalize_host
from src.core.cancel import raise_if_cancelled
from src.figures.apptainer import _CGROUP_GUARD, _host_env
from .relay import BrowseError, Relay


@dataclass(frozen=True)
class Config:
    image: Path
    packages: Path
    hosts: frozenset[str]
    host_python: str


@lru_cache(maxsize=1)
def config(path: str) -> Config | None:
    if sys.platform != 'linux' or not path:
        return None
    try:
        with Path(path).open('rb') as stream:
            settings = json.loads(stream.read(8193))
        image = Path(settings['image'])
        packages = Path(settings['packages'])
        host_python = Path(settings.get('host_python', '/usr/bin/python3'))
        for item in (image, packages, host_python):
            if not item.is_absolute() or any(char in str(item) for char in ':,\n\r'):
                return None
        if not image.is_file() or not packages.is_dir() or not host_python.is_file():
            return None
        if not Path('/usr/bin/apptainer').is_file() or not Path('/usr/bin/systemd-run').is_file():
            return None
        hosts = frozenset(normalize_host(host) for host in settings['hosts'])
        if not 1 <= len(hosts) <= 40:
            return None
        with image.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != settings['image_sha256']:
                return None
        return Config(image, packages, hosts, str(host_python))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def enabled() -> bool:
    return config(os.getenv('CITRUS_BROWSE_CONFIG', '')) is not None


class Renderer:
    def __init__(self, settings: Config):
        self.settings = settings
        self.relay = Relay(settings.hosts)
        self.unit = 'citrus-browse-' + uuid.uuid4().hex
        self.process = None
        self.engine = ''
        self.lock = asyncio.Lock()
        self.actions = 0
        self.startup: asyncio.Task | None = None
        self.closing: asyncio.Task | None = None

    async def start(self):
        memory = 1200 * 1024 * 1024
        tasks = 192
        env = _host_env()
        worker = Path(__file__).resolve().with_name('sandbox.py')
        command = [
            '/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
            '--unit', self.unit, '-p', 'Type=exec', '-p', 'RuntimeMaxSec=150s',
            '-p', 'KillMode=control-group', '-p', 'TimeoutStopSec=2s', '-p', 'MemorySwapMax=0',
            '-p', f'MemoryMax={memory}', '-p', f'TasksMax={tasks}', '-p', 'CPUQuota=100%',
            '-p', 'NoNewPrivileges=yes', '-p', 'UMask=0077', '-p', 'LimitCORE=0', '--',
            '/usr/bin/env', '-i', *(f'{key}={value}' for key, value in env.items()),
            self.settings.host_python, '-I', '-c', _CGROUP_GUARD, str(memory), str(tasks), '1',
            '/usr/bin/apptainer', 'exec', '--containall', '--cleanenv', '--no-home',
            '--no-mount', 'hostfs,cwd,home,bind-paths,sys', '--net', '--network', 'none',
            '--pid', '--ipc', '--no-privs', '--drop-caps', 'ALL', '--cwd', '/tmp',
            '--bind', f'{self.settings.packages}:/browser-python:ro',
            '--bind', f'{worker}:/browse.py:ro', '--env', 'PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
            str(self.settings.image), 'python', '-I', '/browse.py', os.readlink('/proc/self/ns/net'),
        ]
        self.process = await asyncio.create_subprocess_exec(*command, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        message = await asyncio.wait_for(self.receive(), 15)
        if message.get('kind') != 'ready' or not re.fullmatch(r'[0-9.]{4,40}', message.get('engine', '')):
            raise BrowseError('browse_renderer_unready')
        self.engine = message['engine']

    async def receive(self) -> dict:
        try:
            frame = await self.process.stdout.readline()
            if not frame.endswith(b'\n') or len(frame) > 65536:
                raise BrowseError('browse_renderer_disconnected')
            message = json.loads(frame)
            if not isinstance(message, dict):
                raise ValueError
            return message
        except ValueError as exc:
            raise BrowseError('browse_bad_observation') from exc

    async def send(self, message: dict):
        try:
            self.process.stdin.write(json.dumps(message, ensure_ascii=True).encode('ascii') + b'\n')
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise BrowseError('browse_renderer_disconnected') from exc

    async def exchange(self, command: dict):
        async with self.lock, asyncio.timeout(22):
            if self.actions >= 28:
                raise BrowseError('browse_action_limit')
            self.actions += 1
            await self.send(command)
            while True:
                raise_if_cancelled('browse')
                result = await self.receive()
                if result.get('kind') == 'result':
                    return result
                if result.get('kind') != 'request':
                    raise BrowseError('browse_renderer_protocol')
                try:
                    response = await asyncio.to_thread(self.relay.fetch, result.get('url'), result.get('method'))
                except BrowseError:
                    await self.send({'ok': False})
                    continue
                raise_if_cancelled('browse')
                await self.send({'ok': True, 'status': response.status, 'headers': response.headers,
                                 'body': base64.b64encode(response.body).decode('ascii')})

    async def stop(self):
        if self.process is None:
            return
        if self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 2)
            except TimeoutError:
                stopper = await asyncio.create_subprocess_exec('/usr/bin/systemctl', '--user', 'stop', self.unit,
                    env=_host_env(), stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
                try:
                    await asyncio.wait_for(stopper.wait(), 5)
                except TimeoutError:
                    stopper.kill()
                    await stopper.wait()
                if self.process.returncode is None:
                    await asyncio.wait_for(self.process.wait(), 5)


Identity = tuple[int, str, str]
_MAX_SESSIONS = 2
_MAX_WAITERS = 4
_WAIT_SECONDS = 20
_SESSIONS: dict[Identity, Renderer] = {}
_WAITERS: dict[Identity, asyncio.Event] = {}


def _notify_waiters():
    for event in _WAITERS.values():
        event.set()


def _discard(identity: Identity, renderer: Renderer):
    if _SESSIONS.get(identity) is renderer:
        del _SESSIONS[identity]
        _notify_waiters()


async def _watch_process(identity: Identity, renderer: Renderer):
    try:
        await renderer.startup
    except Exception:
        return  # Startup failure is closed by the caller.
    await renderer.process.wait()
    _discard(identity, renderer)


async def _stop_renderer(identity: Identity, renderer: Renderer):
    with suppress(Exception):
        await renderer.startup
    try:
        await renderer.stop()
    except (OSError, TimeoutError):
        # Keep the slot until the controller unit has actually exited.
        if renderer.process is None:
            raise
        await renderer.process.wait()
    _discard(identity, renderer)


async def _claim(identity: Identity, settings: Config) -> Renderer:
    if any(key[0] == identity[0] for key in (*_SESSIONS, *_WAITERS)):
        raise BrowseError('browse_account_busy')
    if len(_WAITERS) >= _MAX_WAITERS:
        raise BrowseError('browse_queue_full')
    event = asyncio.Event()
    _WAITERS[identity] = event
    try:
        async with asyncio.timeout(_WAIT_SECONDS):
            while True:
                raise_if_cancelled('browse_queue')
                if len(_SESSIONS) < _MAX_SESSIONS and next(iter(_WAITERS)) == identity:
                    renderer = Renderer(settings)
                    _SESSIONS[identity] = renderer
                    renderer.startup = asyncio.create_task(renderer.start())
                    asyncio.create_task(_watch_process(identity, renderer))
                    return renderer
                event.clear()
                # Check cooperative cancellation even if every slot stays occupied.
                with suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), 0.5)
    except TimeoutError as exc:
        raise BrowseError('browse_queue_timeout') from exc
    finally:
        del _WAITERS[identity]
        _notify_waiters()


async def renderer_for(identity: Identity, *, create: bool = True) -> Renderer:
    renderer = _SESSIONS.get(identity)
    if renderer is None:
        if not create:
            raise BrowseError('browse_session_expired')
        settings = config(os.getenv('CITRUS_BROWSE_CONFIG', ''))
        if not settings:
            raise BrowseError('browse_unavailable')
        renderer = await _claim(identity, settings)
    try:
        await asyncio.shield(renderer.startup)
        if renderer.closing or renderer.process.returncode is not None:
            raise BrowseError('browse_session_expired')
        return renderer
    except BaseException:
        if _SESSIONS.get(identity) is renderer:
            await release(*identity)
        raise


async def release(owner: int, session_id: str, job_id: str):
    identity = (owner, session_id, job_id)
    renderer = _SESSIONS.get(identity)
    if renderer is not None:
        if renderer.closing is None:
            renderer.closing = asyncio.create_task(_stop_renderer(identity, renderer))
        await asyncio.shield(renderer.closing)
