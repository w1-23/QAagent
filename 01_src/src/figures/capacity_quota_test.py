"""Capacity/lifecycle regressions using isolated engineering accounts and stores."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from src.api.test_figure_router import _workspace


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    main = _workspace(tmp_path, monkeypatch)
    from src.core import figure_capacity
    monkeypatch.setattr(figure_capacity, '_active', {})
    monkeypatch.setenv('CITRUS_FIGURE_ACTIVE_PER_ACCOUNT', '1')
    monkeypatch.setenv('CITRUS_FIGURE_ACTIVE_TOTAL', '2')
    return main


def test_capacity_is_owner_scoped_and_global_and_released(workspace):
    from fastapi import HTTPException
    from src.api.figure_router import _write_scope
    from src.core import figure_capacity
    with _write_scope(101):
        with pytest.raises(HTTPException) as full:
            with _write_scope(101):
                pytest.fail('same owner admitted past limit')
        assert full.value.status_code == 429
        assert full.value.headers['Retry-After'] == '2'
        with _write_scope(202):
            assert sum(figure_capacity._active.values()) == 2
    assert figure_capacity._active == {}
    with _write_scope(101):
        assert figure_capacity._active == {101: 1}
    assert not figure_capacity._active


def test_cancelled_observer_keeps_capacity_until_real_thread_finishes(workspace):
    from fastapi import HTTPException
    from src.api.figure_router import _write_scope
    from src.core.account_leases import run_account_thread
    from src.core import figure_capacity
    started, finish = threading.Event(), threading.Event()
    def real_work():
        started.set()
        assert finish.wait(5), 'engineering worker timed out'
    async def exercise():
        async def request():
            with _write_scope(101) as lease:
                await run_account_thread(lease, real_work)
        task = asyncio.create_task(request())
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert figure_capacity._active == {101: 1}
            with pytest.raises(HTTPException) as full:
                with _write_scope(101):
                    pytest.fail('late thread lost its slot')
            assert full.value.status_code == 429
        finally:
            finish.set()
        for _ in range(200):
            if not figure_capacity._active:
                break
            await asyncio.sleep(.01)
        assert not figure_capacity._active
    asyncio.run(exercise())


def test_capacity_global_limit_and_invalid_config_fail_closed(workspace, monkeypatch):
    from fastapi import HTTPException
    from src.api.figure_router import _write_scope
    from src.core import figure_capacity
    monkeypatch.setenv('CITRUS_FIGURE_ACTIVE_TOTAL', '1')
    with _write_scope(101):
        with pytest.raises(HTTPException) as full:
            with _write_scope(202):
                pytest.fail('global limit bypassed')
        assert full.value.status_code == 429
    monkeypatch.setenv('CITRUS_FIGURE_ACTIVE_PER_ACCOUNT', '0')
    with pytest.raises(ValueError):
        with _write_scope(101):
            pytest.fail('invalid quota accepted')
    assert not figure_capacity._active


def test_project_quota_serializes_concurrent_creates_and_preserves_old_project(workspace, monkeypatch):
    from src.figures import store
    monkeypatch.setenv('CITRUS_FIGURE_PROJECTS_PER_ACCOUNT', '1')
    # Initialize schema before exercising concurrent allocation transactions.
    assert store.list_projects(101) == []
    def create(i):
        try:
            return store.create_project(owner_user_id=101, title=f'SYNTHETIC {i}')
        except ValueError as exc:
            return str(exc)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    winners = [x for x in results if isinstance(x, dict)]
    assert len(winners) == 1
    assert len(store.list_projects(101)) == 1
    winner = winners[0]
    assert store.get_project(winner['figure_id'], 101)
    assert store.update_project(winner['figure_id'], 101, title='SYNTHETIC existing')
    assert store.create_project(owner_user_id=202, title='SYNTHETIC other owner')
    monkeypatch.setenv('CITRUS_FIGURE_PROJECTS_PER_ACCOUNT', '0')
    with pytest.raises(ValueError, match='must be positive'):
        store.create_project(owner_user_id=101)
    assert store.get_project(winner['figure_id'], 101)['title'] == 'SYNTHETIC existing'


def test_project_limit_becomes_explicit_api_refusal(workspace, monkeypatch):
    from fastapi import HTTPException
    from src.api.figure_router import _write_scope
    from src.figures import store
    monkeypatch.setenv('CITRUS_FIGURE_PROJECTS_PER_ACCOUNT', '1')
    store.create_project(owner_user_id=101, title='SYNTHETIC retained')
    with pytest.raises(HTTPException) as full:
        with _write_scope(101):
            store.create_project(owner_user_id=101)
    assert full.value.status_code == 429
    assert full.value.detail['code'] == 'figure_project_limit'
    assert len(store.list_projects(101)) == 1
