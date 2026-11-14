"""Loop decisions for one turn, in one place.

The agent node still proposes actions — that is the model's job — but every
decision about whether the loop may continue, extend, retry an obligation or
stop lives here. Previously those counters and thresholds were scattered across
the node and each recovery path had its own copy, so a retry could be added
without the stop condition noticing.

No model calls, no tool calls, no SSE text shaping: pure state transitions that
can be exercised without a provider.
"""
from __future__ import annotations

from dataclasses import dataclass

CONTINUE = "continue"
EXTEND = "extend"
STOP = "stop"

MAX_TURNS = 40
EXTENSION_SIZE = 8
MAX_EXTENSIONS = 3
NO_PROGRESS_LIMIT = 2
MAX_CONTRACT_REPAIRS = 2


@dataclass(frozen=True)
class LoopDecision:
    action: str = CONTINUE
    reason: str = ""
    turn_budget: int = 0
    message: str = ""

    @property
    def stops(self) -> bool:
        return self.action == STOP


@dataclass
class LoopKernel:
    """Per-turn loop state. One instance belongs to one request."""

    turn_budget: int
    tool_turns: int = 0
    extensions_used: int = 0
    contract_repairs: int = 0
    stagnant_batches: int = 0

    # ------------------------------------------------------------- turn budget
    def begin_tool_round(self) -> None:
        self.tool_turns += 1

    def check_turn_limit(self, *, unfinished_groups: bool, needs_export: bool) -> LoopDecision:
        """Extend for real unfinished work, or stop; never extend for a proxy."""
        if self.tool_turns < self.turn_budget:
            return LoopDecision(CONTINUE, turn_budget=self.turn_budget)
        if (unfinished_groups or needs_export) and self.extensions_used < MAX_EXTENSIONS and self.turn_budget < MAX_TURNS:
            self.extensions_used += 1
            self.turn_budget = min(self.turn_budget + EXTENSION_SIZE, MAX_TURNS)
            return LoopDecision(
                EXTEND, turn_budget=self.turn_budget,
                message=f"继续执行：已延长 {EXTENSION_SIZE} 轮预算…")
        return LoopDecision(STOP, reason="tool_turn_limit", turn_budget=self.turn_budget)

    # ---------------------------------------------------------- batch progress
    def record_batch(self, *, new_evidence: bool, coverage_advanced: bool) -> bool:
        """Record one tool batch; return True when the loop has gone stagnant.

        Coverage counts as progress even when the evidence digest repeats: a
        record that moved from unresolved to checked changes status without
        changing its fingerprint. A batch over already-covered targets is not
        progress, however many tools it called.
        """
        if new_evidence or coverage_advanced:
            self.stagnant_batches = 0
            return False
        self.stagnant_batches += 1
        return self.stagnant_batches >= NO_PROGRESS_LIMIT

    # ------------------------------------------------------- obligation repair
    def allow_contract_repair(self) -> bool:
        if self.contract_repairs >= MAX_CONTRACT_REPAIRS:
            return False
        self.contract_repairs += 1
        return True

    def as_dict(self) -> dict:
        return {
            "turn_budget": self.turn_budget,
            "tool_turns": self.tool_turns,
            "extensions_used": self.extensions_used,
            "contract_repairs": self.contract_repairs,
            "stagnant_batches": self.stagnant_batches,
        }


__all__ = [
    "CONTINUE", "EXTEND", "EXTENSION_SIZE", "LoopDecision", "LoopKernel",
    "MAX_CONTRACT_REPAIRS", "MAX_EXTENSIONS", "MAX_TURNS", "NO_PROGRESS_LIMIT", "STOP",
]
