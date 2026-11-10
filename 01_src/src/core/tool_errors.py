"""Typed tool-argument errors (kept in core to avoid import cycles)."""
from __future__ import annotations


class ToolArgumentError(ValueError):
    """A tool argument is malformed or incomplete.

    ``retryable`` tells the orchestrator that re-issuing the call with a smaller
    or corrected payload can work, so an intermediate failure should not be
    presented as a permanent step failure.
    """

    retryable = True

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = bool(retryable)
