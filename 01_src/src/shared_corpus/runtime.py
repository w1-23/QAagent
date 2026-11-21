"""One shared component per supported Web process, with durable activation."""
from __future__ import annotations

import asyncio
from concurrent.futures import Future
from contextlib import closing, contextmanager
from hashlib import sha256
import json
import logging
import os
from pathlib import Path
import sqlite3
from threading import Thread
import time

from .activity import ActivityTracker, MAINTENANCE_MESSAGE, RETRY_AFTER
from .journal import Journal, _ACTIVATING, read_pointer, write_pointer

logger = logging.getLogger(__name__)
CORPUS_ID = "shared_agent_literature"
FAMILY_ID = "agent_literature"
_runtime = None
_HEALTH_TIMEOUT_SEC = 5


def current_runtime():
    return _runtime


@contextmanager
def retrieval_reader():
    runtime = current_runtime()
    if runtime is None:
        yield
    else:
        with runtime.activity.reader():
            yield


def _models():
    from src.engine.embedder import Embedder
    from src.engine.reranker import Reranker
    return Embedder(), Reranker()


def _durable_counts():
    from src.config import PROJECT_ROOT
    from src.core import chat_turn_queue as queue, jobs
    from src.code_execution.protocol import TERMINAL

    with closing(sqlite3.connect(Path(jobs._DB_PATH).resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
        conn.row_factory = sqlite3.Row
        queued = conn.execute("SELECT EXISTS(SELECT 1 FROM chat_turn_queue WHERE state <> ?)",
                              (queue.TERMINAL,)).fetchone()[0]
        # Awaiting-input parents stay that way after a validated child is created.
        # Use the same reconciliation rule as FIFO for legacy open work.
        scopes = conn.execute("SELECT DISTINCT owner_user_id,session_id FROM task_jobs "
                              "WHERE status IN ('running','detached','cancel_requested','queued','awaiting_input') "
                              "OR current_step='code_reconciliation_required'")
        graph = any((queue._legacy_blocker(conn, owner, session) if owner is not None else True)
                    for owner, session in scopes)
    code_path = PROJECT_ROOT / "state" / "code_executions.sqlite3"
    tools = False
    if os.getenv("CITRUS_CODE_EXECUTION") == "offline-development" and code_path.exists():
        with closing(sqlite3.connect(code_path.resolve().as_uri() + "?mode=ro", uri=True, timeout=1)) as conn:
            marks = ",".join("?" for _ in TERMINAL)
            tools = conn.execute(f"SELECT EXISTS(SELECT 1 FROM code_executions WHERE status NOT IN ({marks}))",
                                 tuple(TERMINAL)).fetchone()[0]
    return {"J": graph, "T": tools, "Q": queued}


def _digest(text):
    return sha256(text.encode("utf-8")).hexdigest()


class SharedCorpusRuntime:
    def __init__(self, root, *, models=_models, counts=_durable_counts, quiet_sec=120):
        from .catalog import Catalog
        self.root = Path(root)
        self.catalog = Catalog(self.root)
        self.journal = Journal(self.root)
        self.activity = ActivityTracker(self.journal, quiet_sec=quiet_sec)
        self.models = models
        self.counts = counts
        self.handle = None
        self.generation = None
        self.unavailable = False
        self._candidate = None
        self._preflight_thread = None
        self.wake = asyncio.Event()
        self.loop = None

    def notify(self):
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.wake.set)

    def request_build(self, owner):
        return self.enqueue_build(owner, self.catalog.publishable())

    def enqueue_build(self, owner, publishable):
        documents = sorted({(row["document_id"], row["version_id"]) for row in publishable})
        if not documents or len(documents) > 100:
            raise ValueError("shared_publishable_batch_out_of_bounds")
        job = self.journal.enqueue(owner, documents)
        self.notify()
        return job

    def _build(self, job):
        from .build import build_generation
        from .chunks import chunk_document
        expected = json.loads(job["documents"])
        chunks = []
        for document, version in expected:
            if not self.catalog.is_visible(document, version):
                raise ValueError("shared_publication_ineligible")
            metadata, markdown = self.catalog.read_document(document, version)
            chunks.extend(chunk_document(metadata, markdown))
            if len(chunks) > 20_000:
                raise ValueError("shared_chunk_budget_exhausted")
        embedder, _ = self.models()
        return build_generation(self.root, chunks, embedder=embedder)[0]

    def _load(self, generation):
        if generation is None:
            return None
        from .generations import load_generation
        embedder, reranker = self.models()
        return load_generation(self.root, generation, embedder=embedder, reranker=reranker)

    def _document_span(self, row):
        document, version = row["document_id"], row["version_id"]
        if not self.catalog.is_visible(document, version):
            raise LookupError("shared_document_unavailable")
        _, text = self.catalog.read_document(document, version)
        start, end = row["md_start"], row["md_end"]
        if not (0 <= start < end <= len(text)) or text[start:end] != row["text"]:
            raise ValueError("shared_source_span_mismatch")
        return text, _digest(text)

    def search_candidates(self, query, *, limit=6):
        with self.activity.reader():
            if self.unavailable or self.handle is None:
                return []
            rows = self.handle.search(query, limit=limit)
            result = []
            for row in rows:
                try:
                    _, digest = self._document_span(row)
                except (LookupError, ValueError, OSError):
                    continue
                result.append({**row, "source_family": FAMILY_ID, "index_generation": self.generation,
                               "read_ref": {"document_id": row["document_id"], "version_id": row["version_id"],
                                            "index_generation": self.generation, "md_sha256": digest,
                                            "start": row["md_start"], "end": row["md_end"]}})
            return result

    def search(self, query, *, limit=6):
        fields = ("source_family", "index_generation", "document_id", "version_id",
                  "chunk_id", "title", "section_name", "read_ref")
        return [{**{key: row[key] for key in fields if key in row}, "excerpt": row["text"]}
                for row in self.search_candidates(query, limit=limit)]

    def reference_visible(self, reference):
        document = reference["document_id"]
        version = reference["version_id"]
        return (not self.unavailable and self.generation is not None
                and version in self.journal.active_versions(reference["index_generation"], document)
                and self.catalog.is_visible(document, version))

    def indexing_status(self, document, version):
        inactive = {"indexing_status": "unindexed", "index_generation": None}
        if not document or not version or not self.catalog.is_visible(document, version):
            return inactive
        if not self.unavailable and version in self.journal.active_versions(self.generation, document):
            return {"indexing_status": "active", "index_generation": self.generation}
        pending = self.journal.pending()
        if (pending and pending["generation"] and pending["state"] != "rolling_back"
                and [document, version] in json.loads(pending["documents"])):
            return {"indexing_status": "indexed_ready", "index_generation": pending["generation"]}
        return inactive

    def preview(self, document, *, generation=None, version=None, start=0, length=12000, digest=None):
        with self.activity.reader():
            if self.unavailable or not self.generation:
                raise LookupError("shared_document_unavailable")
            generation = generation or self.generation
            versions = self.journal.active_versions(generation, document)
            if version is None and len(versions) == 1:
                version = versions[0]
            if version not in versions:
                raise LookupError("shared_document_unavailable")
            if not self.catalog.is_visible(document, version):
                raise LookupError("shared_document_unavailable")
            metadata, text = self.catalog.read_document(document, version)
            actual_digest = _digest(text)
            if digest is not None and digest != actual_digest:
                raise LookupError("shared_document_unavailable")
            if start > len(text):
                raise ValueError("shared_preview_offset_out_of_range")
            stop = min(start + length, len(text))
            return {"document_id": document, "version_id": version, "index_generation": generation,
                    "title": metadata.get("title", ""), "doi": metadata.get("doi", ""),
                    "section_path": metadata.get("section_path", []), "kind": "stored_markdown",
                    "md_sha256": actual_digest, "start": start, "end": stop, "total_chars": len(text),
                    "next_start": stop if stop < len(text) else None, "markdown": text[start:stop]}

    def _checked_candidate(self, generation):
        from .search import HEALTH_QUERY
        handle = self._load(generation)
        try:
            rows = handle.search(HEALTH_QUERY, limit=12)
            if not rows:
                raise ValueError("shared_health_query_empty")
            for row in rows:
                self._document_span(row)
            return handle, rows
        except BaseException:
            handle.close()
            raise

    async def _check_candidate(self, generation):
        deadline = time.monotonic() + _HEALTH_TIMEOUT_SEC
        work = Future()
        work.set_running_or_notify_cancel()

        def check():
            try:
                candidate = self._checked_candidate(generation)
            except BaseException as error:
                work.set_exception(error)
            else:
                work.set_result(candidate)

        def discard(completed):
            if completed.exception() is None:
                handle, _ = completed.result()
                try:
                    handle.close()
                except Exception as error:
                    logger.warning("Discarded shared handle could not close: %s", type(error).__name__)

        # The preflight owns an independent read-only handle. Keep native reads
        # outside the default executor so they cannot delay event-loop shutdown.
        thread = Thread(target=check, name="shared-corpus-preflight", daemon=True)
        thread.start()
        self._preflight_thread = thread
        result = asyncio.wrap_future(work)
        try:
            done, _ = await asyncio.wait((result,), timeout=max(0, deadline - time.monotonic()))
            if not done or time.monotonic() >= deadline:
                raise TimeoutError("shared_health_query_timeout")
            return result.result()
        except (TimeoutError, asyncio.CancelledError):
            if result.done():
                result.exception()
            else:
                result.cancel()
            # Cancelling the wrapper cannot stop the running native call.
            work.add_done_callback(discard)
            raise

    def _publish(self, job, handle, samples):
        expected = json.loads(job["documents"])
        if not all(self.catalog.is_visible(document, version) for document, version in expected):
            raise ValueError("shared_publication_ineligible")
        for sample in samples:
            self._document_span(sample)
        previous = read_pointer(self.root)
        if previous != self.generation or previous != job["prior"]:
            raise RuntimeError("shared_pointer_changed")
        write_pointer(self.root, previous, "previous")
        write_pointer(self.root, job["generation"])
        # Requests and dispatch stay gated until both runtime and journal agree.
        self.journal.transition(job, "active")
        self._set_active(handle, job["generation"])

    def _set_active(self, handle, generation):
        previous = self.handle
        self.handle, self.generation = handle, generation
        self.unavailable = False
        if previous is not None and previous is not handle:
            try:
                previous.close()
            except Exception as error:
                logger.warning("Previous shared handle could not close: %s", type(error).__name__)

    def _reconcile_activation(self, job_id, handle, error):
        job = self.journal.job(job_id)
        if job is None:
            raise RuntimeError("shared_build_missing_after_gate")
        if job["state"] == "active" and read_pointer(self.root) == job["generation"]:
            # SQLite may have committed before the caller observed an error.
            self._set_active(handle, job["generation"])
            return
        if job["state"] != "maintenance" or job["prior"] != self.generation:
            raise RuntimeError("shared_build_state_conflict")
        self.journal.transition(job, "rolling_back", error=type(error).__name__)
        write_pointer(self.root, job["prior"])
        self.journal.transition({**job, "state": "rolling_back"}, "failed", error=type(error).__name__)

    def recover(self):
        # Called before accepting traffic. An uncommitted pointer switch is rolled back.
        interrupted = self.journal.pending(_ACTIVATING)
        if interrupted:
            write_pointer(self.root, interrupted["prior"])
            self.journal.transition(interrupted, "recovered", error="interrupted_activation")
        incomplete = self.journal.pending(("building",))
        if incomplete:
            self.journal.transition(incomplete, "queued", error="interrupted_build")
        self.generation = read_pointer(self.root)
        self.handle = self._load(self.generation)

    async def step(self):
        job = self.journal.pending()
        if job is None:
            return
        if job["state"] in _ACTIVATING:
            # A prior journal transaction can commit even when its caller sees an
            # error. This runner is serialized, so no activation writer is live.
            if job["prior"] != self.generation:
                self.unavailable = True
                raise RuntimeError("shared_build_recovery_conflict")
            try:
                write_pointer(self.root, job["prior"])
                self.journal.transition(job, "recovered", error="interrupted_activation")
                self.unavailable = False
            finally:
                self.activity.release()
                if self._candidate is not None:
                    _, handle, _ = self._candidate
                    self._candidate = None
                    if handle is not self.handle:
                        handle.close()
            return
        if job["state"] == "building":
            self.journal.transition(job, "queued", error="interrupted_build")
            return
        if self._preflight_thread is not None and self._preflight_thread.is_alive():
            # A timed-out native read may still own model or index resources.
            return
        if job["state"] == "queued":
            self.journal.transition(job, "building")
            job = self.journal.job(job["id"])
            try:
                generation = await self._run_host_work(self._build, job)
            except Exception as error:
                self.journal.transition(job, "failed", error=type(error).__name__)
            else:
                self.journal.transition(job, "waiting_for_idle", generation=generation)
            return
        if job["state"] != "waiting_for_idle":
            raise RuntimeError("shared_build_requires_startup_recovery")
        if self._candidate is None or self._candidate[0] != job["id"]:
            try:
                candidate = await self._check_candidate(job["generation"])
            except Exception as error:
                self.journal.transition(job, "failed", error=type(error).__name__)
                return
            self._candidate = (job["id"], *candidate)
        if not self.activity.try_gate(self.counts, lambda: self.journal.transition(
                job, "maintenance", prior=self.generation)):
            if time.time() - job["updated"] >= 86400 and self.journal.defer_notice(job):
                logger.warning("Shared corpus update has been waiting for an idle window for 24 hours")
            return
        job_id, handle, samples = self._candidate
        try:
            job = self.journal.job(job_id)
            if job is None or job["state"] != "maintenance":
                raise RuntimeError("shared_build_missing_after_gate")
            # Health querying has completed; gate time covers the source rechecks
            # and the durable switch.
            await self._run_host_work(self._publish, job, handle, samples)
        except Exception as error:
            try:
                self._reconcile_activation(job_id, handle, error)
            except Exception as rollback_error:
                self.unavailable = True
                logger.error("Shared corpus rollback could not be committed: %s",
                             type(rollback_error).__name__)
        finally:
            self._candidate = None
            self.activity.release()
            if handle is not self.handle:
                handle.close()

    @staticmethod
    async def _run_host_work(function, *args):
        # Cancellation must not release the process lock while a writer runs.
        work = asyncio.create_task(asyncio.to_thread(function, *args))
        try:
            return await asyncio.shield(work)
        except asyncio.CancelledError:
            await asyncio.gather(work, return_exceptions=True)
            raise

    async def run(self, queue_wake):
        from .orchestrator import maybe_build, process_one

        self.loop = asyncio.get_running_loop()
        next_review = 0.0
        while True:
            self.wake.clear()
            try:
                await self.step()
                queue_wake.set()
                if time.monotonic() >= next_review:
                    next_review = time.monotonic() + 60
                    await self._run_host_work(process_one, self)
                    await self._run_host_work(maybe_build, self)
            except Exception as error:
                logger.warning("Shared corpus maintenance delayed: %s", type(error).__name__)
            try:
                interval = 5 if self.journal.pending() else 60
                await asyncio.wait_for(self.wake.wait(), timeout=interval)
            except TimeoutError:
                pass

    def status(self):
        job = self.journal.pending()
        if self.unavailable:
            state = "unavailable"
        elif self.activity.gated:
            state = "maintenance"
        elif job:
            state = job["state"]
        else:
            state = "active" if self.generation else "idle"
        return {"state": state, "message": MAINTENANCE_MESSAGE if self.activity.gated else "",
                "started_at": job["started"] if job else None,
                "active_generation_label": self.generation,
                "retry_after_sec": RETRY_AFTER if self.activity.gated else 0}


def start_runtime():
    """Use a persistent-volume lock to enforce one participating Web process."""
    global _runtime
    if os.getenv("CITRUS_SHARED_CORPUS") != "single-web":
        return None
    import fcntl
    from src.config import settings
    root = Path(settings.DATA_DIR) / CORPUS_ID
    if root.is_symlink():
        raise ValueError("shared_corpus_root_link_denied")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(root / ".web.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        runtime = SharedCorpusRuntime(root)
        try:
            runtime.recover()
        except Exception as error:
            runtime.unavailable = True
            logger.warning("Shared corpus unavailable at startup: %s", type(error).__name__)
        runtime.lock_file = descriptor
        _runtime = runtime
        return runtime
    except BaseException:
        os.close(descriptor)
        raise


def stop_runtime(runtime):
    global _runtime
    if runtime is not None:
        _runtime = None
        try:
            if runtime._candidate is not None and runtime._candidate[1] is not runtime.handle:
                runtime._candidate[1].close()
            if runtime.handle is not None:
                runtime.handle.close()
        finally:
            os.close(runtime.lock_file)
