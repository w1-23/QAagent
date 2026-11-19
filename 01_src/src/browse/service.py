"""Host-owned lifetime and IPC for networkless Chromium."""
from __future__ import annotations

import asyncio
import base64
from contextlib import suppress
from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import uuid

from src.code_execution.egress_policy import normalize_host
from src.core.cancel import raise_if_cancelled
from src.figures.apptainer import _CGROUP_GUARD, _host_env
from .integrity import Bundle, IntegrityError, load_bundle, trusted_source_file, verify_bundle, verify_host_python
from .relay import BrowseError, Relay


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Config:
    image: Path
    packages: Path
    hosts: frozenset[str]
    host_python: str
    bundle: Bundle | None = None
    mounted: bool = False


@lru_cache(maxsize=1)
def config(path: str, expected_sha256: str | None = None, *, mounted=False) -> Config | None:
    if not path:
        return None
    try:
        if sys.platform != 'linux':
            raise IntegrityError('Browse requires Linux')
        if not Path('/usr/bin/apptainer').is_file() or not Path('/usr/bin/systemd-run').is_file():
            raise IntegrityError('Browse requires /usr/bin/apptainer and /usr/bin/systemd-run')
        bundle = load_bundle(path, expected_sha256, mounted=mounted)
        hosts = frozenset(normalize_host(host) for host in bundle.hosts)
        verify_bundle(bundle, mounted=mounted)
        return Config(bundle.image, bundle.packages, hosts, str(bundle.host_python), bundle, mounted)
    except (OSError, ValueError) as exc:
        _LOG.error('Browse disabled: %s', exc)
        return None


def _configured() -> Config | None:
    return config(os.getenv('CITRUS_BROWSE_CONFIG', ''), os.getenv('CITRUS_BROWSE_CONFIG_SHA256'),
                  mounted=os.getenv('CITRUS_BROWSE_APPTAINER') == '1')


def enabled() -> bool:
    return _configured() is not None


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
        bundle = self.settings.bundle
        worker = Path(__file__).absolute().with_name('sandbox.py')
        verifier = worker.with_name('integrity.py')
        try:
            if bundle is None:
                raise IntegrityError('Browse bundle pins are missing')
            # The first host interpreter must be trusted before it can run the guard.
            await asyncio.to_thread(verify_host_python, bundle, mounted=self.settings.mounted)
            for source_file in (worker, verifier):
                trusted_source_file(str(source_file), mounted=self.settings.mounted)
        except IntegrityError as exc:
            _LOG.error('Browse renderer refused: %s', exc)
            raise BrowseError('browse_bundle_integrity') from exc
        host_python = str(bundle.host_python)
        command = [
            '/usr/bin/systemd-run', '--user', '--quiet', '--wait', '--pipe', '--collect',
            '--unit', self.unit, '-p', 'Type=exec', '-p', 'RuntimeMaxSec=150s',
            '-p', 'KillMode=control-group', '-p', 'TimeoutStopSec=2s', '-p', 'MemorySwapMax=0',
            '-p', f'MemoryMax={memory}', '-p', f'TasksMax={tasks}', '-p', 'CPUQuota=100%',
            '-p', 'NoNewPrivileges=yes', '-p', 'UMask=0077', '-p', 'LimitCORE=0', '--',
            '/usr/bin/env', '-i', *(f'{key}={value}' for key, value in env.items()),
            host_python, '-I', '-S', '-c', _CGROUP_GUARD, str(memory), str(tasks), '1',
            host_python, '-I', '-S', str(verifier), 'exec', str(bundle.config_path), bundle.config_sha256, '--',
            '/usr/bin/apptainer', 'exec', '--containall', '--cleanenv', '--no-home',
            '--no-mount', 'hostfs,cwd,home,bind-paths,sys', '--net', '--network', 'none',
            '--pid', '--ipc', '--no-privs', '--drop-caps', 'ALL', '--cwd', '/tmp',
            '--bind', f'{bundle.packages}:/browser-python:ro',
            '--bind', f'{worker}:/browse.py:ro', '--env', 'PLAYWRIGHT_BROWSERS_PATH=/ms-playwright',
            str(bundle.image), 'python', '-I', '-B', '/browse.py', os.readlink('/proc/self/ns/net'),
        ]
        self.process = await asyncio.create_subprocess_exec(*command, env=env,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        message = await asyncio.wait_for(self.receive(), 15)
        if message.get('kind') == 'integrity_error':
            _LOG.error('Browse renderer refused by host integrity guard: %r', str(message.get('detail', ''))[:1024])
            raise BrowseError('browse_bundle_integrity')
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

    async def exchange(self, command: dict, *, expected_actions: int | None = None):
        async with self.lock, asyncio.timeout(22):
            raise_if_cancelled('browse')
            if self.closing:
                raise BrowseError('browse_session_expired')
            if expected_actions is not None and expected_actions != self.actions:
                raise BrowseError('browse_retry_rejected')
            if self.actions >= 28:
                raise BrowseError('browse_action_limit')
            self.actions += 1
            navigations = {}
            await self.send(command)
            while True:
                raise_if_cancelled('browse')
                result = await self.receive()
                raise_if_cancelled('browse')
                if result.get('kind') == 'result':
                    result.pop('_navigation_response', None)
                    result['_action_sequence'] = self.actions
                    tab_id = result.get('tab_id')
                    observed_url = result.get('url')
                    document = navigations.get(tab_id) if isinstance(tab_id, str) else None
                    if document and isinstance(observed_url, str):
                        try:
                            if self.relay.authorize(observed_url).url == document['url']:
                                result['_navigation_response'] = document
                        except BrowseError:
                            pass
                    return result
                if result.get('kind') != 'request':
                    raise BrowseError('browse_renderer_protocol')
                navigation_tab = result.get('navigation_tab')
                navigation = isinstance(navigation_tab, str) and re.fullmatch(r't-[a-f0-9]{12}', navigation_tab)
                if navigation:
                    navigations.pop(navigation_tab, None)
                try:
                    response = await self.relay.fetch(result.get('url'), result.get('method'))
                except BrowseError:
                    raise_if_cancelled('browse')
                    if self.closing:
                        raise BrowseError('browse_session_expired')
                    await self.send({'ok': False})
                    continue
                raise_if_cancelled('browse')
                if navigation and result.get('method') == 'GET' and 200 <= response.status < 300:
                    from datetime import datetime, timezone
                    navigations[navigation_tab] = {
                        'url': response.url, 'http_status': response.status,
                        'received_at': datetime.now(timezone.utc).isoformat(),
                        'response_sha256': hashlib.sha256(response.body).hexdigest(),
                    }
                await self.send({'ok': True, 'status': response.status, 'headers': response.headers,
                                 'body': base64.b64encode(response.body).decode('ascii')})

    async def download(self, url: str):
        async with self.lock:
            if self.closing:
                raise BrowseError('browse_session_expired')
            return await self.relay.download(url)

    async def stop(self):
        await self.relay.close()
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
    await renderer.relay.close()
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
        if len(_SESSIONS) >= _MAX_SESSIONS or next(iter(_WAITERS)) != identity:
            from src.core.progress_bus import emit_progress
            emit_progress('browse_phase', {'phase': 'waiting'})
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
        settings = _configured()
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
