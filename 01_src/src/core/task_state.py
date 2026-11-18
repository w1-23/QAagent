"""Read-only model access to host task facts in the existing event journal."""
from __future__ import annotations

import json


def publish(job_id, contract, coverage):
    if not job_id:
        return
    from src.core.task_events import append_event
    append_event(job_id, "task_state", {"schema_version": 1,
        "task_contract": contract.as_dict(), "coverage": coverage.as_dict(),
        "remaining": coverage.gaps(contract)})


def current(owner: int, session_id: str, job_id: str) -> dict:
    from src.core.task_events import transaction, _job, TaskConflict
    if type(owner) is not int or owner <= 0 or not session_id or not job_id:
        raise TaskConflict("task_state_unavailable")
    with transaction() as conn:
        job = _job(conn, job_id)
        if job["owner_user_id"] != owner or job["session_id"] != session_id:
            raise TaskConflict("task_state_unavailable")
        row = conn.execute("SELECT seq,data FROM task_events WHERE job_id=? AND event='task_state' ORDER BY seq DESC LIMIT 1",
                           (job_id,)).fetchone()
        snapshot = json.loads(row["data"]) if row is not None else None
        if snapshot is not None:
            # The current request already contains the full user input. A status
            # query need not reintroduce it; the persisted contract remains intact.
            snapshot["task_contract"].pop("query", None)
        operations = []
        for operation in conn.execute("SELECT operation_id,tool_name,status,outcome,result_refs FROM task_operations WHERE job_id=? ORDER BY updated_at", (job_id,)):
            operations.append({"operation_id": operation["operation_id"], "tool_name": operation["tool_name"],
                               "status": operation["status"], "outcome": json.loads(operation["outcome"]),
                               "result_refs": json.loads(operation["result_refs"])})
        return {"job_id": job_id, "snapshot_seq": row["seq"] if row is not None else None,
                "status": job["status"],
                "snapshot_scope": "last_completed_tool_batch" if row is not None else "durable_operations",
                "state": snapshot, "operations": operations,
                "notice": ("宿主记录的已执行事实；读取不重放操作。缺口以真实覆盖为准，不以模型建议为完成证明。"
                           if row is not None else
                           "当前任务状态与持久化操作回执；读取不重放操作。无旧版任务快照，不推断任务契约、覆盖或剩余义务。")}
