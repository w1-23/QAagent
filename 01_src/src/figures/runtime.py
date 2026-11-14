"""Serialize Matplotlib's process-global settings until rendering moves to workers."""
from contextlib import contextmanager
from threading import RLock

_RENDER_LOCK = RLock()


@contextmanager
def render_context(params=None):
    import matplotlib
    with _RENDER_LOCK, matplotlib.rc_context(rc=params):
        yield
