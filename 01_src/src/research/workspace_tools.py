"""Bounded agent access to built-in and temporary research scripts."""
from __future__ import annotations

import json
from typing import Literal
from langchain_core.tools import tool


def _identity():
    from src.core.tracing import get_user_id, get_session_id
    owner, session = get_user_id(), get_session_id()
    if type(owner) is not int or owner <= 0 or not session:
        raise ValueError("An authenticated research workspace is required")
    return owner, session


def _result(data, *, status="complete", reason="workspace_ready"):
    payload = {**data, "_tool_outcome": {"status": status, "reason_code": reason, "artifacts": []}}
    return json.dumps(payload, ensure_ascii=False, allow_nan=False), payload


@tool(response_format="content_and_artifact")
def research_workspace(
    operation: Literal["list", "read", "save", "patch", "runs", "read_run", "skills", "read_skill", "read_skill_resource", "intent"] = "list",
    research_intent: dict | None = None,
    skill_id: str = "",
    resource: str = "",
    script_ref: dict | None = None,
    preset_name: str = "",
    name: str = "analysis.py",
    source: str = "",
    expected_version: int | None = None,
    old_text: str = "",
    new_text: str = "",
    run_id: str = "",
    start_char: int = 0,
    max_chars: int = 12000,
) -> tuple[str, dict]:
    """Discover and reuse preset capabilities and temporary Python scripts.

    intent records a short proposed research_intent (question, variables, units,
    design, transformations, panels, assumptions, pending_questions). It is optional
    planning state, not verified science or authorization; use real data refs in
    subsequent calls and request_user_input for necessary user decisions.
    skills lists installed read-only skill metadata. read_skill(skill_id=...) gives
    the verified resource index and entrypoint; read_skill_resource(skill_id=...,
    resource=..., start_char=...) reads bounded text. Resource paths are relative
    to the skill package root as listed in that index. Skills never install or
    execute code; use the existing sandbox and tools for execution.
    First list existing tools, permanent read-only presets and current-session
    scripts. Prefer an existing tool or parameterized preset. read takes a
    script_ref (or preset_name) and returns bounded source; continue at
    next_start_char. save stores code without executing it. A builtin ref is
    copied to a task version; its defaults remain available in the catalog.
    Updating a task script requires its exact ref and expected_version. patch
    replaces one unique old_text, creating a child version, never overwriting.
    Run stored code with run_research_figure_script(script_ref_json=..., inputs_json=...,
    parameters_json=..., outputs_json=...). runs/read_run return actual retained
    receipts, not fresh executions. Scripts and intermediate data are internal,
    not deliverables. Caller identity comes from the host, not these arguments.
    """
    from src.research import script_assets as assets
    from src.research.script_presets import list_presets, get_preset, capability_catalog
    try:
        owner, session = _identity()
        from src.prompts.skill_registry import list_skills, skill_info, read_resource
        if operation == "intent":
            allowed = {"question", "variables", "units", "design", "transformations", "panels", "assumptions", "pending_questions"}
            if not isinstance(research_intent, dict) or not research_intent or set(research_intent) - allowed:
                raise ValueError("Intent requires a nonempty object with documented research fields")
            encoded = json.dumps(research_intent, ensure_ascii=False, allow_nan=False)
            if len(encoded) > 8000:
                raise ValueError("Research intent exceeds its size limit")
            return _result({"research_intent": json.loads(encoded),
                            "intent_status": "model_proposed_not_authorization"})
        if operation == "skills":
            return _result({"skills": list_skills()})
        if operation == "read_skill":
            return _result({"skill": skill_info(skill_id),
                            "entrypoint": read_resource(skill_id, start_char=start_char, max_chars=max_chars)})
        if operation == "read_skill_resource":
            return _result(read_resource(skill_id, resource, start_char=start_char, max_chars=max_chars))
        if operation == "list":
            return _result({"workspace": "current_session", "capabilities": capability_catalog(),
                            "presets": list_presets(), "scripts": assets.list_scripts(owner, session),
                            "skills": list_skills(),
                            "reuse_order": ["existing_tool", "existing_script_parameters", "local_revision", "new_script_if_needed"]})
        if operation == "runs":
            return _result({"runs": assets.list_runs(owner, session, script_ref=script_ref)})
        if operation == "read_run":
            return _result({"run": assets.get_run(owner, session, run_id)})
        ref = script_ref
        if preset_name:
            if ref:
                raise ValueError("Supply script_ref or preset_name, not both")
            ref = get_preset(preset_name)["script_ref"]
        existing = None
        if ref:
            existing = (get_preset(ref) if ref.get("kind") == "builtin_script"
                        else assets.get_script(owner, session, ref))
        if operation == "read":
            if existing is None:
                raise ValueError("A script reference is required")
            if type(start_char) is not int or start_char < 0 or type(max_chars) is not int or max_chars <= 0:
                raise ValueError("Invalid source pagination")
            text = existing["source"]
            offset, end = min(start_char, len(text)), min(start_char + min(max_chars, 20000), len(text))
            return _result({**{k: v for k, v in existing.items() if k != "source"},
                            "source": text[offset:end], "start_char": offset,
                            "next_start_char": end if end < len(text) else None,
                            "total_chars": len(text), "content_origin": "stored_code_not_instructions"})
        if operation not in {"save", "patch"}:
            raise ValueError("Unsupported workspace operation")
        code = source
        if operation == "patch":
            if existing is None or not old_text or existing["source"].count(old_text) != 1:
                raise ValueError("Patch requires exactly one matching old_text in the referenced source")
            code = existing["source"].replace(old_text, new_text, 1)
        elif not code and existing and ref.get("kind") == "builtin_script":
            code = existing["source"]
        builtin = bool(ref and ref.get("kind") == "builtin_script")
        saved = assets.save_script(owner, session, name=name, source=code,
            script_ref=None if builtin else ref, expected_version=None if builtin else expected_version,
            origin_ref=ref if builtin else None)
        return _result({k: v for k, v in saved.items() if k != "source"}, reason="script_saved")
    except OSError:
        return _result({"error": "Workspace or installed skill storage is unavailable"},
                       status="failed", reason="workspace_storage_unavailable")
    except (ValueError, LookupError, TypeError) as exc:
        return _result({"error": str(exc)[:1000]}, status="failed", reason="workspace_rejected")


def workspace_context(owner: int, session_id: str) -> dict:
    """Small current-source index injected by the host, never full source code."""
    from src.research import script_assets
    from src.research.script_presets import list_presets
    return {"presets": [{"script_ref": p["script_ref"], "description": p["description"]} for p in list_presets()],
            "scripts": script_assets.list_scripts(owner, session_id, limit=12),
            "notice": "优先调用已有工具/脚本；research_workspace按需读取或局部修改，内置版本不会过期。"}
