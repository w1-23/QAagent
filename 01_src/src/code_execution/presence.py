"""Expiring Worker availability hint, never an execution lease or safety receipt."""
from contextlib import contextmanager
import sqlite3
import threading
import time
import uuid

_SCHEMA = ('CREATE TABLE IF NOT EXISTS code_worker_presence ('
           'instance TEXT PRIMARY KEY, seen REAL NOT NULL, policy TEXT NOT NULL)')


def worker_available(path, *, now=None, since=None):
    if not path.is_file():
        return False
    observed_at = time.time() if now is None else now
    earliest = observed_at - 10 if since is None else max(since, observed_at - 10)
    try:
        with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1) as conn:
            return conn.execute('SELECT 1 FROM code_worker_presence WHERE seen>? AND seen<=? '
                "AND policy='offline-development' LIMIT 1",
                (earliest, observed_at + 1)).fetchone() is not None
    except sqlite3.Error:
        return False


@contextmanager
def worker_presence(path):
    """Publish while the controller is alive, including a long sandbox wait."""
    instance = uuid.uuid4().hex
    stopped = threading.Event()
    failed = threading.Event()
    def pulse():
        with sqlite3.connect(path, timeout=5) as conn:
            conn.execute(_SCHEMA)
            conn.execute('DELETE FROM code_worker_presence WHERE seen<?', (time.time() - 60,))
            conn.execute('INSERT OR REPLACE INTO code_worker_presence VALUES(?,?,?)',
                         (instance, time.time(), 'offline-development'))
    def maintain():
        while not stopped.wait(2):
            try:
                pulse()
            except sqlite3.Error:
                failed.set()
                return
    pulse()
    thread = threading.Thread(target=maintain, name='code-worker-presence', daemon=True)
    thread.start()
    try:
        yield failed
    finally:
        stopped.set()
        thread.join(timeout=6)
        with sqlite3.connect(path, timeout=5) as conn:
            conn.execute('DELETE FROM code_worker_presence WHERE instance=?', (instance,))
