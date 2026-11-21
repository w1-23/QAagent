"""Atomic admission, dispatch reservations and current reader counts."""
from collections import Counter
from contextlib import contextmanager
import re
import threading
import time

from starlette.responses import JSONResponse

MAINTENANCE_MESSAGE = "语料库自进化更新中，已发送的消息会保留并按顺序继续"
RETRY_AFTER = 5
_ENQUEUE = re.compile(r"/api/v2/session/[^/]+/turn-queue")
_HISTORY = re.compile(r"/api/v2/session/[^/]+/(?:messages|history|turn-queue(?:/[^/]+)?|task-by-request/[^/]+)")
_EVENT = re.compile(r"/jobs/[^/]+/events")


class MaintenanceBusy(RuntimeError):
    pass


class ActivityTracker:
    def __init__(self, journal, *, quiet_sec=120, clock=time.time):
        self.journal = journal
        self.quiet_sec = quiet_sec
        self.clock = clock
        self.lock = threading.RLock()
        self.counts = Counter()
        self.last_activity = max(self.clock(), journal.last_activity())
        self.gated = False

    def enter(self, kind, *, allowed_while_gated=False):
        with self.lock:
            if self.gated and not allowed_while_gated:
                raise MaintenanceBusy("knowledge_base_updating")
            self.counts[kind] += 1

    def leave(self, kind, *, user_activity=False):
        with self.lock:
            self.counts[kind] -= 1
            if user_activity:
                self.last_activity = self.clock()
                try:
                    self.journal.touch(self.last_activity)
                except Exception:
                    # The process clock still enforces the quiet interval; restart
                    # begins a fresh quiet interval before any publication.
                    import logging
                    logging.getLogger(__name__).warning("Shared activity journal write unavailable")

    @contextmanager
    def reader(self):
        self.enter("R")
        try:
            yield
        finally:
            self.leave("R")

    def try_gate(self, durable_counts, mark_gated):
        # Incoming business requests and graph dispatch reserve under this same lock.
        with self.lock:
            if (self.gated or any(self.counts.values())
                    or self.clock() - self.last_activity < self.quiet_sec):
                return False
            if any(durable_counts().values()):
                return False
            mark_gated()
            self.gated = True
            return True

    def release(self):
        with self.lock:
            self.gated = False


class ActivityMiddleware:
    """Keep HTTP reservations until the ASGI response body has finished."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        from .runtime import current_runtime
        runtime = current_runtime()
        path = scope["path"]
        if runtime is None or not (path.startswith(("/api/", "/jobs/", "/sessions/"))):
            return await self.app(scope, receive, send)
        method = scope["method"]
        if (method == "OPTIONS" or path in {"/api/v2/shared-corpus/maintenance", "/api/v2/auth/me",
                                           "/api/v2/auth/avatar"}
                or method == "GET" and _EVENT.fullmatch(path)
                or path.startswith("/api/v2/auth/") and path.rsplit("/", 1)[-1] in {
                    "login", "logout", "register", "recover"}):
            # Durable jobs account for active events; an open observer adds no work.
            return await self.app(scope, receive, send)
        enqueue = method == "POST" and bool(_ENQUEUE.fullmatch(path))
        history = method in {"GET", "HEAD"} and bool(_HISTORY.fullmatch(path))
        cancel_turn = method == "DELETE" and path.rsplit("/", 1)[0].endswith("/turn-queue")
        activity = runtime.activity
        try:
            activity.enter("H", allowed_while_gated=enqueue or history or cancel_turn)
        except MaintenanceBusy:
            response = JSONResponse({"detail": {"code": "knowledge_base_updating",
                                                "message": MAINTENANCE_MESSAGE}}, status_code=503,
                                    headers={"Retry-After": str(RETRY_AFTER), "Cache-Control": "no-store"})
            return await response(scope, receive, send)
        try:
            return await self.app(scope, receive, send)
        finally:
            # Passive FIFO/status reads do not reset the quiet interval.
            activity.leave("H", user_activity=bool(scope.get("state", {}).get("user"))
                           and not (method == "GET" and _ENQUEUE.fullmatch(path)))
