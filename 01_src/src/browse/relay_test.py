"""Defensive relay fixtures: only local TCP and a synthetic resolver are used."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from http.client import HTTPConnection
from itertools import count
import json
from pathlib import Path
import socket
import sys
from tempfile import TemporaryDirectory
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from src.browse import relay as transport
from src.core.cancel import CancelToken, ToolCancelled, bound_token

_URL = 'https://relay.example/start'
_SLOW_BUDGET = 1.2


def _fixture_worker(mode, port, entered):
    def resolve(host, port, *, type):
        assert host == 'relay.example' and port == 443 and type == socket.SOCK_STREAM
        Path(entered).touch()
        if mode == 'dns':
            time.sleep(30)
        records = [(socket.AF_INET, type, 0, '', ('8.8.8.8', 443))]
        if mode == 'mixed':
            records.append((socket.AF_INET6, type, 0, '', ('::1', 443, 0, 0)))
        return records

    def connect(host, peer, *, timeout):
        # Replace only the socket adapter in this fixture. The real worker still
        # runs its DNS policy, HTTP parser, receipt, and deadline checkpoints.
        assert host == 'relay.example' and peer == '8.8.8.8'
        if mode == 'connect':
            Path(entered).with_suffix('.connect').touch()
            time.sleep(30)
        assert mode == 'http'
        return HTTPConnection('127.0.0.1', int(port), timeout=timeout)

    transport.socket.getaddrinfo = resolve
    # HTTPConnection would otherwise resolve loopback again; bypass resolution in the fixture.
    def connect_local(address, timeout, source_address):
        conn = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        conn.settimeout(timeout)
        conn.connect(address)
        return conn

    transport.socket.create_connection = connect_local
    transport._PinnedHTTPS = connect
    transport._worker()


class RelayDeadlineTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        work = self.enterContext(TemporaryDirectory())
        self.entered = Path(work) / 'resolver-entered'
        self.relay = transport.Relay(frozenset({'relay.example'}))
        self.processes = []
        self.handlers = set()

    async def asyncTearDown(self):
        for task in self.handlers:
            task.cancel()
        await asyncio.gather(*self.handlers, return_exceptions=True)
        # A broken relay may leave a fixture process behind; don't let it outlive the test.
        for process in self.processes:
            if process.returncode is None:
                with suppress(ProcessLookupError):
                    process.kill()
                await process.wait()

    async def _spawn(self, mode, port=0):
        entry = ('import sys; sys.path.insert(0, sys.argv[1]); '
                 'from src.browse.relay_test import _fixture_worker; _fixture_worker(*sys.argv[2:])')
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-I', '-S', '-B', '-c', entry, transport._SOURCE_ROOT, mode, str(port),
            str(self.entered), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=transport._RECEIPT_LIMIT,
        )
        self.processes.append(process)
        return process

    def _mock_worker(self, mode, port=0):
        async def spawn():
            return await self._spawn(mode, port)
        return patch.object(transport, '_spawn_worker', new=spawn)

    async def _stage(self):
        async with asyncio.timeout(4):
            while not self.entered.exists():
                await asyncio.sleep(0.01)

    async def _serve(self, payload, *, trickle=False):
        connected = asyncio.Event()
        requests = []

        async def response(reader, writer):
            task = asyncio.current_task()
            self.handlers.add(task)
            try:
                request = await reader.readuntil(b'\r\n\r\n')
                requests.append(request)
                writer.write(payload)
                await writer.drain()
                connected.set()
                if trickle:
                    while True:
                        await asyncio.sleep(0.025)
                        writer.write(b'x')
                        await writer.drain()
            except (ConnectionError, asyncio.IncompleteReadError):
                pass
            finally:
                writer.close()
                with suppress(ConnectionError):
                    await writer.wait_closed()
                self.handlers.discard(task)

        server = await asyncio.start_server(response, '127.0.0.1', 0)
        await self.enterAsyncContext(server)
        return server.sockets[0].getsockname()[1], connected, requests

    def _assert_reaped(self):
        self.assertEqual(len(self.processes), 1)
        self.assertIsNotNone(self.processes[0].returncode)

    async def test_production_launch_uses_the_isolated_worker(self):
        process = await transport._spawn_worker()
        self.processes.append(process)
        request = json.dumps([_URL, 'GET', 1024, time.monotonic() - 1]).encode()
        stdout, _ = await asyncio.wait_for(process.communicate(request), 3)
        self.assertEqual(process.returncode, 0)
        self.assertEqual(json.loads(stdout), [0, {}, 0, 'browse_network_timeout'])

    async def test_absolute_deadline_interrupts_dns_and_reaps(self):
        start = time.monotonic()
        with self._mock_worker('dns'), self.assertRaisesRegex(transport.BrowseError, '^browse_network_timeout$'):
            await self.relay.fetch(_URL, 'GET', limit=64, max_seconds=_SLOW_BUDGET)
        self.assertTrue(self.entered.exists(), 'fixture did not reach DNS')
        self.assertLess(time.monotonic() - start, _SLOW_BUDGET + 1)
        self.assertEqual(self.relay.remaining_requests, 99)
        self.assertEqual(self.relay.remaining_bytes, 40 * 1024 * 1024 - 65)
        self._assert_reaped()

    async def test_absolute_deadline_interrupts_connection_setup(self):
        with self._mock_worker('connect'), self.assertRaisesRegex(transport.BrowseError, '^browse_network_timeout$'):
            await self.relay.fetch(_URL, 'GET', max_seconds=_SLOW_BUDGET)
        self.assertTrue(self.entered.with_suffix('.connect').exists(), 'fixture did not reach connection setup')
        self._assert_reaped()

    async def _trickle_times_out(self, prefix):
        port, connected, _ = await self._serve(prefix, trickle=True)
        start = time.monotonic()
        with self._mock_worker('http', port), self.assertRaisesRegex(transport.BrowseError, '^browse_network_timeout$'):
            await self.relay.fetch(_URL, 'GET', max_seconds=_SLOW_BUDGET)
        self.assertTrue(connected.is_set(), 'fixture did not reach HTTP')
        self.assertLess(time.monotonic() - start, _SLOW_BUDGET + 1)
        self._assert_reaped()

    async def test_absolute_deadline_interrupts_trickling_headers(self):
        await self._trickle_times_out(b'HTTP/1.1 200 OK\r\nX-Fixture: ')

    async def test_absolute_deadline_interrupts_trickling_body(self):
        await self._trickle_times_out(b'HTTP/1.1 200 OK\r\nContent-Length: 4096\r\n\r\n')

    async def test_cooperative_cancel_kills_and_reaps(self):
        token = CancelToken()
        with self._mock_worker('dns'), bound_token(token):
            task = asyncio.create_task(self.relay.fetch(_URL, 'GET'))
            await self._stage()
            token.cancel('fixture-stop')
            with self.assertRaisesRegex(ToolCancelled, 'fixture-stop'):
                await asyncio.wait_for(task, 1)
        self._assert_reaped()

    async def test_async_cancel_during_spawn_still_reaps_owned_process(self):
        started, return_handle = asyncio.Event(), asyncio.Event()

        async def spawn():
            process = await self._spawn('dns')
            started.set()
            await return_handle.wait()
            return process

        with patch.object(transport, '_spawn_worker', new=spawn):
            task = asyncio.create_task(self.relay.fetch(_URL, 'GET'))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # A second cancel must not prematurely release its lease.
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            return_handle.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self._assert_reaped()

    async def test_relay_close_waits_for_reaping(self):
        with self._mock_worker('dns'):
            task = asyncio.create_task(self.relay.fetch(_URL, 'GET'))
            await self._stage()
            await asyncio.wait_for(self.relay.close(), 1)
            with self.assertRaisesRegex(transport.BrowseError, '^browse_session_expired$'):
                await task
            with self.assertRaisesRegex(transport.BrowseError, '^browse_session_expired$'):
                await self.relay.fetch(_URL, 'GET')
        self._assert_reaped()

    async def test_dns_checks_every_answer_before_connecting(self):
        with self._mock_worker('mixed'), self.assertRaisesRegex(transport.BrowseError, '^browse_dns_denied$'):
            await self.relay.fetch(_URL, 'GET')
        self._assert_reaped()
        self.assertEqual(self.relay.remaining_bytes, 40 * 1024 * 1024)

    async def test_policy_rejects_method_and_urls_before_spawning(self):
        with patch.object(transport, '_spawn_worker', new_callable=AsyncMock) as spawn:
            for url, method, reason in (
                (_URL, 'POST', 'browse_method_denied'),
                (_URL, [], 'browse_method_denied'),
                ('http://relay.example/', 'GET', 'browse_url_denied'),
                ('https://relay.example:8443/', 'GET', 'browse_url_denied'),
                ('https://user@relay.example/', 'GET', 'browse_url_denied'),
                ('https://other.example/', 'GET', 'browse_host_not_allowed'),
            ):
                with self.subTest(url=url, method=method), self.assertRaisesRegex(transport.BrowseError, f'^{reason}$'):
                    await self.relay.fetch(url, method)
            spawn.assert_not_called()
        self.assertEqual(self.relay.remaining_requests, 100)

    async def test_success_returns_filtered_headers_and_charges_actual_body(self):
        port, _, requests = await self._serve(b'HTTP/1.1 200 OK\r\nContent-Length: 3\r\n'
            b'Content-Type: text/plain\r\nSet-Cookie: test=private\r\n\r\nabc')
        with self._mock_worker('http', port):
            result = await self.relay.fetch(_URL + '?a=1#frag', 'GET')
            head = await self.relay.fetch(_URL, 'HEAD')
        self.assertEqual(result, transport.Exchange(_URL + '?a=1', 200, {'content-type': 'text/plain'}, b'abc'))
        self.assertEqual(head.body, b'')
        self.assertEqual(self.relay.remaining_bytes, 40 * 1024 * 1024 - 3)
        self.assertIn(b'GET /start?a=1 HTTP/1.1\r\n', requests[0])
        self.assertIn(b'Accept-Encoding: identity\r\n', requests[0])
        self.assertIn(b'HEAD /start HTTP/1.1\r\n', requests[1])
        self.assertTrue(all(process.returncode == 0 for process in self.processes))

    async def test_overflow_charges_consumed_bytes_without_returning_partial_data(self):
        port, _, _ = await self._serve(b'HTTP/1.1 200 OK\r\nConnection: close\r\n\r\n12345')
        with self._mock_worker('http', port), self.assertRaisesRegex(transport.BrowseError, '^browse_response_limit$'):
            await self.relay.fetch(_URL, 'GET', limit=3)
        self.assertEqual(self.relay.remaining_bytes, 40 * 1024 * 1024 - 4)
        self._assert_reaped()

    async def test_redirect_host_revalidated_before_returning(self):
        port, _, requests = await self._serve(b'HTTP/1.1 302 Found\r\n'
            b'Location: https://other.example/\r\nContent-Length: 0\r\n\r\n')
        with self._mock_worker('http', port), self.assertRaisesRegex(transport.BrowseError, '^browse_host_not_allowed$'):
            await self.relay.download(_URL)
        self.assertEqual(len(requests), 1)
        self._assert_reaped()

    async def test_download_uses_one_deadline_clipped_by_session(self):
        recovered = transport.Exchange('https://relay.example/end', 200, {}, b'x')
        redirected = transport.Exchange(_URL, 302, {'location': '/end'}, b'')
        for session_deadline, expected in [(108.0, 108.0), (150.0, 115.0)]:
            self.relay.deadline = session_deadline
            # Stage the second redirect several seconds later without sleeping.
            clock = SimpleNamespace(monotonic=count(100, 0.5).__next__)
            with patch.object(transport, 'time', clock), patch.object(self.relay, '_fetch',
                    new_callable=AsyncMock, side_effect=[(redirected, 0, ''), (recovered, 1, '')]) as run:
                self.assertEqual(await self.relay.download(_URL), (recovered, [_URL]))
            calls = run.await_args_list
            self.assertEqual([call.args[0].url for call in calls], [_URL, recovered.url])
            self.assertEqual([call.args[3] for call in calls], [expected] * 2)

    async def test_browser_fetch_clips_deadline_and_enforces_request_cap(self):
        self.relay.deadline = time.monotonic() + 3
        self.relay.remaining_requests = 1
        with patch.object(self.relay, '_fetch', new_callable=AsyncMock,
                          return_value=(transport.Exchange(_URL, 200, {}, b''), 0, '')) as run:
            await self.relay.fetch(_URL, 'GET')
            self.assertEqual(run.await_args.args[3], self.relay.deadline)
            with self.assertRaisesRegex(transport.BrowseError, '^browse_network_budget$'):
                await self.relay.fetch(_URL, 'GET')
            run.assert_awaited_once()
