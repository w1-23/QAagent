"""Single-worker figure API admission; occupied until retained work drains."""
from __future__ import annotations
import os
import threading


class FigureCapacityExceeded(RuntimeError):
    pass


_lock = threading.Lock()
_active: dict[int, int] = {}


def _positive(name: str, default: int) -> int:
    value = int(os.environ.get(name, default))
    if value <= 0:
        raise ValueError(f'{name} must be positive')
    return value


def reserve_on_lease(owner: int, lease) -> None:
    """No unbounded HTTP queue; a caller retries after an explicit 429."""
    if lease.owner != int(owner):
        raise ValueError('figure capacity owner mismatch')
    per_owner = _positive('CITRUS_FIGURE_ACTIVE_PER_ACCOUNT', 2)
    total = _positive('CITRUS_FIGURE_ACTIVE_TOTAL', 4)
    owner = int(owner)
    with _lock:
        if _active.get(owner, 0) >= per_owner or sum(_active.values()) >= total:
            raise FigureCapacityExceeded('图件操作繁忙，请等待当前重绘完成后重试。')
        _active[owner] = _active.get(owner, 0) + 1
    def release():
        with _lock:
            left = _active.get(owner, 0) - 1
            if left > 0:
                _active[owner] = left
            else:
                _active.pop(owner, None)
    try:
        lease.add_finalizer(release)
    except BaseException:
        release()
        raise
