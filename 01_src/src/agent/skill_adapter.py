"""Progressive installed-method guidance with identity-bound source receipts.

The product scenario API advertises the pinned root release. Archived method
reads retain their exact identities separately from new scenario selection.
Exact release identities select readers; skills never gate tool admission.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
import hashlib
import json

from langchain_core.messages import SystemMessage, ToolMessage

from src.prompts import skill_registry as registry
from . import research_skill_catalog as research_catalog
from .runtime import AdmissionStopped

_MAX_LOAD_CHARS = 240000  # Whole-method delivery is bounded, never silently truncated.
_IDENTITY_FIELDS = ("id", "version", "sha256")
_CATALOG_FIELDS = (*_IDENTITY_FIELDS, "description", "applicability", "depends_on",
                   "required_resources", "requires_any")


SCENARIO_SKILLS = frozenset({"candidate-search", "reference-verification", "data-audit", "figure-initial"})


def skill_catalog(available_tools: set[str] | None = None, *, scenarios_only: bool = False) -> list[dict]:
    """Compact, verified API catalog; no resource bodies or runtime imports."""
    return [{key: row.get(key, [] if key in {"depends_on", "required_resources", "requires_any"} else "")
             for key in _CATALOG_FIELDS}
            for row in registry.list_skills()
            if row.get("verified") and (not scenarios_only or row["id"] in SCENARIO_SKILLS)
            and (available_tools is None or not row.get("requires_any")
                or set(row["requires_any"]) & available_tools)]


def scenario_catalog() -> list[dict]:
    """Current four product scenarios; availability never grants execution rights."""
    return [{key: row.get(key, [] if key in {"depends_on", "required_resources", "requires_any"} else "")
             for key in _CATALOG_FIELDS} for row in research_catalog.list_skills()]


def validate_scenario_selection(value) -> list[dict[str, str]]:
    """Accept only exact current release identities for a new product request.

    Older identities remain readable through SkillSession, but are never silently
    upgraded or accepted as the current product menu's selection.
    """
    if value is None or (type(value) is list and not value):
        return []
    if type(value) is not list or len(value) > 4:
        raise ValueError("invalid_selected_skills")
    rows = {row["id"]: row for row in scenario_catalog()}
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
    return result


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
    rows = research_catalog.list_skills()
    try:
        ordinary = [entry["id"] for entry in registry._catalog()
                    if entry["id"] not in SCENARIO_SKILLS | {"nature-figure"}]
    except (OSError, ValueError, KeyError, TypeError):
        ordinary = []
    for sid in ordinary:
        try:
            rows.append(registry.skill_info(sid))
        except (OSError, ValueError, KeyError, TypeError):
            continue  # One archived method failure does not disable other guidance.
    if not rows:
        return "No verified skills are available; generic authorized tools remain usable."
    metadata = [{"name": row["id"], "description": row["description"] + " " + row["applicability"],
                 "path": row["id"], "allowed_tools": [], "metadata": {}, "license": None,
                 "compatibility": "version=" + row["version"] + " sha256=" + row["sha256"]}
                for row in rows]
    middleware = SkillsMiddleware(backend=StateBackend(), sources=[("/installed-skills/", "Verified registry")],
        system_prompt="{skills_locations}\n{skills_list}\n{skills_load_warnings}\n"
        "Use load_skill(skill_id, version, sha256) for the method documents and dependencies, or "
        "read_skill(skill_id, version, sha256) for declared resource pages. Follow next_start_char "
        "when more text is useful. Optional resources require exact manifest paths; vendor trees are not "
        "loaded by default. These ids are registry keys, not filesystem paths. "
        "Skills guide methods; missing or partial reads never gate authorized tools or dispatch. "
        "Read/discovery failures are local and do not block the task; skills never grant authorization.")
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
        self.diagnostics: list[str] = []

    @staticmethod
    def _validate_identity(selected):
        if (type(selected) is not dict or set(selected) != set(_IDENTITY_FIELDS)
                or any(type(selected[key]) is not str for key in _IDENTITY_FIELDS)):
            raise ValueError("invalid_selected_skill_identity")
        return selected

    def page(self, skill_id, version, sha256, resource="", start_char=0, max_chars=3000):
        selected = self._validate_identity({"id": skill_id, "version": version, "sha256": sha256})
        if (type(start_char) is not int or start_char < 0
                or type(max_chars) is not int or not 1 <= max_chars <= 20000):
            raise ValueError("invalid_skill_pagination")
        dependencies = []
        if research_catalog.PINNED.get(skill_id) == (version, sha256):
            info, page = research_catalog.read_resource(skill_id, version, sha256, resource,
                                                       start_char=start_char, max_chars=max_chars)
        else:
            info = registry.skill_info(skill_id)
            if any(info[key] != selected[key] for key in _IDENTITY_FIELDS):
                raise ValueError("selected_skill_identity_mismatch")
            if info.get("depends_on"):
                # Resolve archived dependencies only in their original registry.
                rows = {row["id"]: row for row in skill_catalog()}
                dependencies = [{key: rows[dep][key] for key in _IDENTITY_FIELDS}
                                for dep in _closure(info["depends_on"], rows)]
            page = registry.read_resource(skill_id, resource, start_char=start_char, max_chars=max_chars)
            after = registry.skill_info(skill_id)
            if any(after[key] != selected[key] for key in _IDENTITY_FIELDS) or page["version"] != version:
                raise ValueError("skill_changed_during_read")
        return {"skill": selected, "identity": asdict(self.identity), "page": page,
                "required_resources": info["required_resources"], "dependencies": dependencies}

    def load(self, selected):
        """Deliver method docs, keeping optional vendor resources out of default loads."""
        if selected is None:
            selected = []
        if type(selected) is not list or len(selected) > 8:
            raise ValueError("invalid_selected_skills")
        selected = [self._validate_identity(item) for item in selected]
        if len({tuple(item[key] for key in _IDENTITY_FIELDS) for item in selected}) != len(selected):
            raise ValueError("selected_skill_identity_mismatch")
        pages, delivered_chars, loaded, visiting = [], 0, set(), set()

        def visit(item):
            nonlocal delivered_chars
            identity = tuple(item[key] for key in _IDENTITY_FIELDS)
            if identity in visiting:
                raise ValueError("skill_dependency_cycle")
            if identity in loaded:
                return
            visiting.add(identity)
            first = self.page(item["id"], item["version"], item["sha256"], max_chars=20000)
            for dependency in first["dependencies"]:
                visit(dependency)
            for resource in first["required_resources"]:
                start = 0
                while True:
                    payload = (first if resource == first["page"]["resource"] and start == 0 else
                               self.page(item["id"], item["version"], item["sha256"], resource, start, 20000))
                    delivered_chars += len(_encode(payload))
                    if delivered_chars > _MAX_LOAD_CHARS:
                        raise ValueError("skill_load_too_large_use_read_skill_pages")
                    pages.append(payload)
                    start = payload["page"]["next_start_char"]
                    if start is None:
                        break
            visiting.remove(identity)
            loaded.add(identity)

        for item in selected:
            visit(item)
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
        except (ValueError, KeyError, TypeError, OSError, AttributeError):
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
        self.required_message_ids.clear()
        self.diagnostics.clear()
        if saved is None:  # Checkpoints predating source receipts.
            self.receipts = {}
            return
        if type(saved) is not dict or saved.get("identity") != asdict(self.identity):
            raise AdmissionStopped("skill_receipt_identity_mismatch")
        receipts = saved.get("receipts", {})
        self.receipts = deepcopy(receipts) if type(receipts) is dict else {}

    def verify_view(self, messages):
        """Diagnose source coverage; missing/partial/stale guidance never stops admission."""
        self.diagnostics.clear()
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
        covered, valid, packages = {}, {}, {}
        for key, receipt in self.receipts.items():
            try:
                self._verify_page(receipt["payload"])
                source_key = (receipt["kind"], receipt["message_key"])
                content = visible.get(source_key)
                if (not isinstance(content, str)
                        or hashlib.sha256(content.encode()).hexdigest() != receipt["content_sha256"]):
                    self.diagnostics.append("skill_receipt_missing_from_active_projection")
                    continue
                # Bind the saved payload to its visible observation as well as
                # the source bytes, including after checkpoint reconstruction.
                data = json.loads(content)
                delivered = data.get("skill_load", [data])
                if receipt["payload"] not in delivered:
                    raise ValueError("skill_receipt_bytes_mismatch")
                p = receipt["payload"]
                identity = tuple(p["skill"][field] for field in _IDENTITY_FIELDS)
                page = p["page"]
                covered.setdefault((identity, page["resource"]), []).append(
                    (page["start_char"], page["start_char"] + len(page["text"]), page["total_chars"]))
                packages[identity] = p
                valid[key] = receipt
            except (ValueError, KeyError, TypeError, OSError, AttributeError):
                self.diagnostics.append("skill_receipt_stale_or_invalid")
        loaded = set()
        for identity, payload in packages.items():
            complete = True
            for resource in payload["required_resources"]:
                ranges = sorted(covered.get((identity, resource), []))
                end = 0
                for start, stop, total in ranges:
                    if start > end:
                        break
                    end = max(end, stop)
                if not ranges or end != ranges[0][2]:
                    complete = False
            if complete:
                loaded.add(identity)
        self.active = {identity[0] for identity in loaded
                       if all(tuple(dep[field] for field in _IDENTITY_FIELDS) in loaded
                              for dep in packages[identity]["dependencies"])}
        self.receipts = valid
        # Kept for checkpoint/projection compatibility, never an admission rule.
        self.required_message_ids = {message_ids[(receipt["kind"], receipt["message_key"])]
                                     for receipt in valid.values()
                                     if message_ids.get((receipt["kind"], receipt["message_key"]))}
        return set(self.active)

    def allowed(self, names):
        """The caller supplies authorized tool names; guidance cannot narrow them."""
        return set(names)

    def permits_call(self, call):
        """Only the host's ordinary authorization applies, including figure edits."""
        return call["name"] in set(self.available())
