"""Request interleaving and copied worker contexts must never exchange SVGs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context

from src.core import figures


def test_interleaved_requests_keep_own_placeholders():
    async def scenario():
        a_ready, b_ready = asyncio.Event(), asyncio.Event()

        async def request_a():
            figures.begin_request()
            figures.register('<svg id="account-a"/>')
            assert figures.bump_attempts() == 1
            a_ready.set()
            await b_ready.wait()
            assert figures.count() == 1
            assert figures.bump_attempts() == 2
            return figures.expand('[[FIGURE:1]]')

        async def request_b():
            await a_ready.wait()
            figures.begin_request()
            figures.register('<svg id="account-b"/>')
            assert figures.bump_attempts() == 1
            b_ready.set()
            return figures.expand('[[FIGURE:1]]')

        a, b = await asyncio.gather(request_a(), request_b())
        assert 'account-a' in a and 'account-b' not in a
        assert 'account-b' in b and 'account-a' not in b

    asyncio.run(scenario())


def test_worker_updates_visible_only_to_owning_context():
    figures.begin_request()
    first_request = copy_context()
    with ThreadPoolExecutor(max_workers=2) as workers:
        registered = workers.submit(first_request.run, figures.register, '<svg id="first"/>')
        assert registered.result()[0] == 1
        assert 'first' in figures.expand('[[FIGURE:1]]')
        figures.begin_request()
        assert figures.count() == 0
        assert figures.expand('[[FIGURE:1]]') == '[[FIGURE:1]]'
        assert 'first' in first_request.run(figures.expand, '[[FIGURE:1]]')


def test_parallel_tools_in_one_request_have_unique_indices():
    figures.begin_request()
    with ThreadPoolExecutor(max_workers=4) as workers:
        futures = [workers.submit(copy_context().run, figures.register, f'<svg id="{i}"/>')
                   for i in range(20)]
        indices = [future.result()[0] for future in futures]
    assert sorted(indices) == list(range(1, 21))
    assert figures.count() == 20
    snapshot = figures.snapshot()
    snapshot[1]['svg'] = 'changed'
    assert figures.snapshot()[1]['svg'] != 'changed'
