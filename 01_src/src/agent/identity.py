"""Host-owned execution identity; a message role is not a turn boundary."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ExecutionIdentity:
    owner_user_id: int
    session_id: str
    user_turn_id: str
    run_id: str

    def __post_init__(self):
        if type(self.owner_user_id) is not int or self.owner_user_id <= 0:
            raise ValueError("execution_owner_required")
        for name in ("session_id", "user_turn_id", "run_id"):
            value = getattr(self, name)
            if (not isinstance(value, str) or not value or value != value.strip()
                    or len(value) > 256 or any(ord(c) < 32 for c in value)):
                raise ValueError("invalid_execution_identity:" + name)

    @property
    def turn_key(self) -> tuple[int, str, str]:
        """Recovery changes the run, never this authorization/budget boundary."""
        return self.owner_user_id, self.session_id, self.user_turn_id

    def message_metadata(self, origin: str) -> dict:
        if origin not in {"user", "assistant", "tool", "internal_control"}:
            raise ValueError("unknown_message_origin")
        return {"context_origin": origin, "context_trusted": True,
                "session_id": self.session_id, "turn_id": self.user_turn_id,
                "context_turn_id": self.user_turn_id,
                "request_id": self.user_turn_id, "run_id": self.run_id}
