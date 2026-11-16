"""Code-owned capability descriptions with isolated, lazy implementations.

A catalog describes tools; it does not grant authorization or execute code.
Factories are installed by the application, never from model/file/skill text.
Importing a catalog does not import the optional providers it describes.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import re
import threading
from typing import Any, Callable


class CapabilityLoadError(ValueError):
    """A code-owned, safe load diagnostic; never includes provider text."""

    def __init__(self, code: str, field: str = ""):
        if code not in {"preset_schema_drift", "installed_contract_changed"}:
            raise ValueError("invalid_load_diagnostic")
        self.code = code
        self.field = field if re.fullmatch(r"[A-Za-z0-9_./~-]{0,160}", field) else "schema"
        super().__init__(code + (":" + self.field if self.field else ""))


class CapabilityUnavailable(LookupError):
    def __init__(self, name: str, reason: str):
        self.name, self.reason = name, reason
        super().__init__(f"{name}: {reason}")


@dataclass(frozen=True)
class CapabilityDefinition:
    name: str
    description: str
    parameters: dict
    load: Callable[[], Any]
    provider: str = "core"
    mutates: bool = False
    skill_id: str = ""
    execution_mode: str = "parallel"  # parallel reads, sequential work, interaction barrier

    def __post_init__(self):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", self.name):
            raise ValueError("invalid_capability_name")
        if not self.description.strip() or not self.provider.strip():
            raise ValueError("capability_description_and_provider_required")
        if not isinstance(self.parameters, dict) or self.parameters.get("type") != "object":
            raise ValueError("capability_object_schema_required")
        if not callable(self.load):
            raise TypeError("capability_factory_required")
        if self.execution_mode not in {"parallel", "sequential", "interaction"}:
            raise ValueError("invalid_capability_execution_mode")
        if type(self.mutates) is not bool:
            raise TypeError("capability_mutates_must_be_boolean")


class CapabilityCatalog:
    def __init__(self):
        self._definitions: dict[str, CapabilityDefinition] = {}
        self._implementations: dict[str, Any] = {}
        self._unavailable: dict[str, str] = {}
        self._lock = threading.RLock()

    def register(self, definition: CapabilityDefinition) -> None:
        with self._lock:
            if definition.name in self._definitions:
                raise ValueError("duplicate_capability:" + definition.name)
            # Caller-owned mutable schemas cannot change a registered contract.
            self._definitions[definition.name] = CapabilityDefinition(
                definition.name, definition.description, deepcopy(definition.parameters),
                definition.load, definition.provider, definition.mutates, definition.skill_id,
                definition.execution_mode)

    def describe(self) -> list[dict]:
        with self._lock:
            return [{"name": d.name, "description": d.description,
                     "provider": d.provider, "mutates": d.mutates,
                     "skill_id": d.skill_id, "execution_mode": d.execution_mode,
                     "state": "unavailable" if d.name in self._unavailable else
                              "loaded" if d.name in self._implementations else "discoverable",
                     "reason": self._unavailable.get(d.name, "")}
                    for d in self._definitions.values()]

    def schemas(self, allowed: set[str]) -> list[dict]:
        """The host supplies allowed names from real authorization/readiness.

        No domain classification is accepted here. A missing optional plugin
        affects only its own tools, never the entire catalog.
        """
        with self._lock:
            return [{"type": "function", "function": {
                "name": d.name, "description": d.description,
                "parameters": deepcopy(d.parameters)}}
                for d in self._definitions.values()
                if d.name in allowed and d.name not in self._unavailable]

    def execution_mode(self, name: str) -> str:
        """Scheduling metadata never loads a plugin or grants execution rights."""
        with self._lock:
            definition = self._definitions.get(name)
            if definition is None:
                return "sequential"  # unknown tools are observed as not-run
            if definition.execution_mode == "interaction":
                return "interaction"
            return "sequential" if definition.mutates else definition.execution_mode

    def resolve(self, name: str, allowed: set[str]):
        with self._lock:
            if name not in allowed:
                raise CapabilityUnavailable(name, "not_authorized_or_ready")
            definition = self._definitions.get(name)
            if definition is None:
                raise CapabilityUnavailable(name, "not_registered")
            if name in self._unavailable:
                raise CapabilityUnavailable(name, self._unavailable[name])
            if name not in self._implementations:
                try:
                    implementation = definition.load()
                    if implementation is None:
                        raise TypeError("capability_factory_returned_none")
                except Exception as exc:
                    # Only our typed diagnostic may publish a field path. Arbitrary
                    # dependency exceptions still expose the class, never their text.
                    reason = "load_failed:" + (str(exc) if isinstance(exc, CapabilityLoadError)
                                               else type(exc).__name__)
                    import logging
                    logging.getLogger(__name__).warning("Capability %s unavailable: %s", name, reason)
                    self._unavailable[name] = reason
                    raise CapabilityUnavailable(name, reason) from None
                self._implementations[name] = implementation
            return self._implementations[name]

    def invalidate(self, provider: str) -> None:
        """Host-only readiness change; does not dispatch/replay pending work."""
        with self._lock:
            for name, definition in self._definitions.items():
                if definition.provider == provider:
                    self._implementations.pop(name, None)
                    self._unavailable.pop(name, None)
