"""Loop boundaries, separate from provider messages and operation receipts.

The event sink is safe for public progress: it receives no model text, tool
arguments/results, or private provider state. Checkpoints are different: their
messages are protected execution state, never a public event payload. Hosts
must use their provider-aware persistence adapter, not dataclasses.asdict().

Loop mechanics reference pi-mono and OpenCode; see docs/agent_loop_sources.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from langchain_core.messages import BaseMessage

from .identity import ExecutionIdentity


Phase = Literal["ready", "model", "tools", "final", "stopped"]


@dataclass(frozen=True)
class LoopCheckpoint:
    identity: ExecutionIdentity
    messages: tuple[BaseMessage, ...]
    model_steps: int
    phase: Phase
    pending_tool_calls: tuple[str, ...] = ()


@dataclass(frozen=True)
class LoopEvent:
    kind: Literal["run_started", "model_started", "model_finished", "batch_started",
                  "batch_finished", "model_output_rejected", "final_rejected", "run_finished"]
    identity: ExecutionIdentity
    model_steps: int
    tool_call_ids: tuple[str, ...] = ()
    status: str = ""
    reason: str = ""
