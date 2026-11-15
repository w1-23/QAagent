"""Validate persisted post-turn navigation before hiding any raw history."""
from __future__ import annotations
import json


def structured_checkpoint(text):
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) and data.get("schema") == "citrus-turn-checkpoint/1" else None
    except (ValueError, TypeError):
        return None


def validate_checkpoint(data, manager, session_id, cutoff):
    try:
        from src.guardrails.turn_checkpoint import validate_pairs
        from src.core import tool_results
        if data.get("navigation_authoritative") is not False:
            return False
        status = data["host_task_status"]
        if status not in {"completed", "partial"} or data["host_task_state"]["turn_outcome"]["status"] != status:
            return False
        sources = manager.get_checkpoint_sources(session_id, cutoff)
        inventory = [{key: row[key] for key in ("source_id", "role", "total_chars")} for row in sources]
        if inventory != data["source_inventory"] or not sources or int(sources[-1]["source_id"]) != cutoff:
            return False
        call_ids = validate_pairs(sources)
        if call_ids != data["tool_result_call_ids"]:
            return False
        for call_id in call_ids:
            row = tool_results.fetch(call_id)
            if (not row or row.get("session_id") != session_id or not row.get("pinned")
                    or not tool_results.storage_integrity(row)["complete"]):
                return False
        if not isinstance(data["exact_excerpts"], list):
            return False
        for excerpt in data["exact_excerpts"]:
            source_id, start, end = excerpt["source_id"], excerpt["start_char"], excerpt["end_char"]
            if (source_id not in {r["source_id"] for r in sources} or type(start) is not int
                    or type(end) is not int or not 0 <= start < end or end - start > 2000):
                return False
            original = manager.get_context_source(session_id, int(source_id), start, end - start)
            if original["text"] != excerpt["quote"]:
                return False
        return True
    except (KeyError, TypeError, ValueError, LookupError, PermissionError):
        return False
