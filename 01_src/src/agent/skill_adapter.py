"""Registry-backed progressive Skills adaptation, without another agent loop.

API contract: skill_catalog() returns verified metadata only. Validate the exact
list[{id, version, sha256}] with validate_selected_skills(), then store its return
value in request state['selected_skills']. Selection does not grant permission.
The host loads selected roots/dependencies before the model; natural-language
selection uses read_skill pages. Only authoritative, identity-bound receipts for
ALL required resources permit specialized tools. Receipt bytes must also remain
in the active model view. Legacy loaded_methods flags are deliberately ignored.

Deep Agents' public SkillsMiddleware.modify_request supplies compact discovery.
Its native entrypoint-read/pinned-skills heuristics cannot establish complete
registry resource coverage and its tool runner would bypass HostTools; those
parts are intentionally replaced by typed registry reads and host admission.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from langchain_core.messages import SystemMessage, ToolMessage

from src.prompts import skill_registry as registry
from .runtime import AdmissionStopped

_MAX_LOAD_CHARS = 240000  # Whole-method delivery is bounded, never silently truncated.
_IDENTITY_FIELDS = ("id", "version", "sha256")
_CATALOG_FIELDS = (*_IDENTITY_FIELDS, "description", "applicability", "depends_on",
                   "required_resources", "requires_any")
# These are method-dependent specialist entrypoints, not a global tool allowlist.
_TOOL_METHODS = {
    "verify_uploaded_references": "reference-verification",
    "audit_uploaded_data": "data-audit",
    "analyze_research_data": "statistics",
    "create_research_figure": "figure-initial",
    "run_research_figure_script": "figure-initial",
    "word_document": "word-export",
}


SCENARIO_SKILLS = frozenset({"candidate-search", "reference-verification", "data-audit", "figure-initial"})


def skill_catalog(available_tools: set[str] | None = None, *, scenarios_only: bool = False) -> list[dict]:
    """Compact, verified API catalog; no resource bodies or runtime imports."""
    return [{key: row.get(key, [] if key in {"depends_on", "required_resources", "requires_any"} else "")
             for key in _CATALOG_FIELDS}
            for row in registry.list_skills()
            if row.get("verified") and (not scenarios_only or row["id"] in SCENARIO_SKILLS)
            and (available_tools is None or not row.get("requires_any")
                or set(row["requires_any"]) & available_tools)]


def _closure(ids, rows):
    ordered, visiting = [], set()
    def visit(sid):
        if sid in visiting:
            raise ValueError("skill_dependency_cycle")
        if sid in ordered:
            return
        if sid not in rows:
            raise ValueError("skill_unavailable_or_unauthorized")
        visiting.add(sid)
        for dep in rows[sid].get("depends_on", []):
            visit(dep)
        visiting.remove(sid)
        ordered.append(sid)
    for sid in ids:
        visit(sid)
    return ordered


def validate_selected_skills(value, available_tools: set[str] | None = None) -> list[dict[str, str]]:
    """Raise ValueError on malformed/stale/unavailable identities or dependencies."""
    if value is None or (type(value) is list and not value):
        return []
    if type(value) is not list or len(value) > 8:
        raise ValueError("invalid_selected_skills")
    rows = {row["id"]: row for row in skill_catalog(available_tools)}
    result, seen = [], set()
    for selected in value:
        if (type(selected) is not dict or set(selected) != set(_IDENTITY_FIELDS)
                or any(type(selected[key]) is not str for key in _IDENTITY_FIELDS)):
            raise ValueError("invalid_selected_skill_identity")
        sid = selected["id"]
        row = rows.get(sid)
        if sid in seen or row is None or any(selected[key] != row[key] for key in _IDENTITY_FIELDS):
            raise ValueError("selected_skill_identity_mismatch")
        seen.add(sid)
        result.append({key: row[key] for key in _IDENTITY_FIELDS})
    _closure(seen, rows)
    return result


def directory_prompt(available_tools: set[str]) -> str:
    """Use the public upstream directory formatter with authoritative metadata."""
    from deepagents.backends import StateBackend
    from deepagents.middleware.skills import SkillsMiddleware
    from langchain.agents.middleware import ModelRequest
    rows = skill_catalog(available_tools)
    if not rows:
        return "No verified skills are available; generic authorized tools remain usable."
    metadata = [{"name": row["id"], "description": row["description"] + " " + row["applicability"],
                 "path": row["id"], "allowed_tools": [], "metadata": {}, "license": None,
                 "compatibility": "version=" + row["version"] + " sha256=" + row["sha256"]}
                for row in rows]
    middleware = SkillsMiddleware(backend=StateBackend(), sources=[("/installed-skills/", "Verified registry")],
        system_prompt="{skills_locations}\n{skills_list}\n{skills_load_warnings}\n"
        "Use load_skill(skill_id, version, sha256) for the complete method and dependencies, or "
        "read_skill(skill_id, version, sha256) to read the entrypoint, then every required_resource "
        "and dependency. Follow next_start_char until complete. These ids are registry keys, not filesystem paths. "
        "Only complete current-version reads unlock dependent specialist tools; skills never grant authorization.")
    request = ModelRequest(model=None, messages=[], tools=[], state={"skills_metadata": metadata})
    return middleware.modify_request(request).system_message.text


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class SkillSession:
    """Host-owned receipts persisted in the existing protected host checkpoint."""
    def __init__(self, identity, available):
        self.identity = identity
        self.available = available
        self.receipts: dict[str, dict] = {}
        self.active: set[str] = set()
        self.required_message_ids: set[str] = set()

    def _rows(self):
        return {row["id"]: row for row in skill_catalog(set(self.available()))}

    def page(self, skill_id, version, sha256, resource="", start_char=0, max_chars=3000):
        rows = self._rows()
        selected = {"id": skill_id, "version": version, "sha256": sha256}
        validate_selected_skills([selected], set(self.available()))
        info = registry.skill_info(skill_id)
        if any(info[key] != selected[key] for key in _IDENTITY_FIELDS):
            raise ValueError("skill_changed_during_read")
        page = registry.read_resource(skill_id, resource, start_char=start_char, max_chars=max_chars)
        # Reverify the package after the page read, not just its entrypoint.
        after = registry.skill_info(skill_id)
        if any(after[key] != selected[key] for key in _IDENTITY_FIELDS) or page["version"] != version:
            raise ValueError("skill_changed_during_read")
        return {"skill": selected, "identity": asdict(self.identity), "page": page,
                "required_resources": info["required_resources"],
                "dependencies": [{key: rows[dep][key] for key in _IDENTITY_FIELDS}
                                 for dep in _closure(info.get("depends_on", []), rows)]}

    def load(self, selected):
        """Read only selected roots/dependencies; loading itself grants no receipt."""
        selected = validate_selected_skills(selected, set(self.available()))
        if not selected:
            return {"skill_load": []}
        rows = self._rows()
        pages, delivered_chars = [], 0
        for sid in _closure([row["id"] for row in selected], rows):
            row = rows[sid]
            for resource in row["required_resources"]:
                start = 0
                while True:
                    payload = self.page(sid, row["version"], row["sha256"], resource, start, 20000)
                    delivered_chars += len(_encode(payload))
                    if delivered_chars > _MAX_LOAD_CHARS:
                        raise ValueError("skill_load_too_large_use_read_skill_pages")
                    pages.append(payload)
                    start = payload["page"]["next_start_char"]
                    if start is None:
                        break
        return {"skill_load": pages}

    def selected_messages(self, selected):
        """Host preload: every loaded byte becomes an explicit protected control."""
        messages = []
        for payload in self.load(selected)["skill_load"]:
            content = _encode(payload)
            mid = "skill-" + hashlib.sha256(content.encode()).hexdigest()
            message = SystemMessage(content=content, id=mid,
                additional_kwargs=self.identity.message_metadata("internal_control"))
            self.receipts[mid] = self._receipt("selected", mid, content, payload)
            messages.append(message)
        return messages

    @staticmethod
    def _receipt(kind, message_key, content, payload):
        return {"kind": kind, "message_key": message_key,
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(), "payload": payload}

    def observe(self, message):
        """Only call with a HostTools-published/recovered observation, never model text."""
        if (not isinstance(message, ToolMessage) or message.name not in {"read_skill", "load_skill"}
                or message.status == "error"):
            return
        try:
            data = json.loads(message.content)
            pages = data["skill_load"] if message.name == "load_skill" else [data]
            for payload in pages:
                self._verify_page(payload)
        except (ValueError, KeyError, TypeError, OSError):
            return
        for index, payload in enumerate(pages):
            key = message.tool_call_id if message.name == "read_skill" else f"{message.tool_call_id}:{index}"
            self.receipts[key] = self._receipt("tool", message.tool_call_id, message.content, payload)

    def _verify_page(self, payload):
        if payload.get("identity") != asdict(self.identity):
            raise ValueError("skill_receipt_identity_mismatch")
        skill, page = payload["skill"], payload["page"]
        expected = self.page(skill["id"], skill["version"], skill["sha256"], page["resource"],
                             page["start_char"], max(1, len(page["text"])))
        if expected != payload:
            raise ValueError("skill_receipt_bytes_mismatch")

    def export(self):
        return {"identity": asdict(self.identity), "receipts": deepcopy(self.receipts)}

    def restore(self, saved):
        self.active.clear()
        if saved is None:  # P3 checkpoints predate authoritative skill receipts.
            self.receipts = {}
            return
        if saved.get("identity") != asdict(self.identity):
            raise AdmissionStopped("skill_receipt_identity_mismatch")
        self.receipts = deepcopy(saved.get("receipts", {}))

    def verify_view(self, messages):
        """Recheck permissions, package bytes, full range coverage and active text."""
        if not self.receipts:
            self.active.clear()
            self.required_message_ids.clear()
            return set()
        rows = self._rows()
        visible, message_ids = {}, {}
        for message in messages:
            if isinstance(message, ToolMessage):
                key = ("tool", message.tool_call_id)
            elif isinstance(message, SystemMessage):
                key = ("selected", message.id)
            else:
                continue
            visible[key] = message.content
            message_ids[key] = message.id
        covered = {}
        valid = {}
        for key, receipt in self.receipts.items():
            try:
                self._verify_page(receipt["payload"])
            except (ValueError, KeyError, TypeError, OSError):
                continue  # changed packages/permissions cannot inherit a receipt
            content = visible.get((receipt["kind"], receipt["message_key"]))
            if (not isinstance(content, str)
                    or hashlib.sha256(content.encode()).hexdigest() != receipt["content_sha256"]):
                raise AdmissionStopped("required_method_missing_from_active_projection")
            valid[key] = receipt
            p = receipt["payload"]
            page = p["page"]
            covered.setdefault((p["skill"]["id"], page["resource"]), []).append(
                (page["start_char"], page["start_char"] + len(page["text"]), page["total_chars"]))
        loaded = set()
        for sid, row in rows.items():
            complete = True
            for resource in row["required_resources"]:
                ranges = sorted(covered.get((sid, resource), []))
                end = 0
                for start, stop, total in ranges:
                    if start > end:
                        break
                    end = max(end, stop)
                if not ranges or end != ranges[0][2]:
                    complete = False
            if complete:
                loaded.add(sid)
        self.active = {sid for sid in loaded if set(_closure([sid], rows)) <= loaded}
        self.receipts = valid
        self.required_message_ids = {message_ids[(receipt["kind"], receipt["message_key"])]
                                     for receipt in valid.values()
                                     if message_ids.get((receipt["kind"], receipt["message_key"]))}
        return set(self.active)

    def _current_methods(self):
        # Recheck package versions/permissions at dispatch; active-view coverage is
        # established only immediately before the current model call.
        if not self.active:
            return set()
        rows = self._rows()
        valid = set()
        for sid in self.active:
            if sid not in rows:
                continue
            receipts = [r for r in self.receipts.values() if r["payload"]["skill"]["id"] == sid]
            if receipts and all(all(r["payload"]["skill"][key] == rows[sid][key]
                                   for key in _IDENTITY_FIELDS) for r in receipts):
                valid.add(sid)
        complete = set()
        for sid in valid:
            try:
                if set(_closure([sid], rows)) <= valid:
                    complete.add(sid)
            except ValueError:
                continue  # A missing dependency must not disable generic tools.
        return complete

    def allowed(self, names):
        valid = self._current_methods()
        return {name for name in names if name not in _TOOL_METHODS or _TOOL_METHODS[name] in valid}

    def permits_call(self, call):
        name, args = call["name"], call.get("args", {})
        if name not in self.allowed(set(self.available())):
            return False
        # The shared workspace remains usable for generic reads/code storage.
        if name == "research_workspace" and args.get("operation") == "figure_edit":
            return "figure-edit" in self._current_methods()
        return True
