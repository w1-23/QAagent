"""Expiring Worker availability hint, never an execution lease or safety receipt."""
from contextlib import closing, contextmanager
import os
import re
import sqlite3
import threading
import time
import uuid

_SCHEMA = ('CREATE TABLE IF NOT EXISTS code_worker_presence ('
           'instance TEXT PRIMARY KEY, seen REAL NOT NULL, policy TEXT NOT NULL)')


_RELEASE_SCHEMA = ('CREATE TABLE IF NOT EXISTS code_worker_release_presence ('
                   'instance TEXT PRIMARY KEY, seen REAL NOT NULL, '
                   'release TEXT NOT NULL, activation TEXT NOT NULL)')


def worker_available(path, *, now=None, since=None, release=None, activation=None):
    if not path.is_file():
        return False
    observed_at = time.time() if now is None else now
    earliest = observed_at - 10 if since is None else max(since, observed_at - 10)
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=1)) as conn:
            release = os.getenv('CITRUS_WORKER_RELEASE', '') if release is None else release
            if release:
                sql = ('SELECT 1 FROM code_worker_release_presence WHERE seen>? AND seen<=? AND release=?')
                args = (earliest, observed_at + 1, release)
                if activation is not None:
                    sql += ' AND activation=?'
                    args += (activation,)
                return conn.execute(sql + ' LIMIT 1', args).fetchone() is not None
            if activation is not None:
                return False
            return conn.execute('SELECT 1 FROM code_worker_presence WHERE seen>? AND seen<=? '
                "AND policy='offline-development' LIMIT 1",
                (earliest, observed_at + 1)).fetchone() is not None
    except sqlite3.Error:
        return False


@contextmanager
def worker_presence(path):
    """Publish while the controller is alive, including a long sandbox wait."""
    instance = uuid.uuid4().hex
    release = os.getenv('CITRUS_WORKER_RELEASE', '')
    activation = os.getenv('CITRUS_WORKER_ACTIVATION', '') or instance
    if release and (not re.fullmatch('[a-f0-9]{64}', release) or not re.fullmatch('[a-f0-9]{32}', activation)):
        raise ValueError('Invalid worker release identity')
    table = 'code_worker_release_presence' if release else 'code_worker_presence'
    stopped = threading.Event()
    failed = threading.Event()
    def pulse():
        with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
            conn.execute(_RELEASE_SCHEMA if release else _SCHEMA)
            conn.execute(f'DELETE FROM {table} WHERE seen<?', (time.time() - 60,))
            if release:
                conn.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?,?)',
                             (instance, time.time(), release, activation))
            else:
                conn.execute(f'INSERT OR REPLACE INTO {table} VALUES(?,?,?)',
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
        with closing(sqlite3.connect(path, timeout=5)) as conn, conn:
            conn.execute(f'DELETE FROM {table} WHERE instance=?', (instance,))
