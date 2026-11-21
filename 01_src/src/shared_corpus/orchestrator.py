"""Bounded processing of originals already acquired by authorized Fetch."""
import time

REVIEW_TTL_SEC = 60 * 86400
DAILY_PREPARE_LIMIT = 24
BATCH_DOCUMENTS = 5
BATCH_WAIT_SEC = 300
MAX_PENDING = 100


def schedule(runtime, candidate_id, owner, *, verified_license=False):
    now = time.time()
    with runtime.journal.connection() as conn:
        conn.execute("BEGIN IMMEDIATE")
        pending = conn.execute("SELECT count(*) FROM shared_preparations WHERE state='queued'").fetchone()[0]
        if pending >= MAX_PENDING:
            return
        conn.execute("INSERT INTO shared_preparations "
                     "(candidate_id,owner,state,created,verified_license) VALUES (?,?,'queued',?,?) "
                     "ON CONFLICT(candidate_id) DO UPDATE SET owner=excluded.owner,state='queued',"
                     "created=excluded.created,finished=NULL,verified_license=1 "
                     "WHERE excluded.verified_license>shared_preparations.verified_license",
                     (candidate_id, owner, now, int(verified_license)))
    runtime.notify()


def process_one(runtime):
    """At most one candidate per pass. The catalog operations are idempotent."""
    from src.auth import user_by_id

    now = time.time()
    with runtime.journal.connection() as conn:
        conn.execute("UPDATE shared_preparations SET state='expired',finished=? "
                     "WHERE state='queued' AND created<?", (now, now - REVIEW_TTL_SEC))
        completed = conn.execute("SELECT count(*) FROM shared_preparations WHERE finished>? "
                                 "AND state IN ('prepared','failed')", (now - 86400,)).fetchone()[0]
        if completed >= DAILY_PREPARE_LIMIT:
            return
        row = conn.execute("SELECT candidate_id,owner,verified_license FROM shared_preparations WHERE state='queued' "
                           "ORDER BY created,candidate_id LIMIT 1").fetchone()
    if row is None:
        return
    candidate, owner, verified_license = row
    user = user_by_id(owner)
    state = "expired"
    if user and user.get("active") and user.get("approval_status") == "approved":
        try:
            proposal = runtime.catalog.propose(candidate, owner)
            prepared = runtime.catalog.prepare(proposal["proposal_id"], owner)
            state = "failed" if prepared["state"] == "failed" else "prepared"
        except (LookupError, ValueError, OSError):
            state = "failed"
    with runtime.journal.connection() as conn:
        conn.execute("UPDATE shared_preparations SET state=?,finished=? WHERE candidate_id=? "
                     "AND state='queued' AND verified_license=?", (state, now, candidate, verified_license))


def maybe_build(runtime):
    if runtime.journal.pending():
        return
    # Automatic batches use the same deduplication and daily limits as manual ones.
    authorized = runtime.catalog.publishable()
    fresh = [item for item in authorized if item["version_id"] not in runtime.journal.active_versions(
        runtime.generation, item["document_id"])]
    if not fresh or len(authorized) > 100:
        return
    # A lone new document is published after a short batching interval.
    from datetime import datetime
    oldest = min(datetime.fromisoformat(item["prepared_at"]).timestamp() for item in fresh)
    if len(fresh) < BATCH_DOCUMENTS and time.time() - oldest < BATCH_WAIT_SEC:
        return
    try:
        runtime.enqueue_build(0, authorized)
    except ValueError:
        return
