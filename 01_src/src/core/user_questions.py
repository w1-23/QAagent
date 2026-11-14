"""Host-owned user questions, durable answers and fresh continuation jobs.

Questions are independent of a turn's immutable done frame. No suspended graph,
account lease, tool invocation or model request is kept alive while awaiting input.
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr, model_validator

from src.core.task_events import TaskConflict, _append, _job, canonical, input_hash, transaction


class QuestionConflict(TaskConflict):
    """HTTP adapters map this to 409; missing owner/session maps to 404."""


class DatasetIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: StrictStr
    id: StrictStr
    version: StrictInt
    sha256: StrictStr


class FieldReference(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    kind: StrictStr
    dataset_ref: DatasetIdentity | None
    file_id: StrictStr | None
    sha256: StrictStr | None
    sheet: StrictStr | None
    column: StrictStr

    @model_validator(mode="after")
    def complete_reference(self):
        if not self.column.strip():
            raise ValueError("column must name a real schema field")
        if self.kind == "dataset_field":
            if self.dataset_ref is None or self.dataset_ref.kind != "dataset_snapshot":
                raise ValueError("dataset_field requires a dataset_snapshot identity")
            if any(x is not None for x in (self.file_id, self.sha256, self.sheet)):
                raise ValueError("dataset_field cannot contain source identity")
        elif self.kind == "source_field":
            if self.dataset_ref is not None or not self.file_id or not self.sha256 or self.sheet is None:
                raise ValueError("source_field requires file_id, sha256 and sheet")
        else:
            raise ValueError("unknown field reference kind")
        return self


class QuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: StrictStr = Field(min_length=1, max_length=64)
    label: StrictStr = Field(min_length=1, max_length=200)
    description: StrictStr = Field(max_length=1000)
    refs: list[FieldReference] = Field(max_length=20)


class UserQuestionInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt: StrictStr = Field(min_length=1, max_length=2000)
    options: list[QuestionOption] = Field(max_length=8)
    multi_select: StrictBool
    allow_text: StrictBool

    @model_validator(mode="after")
    def choices(self):
        if not self.prompt.strip() or (not self.options and not self.allow_text):
            raise ValueError("question needs a prompt and a way to answer")
        if len({x.id for x in self.options}) != len(self.options):
            raise ValueError("option ids must be unique")
        return self


class QuestionAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    version: StrictInt = Field(ge=1)
    selected_option_ids: list[StrictStr] = Field(default_factory=list, max_length=8)
    text: StrictStr = Field(default="", max_length=8000)
    cancelled: StrictBool = False
    client_request_id: StrictStr = Field(min_length=1, max_length=64)


def init_schema(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS task_questions (
        id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, owner_user_id INTEGER NOT NULL,
        session_id TEXT NOT NULL, version INTEGER NOT NULL, status TEXT NOT NULL,
        question TEXT NOT NULL, context TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '',
        answer_hash TEXT NOT NULL DEFAULT '', client_request_id TEXT NOT NULL DEFAULT '',
        child_job_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
        expires_at REAL NOT NULL, pin_until REAL NOT NULL DEFAULT 0)""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_task_questions_expiry ON task_questions(status,expires_at)")


def _authorized(conn, job_id, owner_user_id, session_id):
    job = _job(conn, job_id)
    if type(owner_user_id) is not int or owner_user_id <= 0 or job["owner_user_id"] != owner_user_id or job["session_id"] != session_id:
        raise QuestionConflict("question_not_found")
    return job


def _view(row):
    return json.loads(row["question"]) | {"id": row["id"], "job_id": row["job_id"],
        "version": row["version"], "status": row["status"], "expires_at": row["expires_at"],
        "child_job_id": row["child_job_id"],
        "answer": json.loads(row["answer"]) if row["answer"] else None}


def _validate_refs(options, owner, context):
    """Only schema identities loaded by the host may authorize field options."""
    trusted = (context or {}).get("source_refs") or []
    for option in options:
        for ref in option["refs"]:
            if ref["kind"] == "dataset_field":
                from src.research.analysis_results import load_dataset_snapshot
                snapshot = load_dataset_snapshot(owner, ref["dataset_ref"])
                if ref["column"] not in snapshot.to_dict()["columns"]:
                    raise ValueError("field_not_in_dataset_schema")
            else:
                if not any(source.get("file_id") == ref["file_id"]
                           and source.get("sha256") == ref["sha256"]
                           and str(source.get("sheet", "")) == ref["sheet"]
                           and ref["column"] in source.get("columns", [])
                           for source in trusted if isinstance(source, dict)):
                    raise ValueError("field_not_in_trusted_source_schema")
                from src.session.files import get_account_file
                # The exact identity/schema was authorized by host refs above;
                # a model-supplied internal file_id alone never reaches this read.
                source = get_account_file(ref["file_id"], owner, allow_internal=True)
                if not source or source.get("sha256") != ref["sha256"]:
                    raise ValueError("source_version_changed")


def _script_refs(context):
    refs = list(context.get("script_refs") or [])
    for ref in (context.get("context_working_state") or {}).get("script_refs") or []:
        if ref not in refs:
            refs.append(ref)
    return refs


def _source_refs(context):
    """The same host-carried file identities must be validated and pinned."""
    working = context.get("context_working_state") or {}
    return (list(context.get("source_refs") or [])
            + list(context.get("attachment_refs") or [])
            + list(working.get("source_refs") or []))


def _has_inputs(context):
    return bool(_source_refs(context) or context.get("dataset_refs") or _script_refs(context)
                or (context.get("context_working_state") or {}).get("dataset_refs"))


def _pin_scripts(identity, owner, session_id, context, previous_refs=()):
    from src.research import script_assets
    refs = _script_refs(context)
    # Validate the complete host-scoped set before adding any new dependency.
    for ref in refs:
        script_assets.get_script(owner, session_id, ref)
    added = []
    try:
        for ref in refs:
            script_assets.pin_script(owner, session_id, ref, "user_question", identity)
            if ref not in previous_refs:
                added.append(ref)
    except BaseException:
        for ref in added:
            script_assets.release_script_pin(owner, session_id, ref, "user_question", identity)
        raise


def _validate_context_inputs(owner, context):
    """Recheck host-carried identities, never file IDs supplied in model options."""
    import hashlib
    from src.session.files import get_account_file
    working = context.get("context_working_state") or {}
    refs = _source_refs(context)
    checked = set()
    for ref in refs:
        if not isinstance(ref, dict) or not ref.get("file_id") or str(ref["file_id"]).startswith("upload:"):
            continue
        identity = (ref["file_id"], ref.get("sha256"))
        if identity in checked:
            continue
        checked.add(identity)
        # Context is captured by the host, not accepted from question arguments.
        # Private snapshots need the same owner, immutable hash and byte check.
        entry = get_account_file(ref["file_id"], owner, allow_internal=True)
        if not entry or not ref.get("sha256") or entry.get("sha256") != ref["sha256"]:
            raise ValueError("source_version_changed")
        try:
            with open(entry["path"], "rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
        except OSError as exc:
            raise ValueError("source_unavailable") from exc
        if digest != ref["sha256"]:
            raise ValueError("source_version_changed")
    datasets = list(context.get("dataset_refs") or []) + list(working.get("dataset_refs") or [])
    if datasets:
        from src.research.analysis_results import load_dataset_snapshot
        for ref in datasets:
            load_dataset_snapshot(owner, ref)


def _pin_inputs(identity, owner, context, lease=None, *, session_id=""):
    _validate_context_inputs(owner, context)
    refs = _source_refs(context)
    file_ids = list(dict.fromkeys(r["file_id"] for r in refs if isinstance(r, dict)
        and r.get("file_id") and not str(r["file_id"]).startswith("upload:")))
    datasets = list(context.get("dataset_refs") or [])
    for ref in (context.get("context_working_state") or {}).get("dataset_refs") or []:
        if ref not in datasets:
            datasets.append(ref)
    if not (file_ids or datasets or _script_refs(context)):
        return
    if lease is not None and lease.owner != owner:
        raise ValueError("question pin owner mismatch")
    if file_ids or datasets:
        from src.research.analysis_results import pin_dataset_references
        # One atomic replacement includes payloads, every parent and source.
        pin_dataset_references(owner, "user_question", identity, datasets, file_ids=file_ids)
    _pin_scripts(identity, owner, session_id, context)
    if lease is not None:
        try:
            lease.add_finalizer(lambda: _release_pin(identity, owner, context, session_id=session_id))
        except BaseException:
            _release_pin(identity, owner, context, session_id=session_id)
            raise


def _release_pin(identity, owner, context=None, *, session_id=""):
    from src.session.files import release_resource_references
    from src.research import script_assets
    # Each store transaction closes before entering the next one. Release does
    # not admit new account work, so deletion/lease finalizers can drain safely.
    for ref in _script_refs(context or {}):
        try:
            script_assets.release_script_pin(owner, session_id, ref, "user_question", identity)
        except LookupError:
            pass  # The account/script may already have been purged.
    release_resource_references(owner, "user_question", identity)


def pin_continuation_inputs(child_job_id, *, owner_user_id, session_id, lease):
    """Host binds pins to the fresh execution lease before releasing question pins."""
    with transaction() as conn:
        _authorized(conn, child_job_id, owner_user_id, session_id)
        row = conn.execute("SELECT * FROM task_questions WHERE child_job_id=?", (child_job_id,)).fetchone()
        if row is None or row["pin_until"] <= time.time():
            raise QuestionConflict("continuation_inputs_expired")
        context = json.loads(row["context"])
        _pin_inputs("child:" + child_job_id, owner_user_id, context, lease, session_id=session_id)
        if _has_inputs(context):
            _release_pin(row["id"], owner_user_id, context, session_id=session_id)
        conn.execute("UPDATE task_questions SET pin_until=0 WHERE id=?", (row["id"],))


def create_question(job_id, *, owner_user_id, session_id, prompt, options,
                    multi_select=False, allow_text=True, context=None, ttl_seconds=86400):
    data = UserQuestionInput.model_validate(dict(prompt=prompt, options=options,
        multi_select=multi_select, allow_text=allow_text)).model_dump()
    context = dict(context or {})
    _validate_refs(data["options"], owner_user_id, context)
    datasets = list(context.get("dataset_refs") or [])
    for option in data["options"]:
        for ref in option["refs"]:
            if ref["kind"] == "dataset_field" and ref["dataset_ref"] not in datasets:
                datasets.append(ref["dataset_ref"])
    context["dataset_refs"] = datasets
    ttl_seconds = min(86400, max(60, int(ttl_seconds)))
    now, identity = time.time(), uuid.uuid4().hex
    pin_attempted = False
    try:
        with transaction() as conn:
            job = _authorized(conn, job_id, owner_user_id, session_id)
            existing = conn.execute("SELECT * FROM task_questions WHERE job_id=?", (job_id,)).fetchone()
            if existing:
                if json.loads(existing["question"]) != data:
                    raise QuestionConflict("question_already_exists")
                return _view(existing)
            if job["status"] not in {"running", "detached"} or conn.execute(
                    "SELECT 1 FROM task_events WHERE job_id=? AND event='done'", (job_id,)).fetchone():
                raise QuestionConflict("task_not_running")
            conn.execute("INSERT INTO task_questions(id,job_id,owner_user_id,session_id,version,status,question,context,created_at,expires_at,pin_until) VALUES (?,?,?,?,1,'pending',?,?,?,?,?)",
                (identity, job_id, owner_user_id, session_id, canonical(data), canonical(context), now, now+ttl_seconds, now+ttl_seconds))
            row = conn.execute("SELECT * FROM task_questions WHERE id=?", (identity,)).fetchone()
            question = _view(row)
            pin_attempted = _has_inputs(context)
            _pin_inputs(identity, owner_user_id, context, session_id=session_id)
            record = _append(conn, job_id, "request_user_input", {"question": question})
    except BaseException:
        if pin_attempted:
            _release_pin(identity, owner_user_id, context, session_id=session_id)
        raise
    # Notify only after the transaction commits. Reuse the persisted sequence
    # instead of appending the same fact a second time through DurableEventQueue.
    from src.core.progress_bus import get_progress_queue, _encode_event
    queue = get_progress_queue()
    if getattr(queue, "job_id", None) == job_id:
        queue.put_nowait(_encode_event(record["event"], record["data"]) | {"id": str(record["seq"])})
    return question


def save_question_context(job_id, context):
    """Called at the graph pause boundary, after all paired tool receipts exist."""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM task_questions WHERE job_id=? AND status='pending'", (job_id,)).fetchone()
        if row is None:
            raise QuestionConflict("pending_question_not_found")
        previous = json.loads(row["context"])
        context = dict(context)
        datasets = list(context.get("dataset_refs") or [])
        for ref in previous.get("dataset_refs") or []:
            if ref not in datasets:
                datasets.append(ref)
        context["dataset_refs"] = datasets
        scripts = _script_refs(context)
        previous_scripts = _script_refs(previous)
        for ref in previous_scripts:
            if ref not in scripts:
                scripts.append(ref)
        context["script_refs"] = scripts
        encoded_context = canonical(context)
        _pin_scripts(row["id"], row["owner_user_id"], row["session_id"], context,
                     previous_refs=previous_scripts)
        conn.execute("UPDATE task_questions SET context=? WHERE job_id=? AND status='pending'",
                     (encoded_context, job_id))


def list_questions(job_id, *, owner_user_id, session_id):
    expire_questions()
    with transaction() as conn:
        _authorized(conn, job_id, owner_user_id, session_id)
        result = []
        for row in conn.execute("SELECT * FROM task_questions WHERE job_id=?", (job_id,)):
            view = _view(row)
            if row["child_job_id"]:
                child = conn.execute("SELECT status FROM task_jobs WHERE job_id=?", (row["child_job_id"],)).fetchone()
                view["child_status"] = child[0] if child else ""
            result.append(view)
        return result


def answer_question(job_id, question_id, *, owner_user_id, session_id, version,
                    selected_option_ids=None, text="", cancelled=False, client_request_id):
    answer = QuestionAnswer.model_validate(dict(version=version,
        selected_option_ids=selected_option_ids or [], text=text, cancelled=cancelled,
        client_request_id=client_request_id))
    selected = answer.selected_option_ids
    if len(set(selected)) != len(selected) or not answer.client_request_id.strip():
        raise ValueError("invalid_answer")
    value = {"selected_option_ids": sorted(selected), "text": answer.text.strip(), "cancelled": cancelled}
    digest = input_hash(value)
    expire_questions()
    with transaction() as conn:
        job = _authorized(conn, job_id, owner_user_id, session_id)
        row = conn.execute("SELECT * FROM task_questions WHERE id=? AND job_id=?", (question_id, job_id)).fetchone()
        if row is None:
            raise QuestionConflict("question_not_found")
        # Version identifies the prompt; answering never silently changes it.
        if row["version"] != version:
            raise QuestionConflict("question_version_conflict")
        if row["status"] in {"answered", "cancelled"}:
            if row["answer_hash"] != digest:
                raise QuestionConflict("question_answer_conflict")
            child = conn.execute("SELECT status FROM task_jobs WHERE job_id=?", (row["child_job_id"],)).fetchone()
            return {"question": _view(row), "child_job_id": row["child_job_id"], "created": False,
                    "dispatch_required": bool(child and child[0] == "queued")}
        if row["status"] != "pending":
            raise QuestionConflict("question_expired")
        if job["status"] != "awaiting_input":
            raise QuestionConflict("question_turn_not_closed")
        q = json.loads(row["question"])
        allowed = {o["id"] for o in q["options"]}
        if not set(selected) <= allowed or (not q["multi_select"] and len(selected) > 1):
            raise ValueError("invalid_option_selection")
        if value["text"] and not q["allow_text"]:
            raise ValueError("custom_text_not_allowed")
        if cancelled and (selected or value["text"]):
            raise ValueError("cancelled_answer_must_be_empty")
        if not cancelled and not (selected or value["text"]):
            raise ValueError("empty_answer")
        # Cancellation must remain possible if a source has disappeared.
        # A semantic option with no field refs still carries immutable inputs.
        if not cancelled:
            context = json.loads(row["context"])
            _validate_refs(q["options"], owner_user_id, context)
            _validate_context_inputs(owner_user_id, context)
            from src.research.script_assets import get_script
            for ref in _script_refs(context):
                get_script(owner_user_id, session_id, ref)
        child_id = "" if cancelled else uuid.uuid4().hex[:12]
        now = datetime.now().isoformat()
        if child_id:
            conn.execute("INSERT INTO task_jobs(job_id,session_id,request_id,job_type,status,current_step,progress_summary,result_path,error,created_at,updated_at,finished_at,owner_user_id,parent_job_id,question_id,client_request_id) VALUES (?,?,?,'chat','queued','question_continuation','','','',?,?,'',?,?,?,?)",
                (child_id, session_id, uuid.uuid4().hex, now, now, owner_user_id, job_id, question_id, 'continue:' + child_id))
            conn.execute("INSERT INTO task_budgets(job_id,deadline,max_calls,max_tokens,calls,tokens,final_tokens,final_calls) SELECT ?,0,max_calls,max_tokens,calls,tokens,0,0 FROM task_budgets WHERE job_id=?", (child_id, job_id))
        status = "cancelled" if cancelled else "answered"
        conn.execute("UPDATE task_questions SET status=?,answer=?,answer_hash=?,client_request_id=?,child_job_id=? WHERE id=?",
            (status, canonical(value), digest, client_request_id, child_id, question_id))
        if cancelled:
            context = json.loads(row["context"])
            if _has_inputs(context):
                _release_pin(question_id, owner_user_id, context, session_id=session_id)
            conn.execute("UPDATE task_questions SET pin_until=0 WHERE id=?", (question_id,))
        updated = conn.execute("SELECT * FROM task_questions WHERE id=?", (question_id,)).fetchone()
        return {"question": _view(updated), "child_job_id": child_id,
                "created": bool(child_id), "dispatch_required": bool(child_id)}


def get_continuation(child_job_id, *, owner_user_id, session_id):
    """Fresh graph input, never a replay/checkpoint or old tool call execution."""
    with transaction() as conn:
        child = _authorized(conn, child_job_id, owner_user_id, session_id)
        row = conn.execute("SELECT * FROM task_questions WHERE child_job_id=? AND status='answered'", (child_job_id,)).fetchone()
        if row is None:
            raise QuestionConflict("continuation_not_found")
        context, answer, question = json.loads(row["context"]), json.loads(row["answer"]), _view(row)
        labels = [o["label"] for o in question["options"] if o["id"] in answer["selected_option_ids"]]
        original = str(context.get("query") or "")
        # The model-written prompt remains in question_answer/context. Feeding
        # it back as a new user command can invent exports (e.g. “标注” -> XLSX).
        query = original + "\n\n用户对澄清问题的答复：" + "；".join(labels + ([answer["text"]] if answer["text"] else []))
        return {"job_id": child_job_id, "parent_job_id": row["job_id"],
                "session_id": session_id, "request_id": child["request_id"],
                "client_request_id": child["client_request_id"], "user_id": owner_user_id,
                "query": query, "question_answer": {"question": question, "answer": answer},
                "context_working_state": context.get("context_working_state") or {},
                "attachment_refs": context.get("attachment_refs") or [],
                "source_refs": context.get("source_refs") or [],
                "dataset_refs": context.get("dataset_refs") or [],
                "script_refs": _script_refs(context),
                "visualization_enabled": bool(context.get("visualization_enabled")),
                "continuation_context": context, "no_replay": True}


def claim_child_dispatch(child_job_id, *, owner_user_id, session_id, deadline):
    """Atomic queued->running dispatch fence. Caller schedules only on True.

    deadline is epoch seconds for this new execution segment. On synchronous
    scheduler rejection call release_child_dispatch before retrying the POST.
    Once accepted, never release the fence or rerun interrupted writes.
    """
    if deadline <= time.time():
        raise ValueError("new execution deadline required")
    with transaction() as conn:
        _authorized(conn, child_job_id, owner_user_id, session_id)
        cur = conn.execute("UPDATE task_jobs SET status='running',current_step='init',updated_at=? WHERE job_id=? AND status='queued' AND question_id<>''", (datetime.now().isoformat(), child_job_id))
        if cur.rowcount:
            conn.execute("UPDATE task_budgets SET deadline=? WHERE job_id=?", (deadline, child_job_id))
        return bool(cur.rowcount)


def release_child_dispatch(child_job_id, *, owner_user_id, session_id):
    """Only call when scheduler rejected before starting any execution."""
    with transaction() as conn:
        _authorized(conn, child_job_id, owner_user_id, session_id)
        cur = conn.execute("UPDATE task_jobs SET status='queued',current_step='question_continuation' WHERE job_id=? AND status='running' AND current_step='init' AND NOT EXISTS(SELECT 1 FROM task_events WHERE job_id=?)", (child_job_id, child_job_id))
        return bool(cur.rowcount)


def supersede_pending_questions(new_job_id, *, owner_user_id, session_id):
    """A new ordinary chat turn replaces unanswered choices in this conversation.

    Typed question answers use the continuation path and never call this. Only
    older pending questions are invalidated; accepted answers and running work
    keep their recorded identity. Closed parent event streams stay immutable.
    """
    with transaction() as conn:
        job = _authorized(conn, new_job_id, owner_user_id, session_id)
        if job["question_id"]:
            return 0
        rows = conn.execute("""SELECT q.* FROM task_questions q
            JOIN task_jobs j ON j.job_id=q.job_id
            WHERE q.owner_user_id=? AND q.session_id=? AND q.status='pending'
              AND q.job_id<>? AND j.created_at<=?""",
            (owner_user_id, session_id, new_job_id, job["created_at"])).fetchall()
        for row in rows:
            context = json.loads(row["context"])
            if _has_inputs(context):
                _release_pin(row["id"], owner_user_id, context, session_id=session_id)
            conn.execute("UPDATE task_questions SET status='expired',pin_until=0 WHERE id=? AND status='pending'", (row["id"],))
        return len(rows)


def expire_questions(now=None):
    """Host may call periodically; reads/answers also sweep. Bounded DB pins end here."""
    now = time.time() if now is None else now
    with transaction() as conn:
        expired = conn.execute("SELECT * FROM task_questions WHERE pin_until>0 AND pin_until<=?", (now,)).fetchall()
        for row in expired:
            context = json.loads(row["context"])
            if _has_inputs(context):
                _release_pin(row["id"], row["owner_user_id"], context, session_id=row["session_id"])
            conn.execute("UPDATE task_questions SET pin_until=0 WHERE id=?", (row["id"],))
            if row["child_job_id"]:
                conn.execute("UPDATE task_jobs SET status='cancelled',error='continuation_inputs_expired',finished_at=?,updated_at=? WHERE job_id=? AND status='queued'", (datetime.now().isoformat(), datetime.now().isoformat(), row["child_job_id"]))
        cur = conn.execute("UPDATE task_questions SET status='expired',pin_until=0 WHERE status='pending' AND expires_at<=?", (now,))
        return int(cur.rowcount)
