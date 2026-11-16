"""Optional public task plans on the existing capability/status event pipeline.

Plans describe the agent's work; they grant no execution rights and do not run
steps. There is no extra planner, scheduler, storage table or mandatory plan.
"""
from __future__ import annotations

import json
from typing import Literal

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from src.core.public_progress import sanitize


class TaskPlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")
    title: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "in_progress", "completed", "blocked", "cancelled"]

    @field_validator("title")
    @classmethod
    def public_title(cls, value):
        title = sanitize(value)
        if not title:
            raise ValueError("task_plan_title_not_public")
        return title


class TaskPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    steps: list[TaskPlanStep] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def coherent_steps(self):
        if len({step.id for step in self.steps}) != len(self.steps):
            raise ValueError("task_plan_duplicate_step_id")
        if sum(step.status == "in_progress" for step in self.steps) > 1:
            raise ValueError("task_plan_multiple_active_steps")
        return self


@tool(args_schema=TaskPlanUpdate, response_format="content_and_artifact")
async def update_task_plan(steps: list[TaskPlanStep]):
    """Publish or replace the complete public plan for the current task.

    Use only when a task benefits from several steps; simple questions need no
    plan. Send every step on updates, retaining stable ids and completed work.
    Keep at most one step in_progress. Titles are short public actions, never
    private reasoning, credentials or a final answer. The host supplies plan_id.
    A plan records intent/progress; marking completed does not prove delivery.
    """
    from src.core import jobs, tracing
    from src.core.progress_bus import emit_status

    # Also validate direct calls; normal capability dispatch uses the same schema.
    update = TaskPlanUpdate(steps=steps)
    job = jobs.get_job(tracing.get_job_id())
    if (not job or type(tracing.get_user_id()) is not int or tracing.get_user_id() <= 0
            or job["owner_user_id"] != tracing.get_user_id()
            or job["session_id"] != tracing.get_session_id()
            or job["status"] not in {"running", "detached"}):
        raise PermissionError("task_plan_scope_unavailable")
    payload = {"plan_id": job["job_id"], "steps": [step.model_dump() for step in update.steps]}
    # DurableEventQueue persists this exact status event before notification.
    # Its seq is the authoritative version/order, including after reconnect.
    emit_status("task_plan", **payload)
    return json.dumps(payload, ensure_ascii=False), {
        "_tool_outcome": {"status": "complete", "reason_code": "task_plan_updated"}}


PLAN_TOOL_NAMES = frozenset({update_task_plan.name})


def register_plan_tools(catalog):
    """Register code-owned schema now and its stateless implementation on dispatch."""
    from .capabilities import CapabilityDefinition

    def load():
        from src.tools.registry import register_tool
        # Public metadata only: no external/artifact write or recovery executor.
        register_tool(update_task_plan, category="general", is_readonly=True,
                      recovery_mode="never")
        return update_task_plan

    catalog.register(CapabilityDefinition(update_task_plan.name, update_task_plan.description,
        update_task_plan.args_schema.model_json_schema(), load, execution_mode="sequential"))
