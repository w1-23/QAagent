"""Clause splitting and negation handling shared by the intent detectors.

``general_agent_graph`` decides which capabilities a request explicitly asks
for (export, data audit, reference verification, local search). All of those
predicates must honour an explicit prohibition ("不要核验", "不用导出") in the
same clause, so the clause/negation logic lives here once instead of being
reimplemented per detector.

Keep these helpers pure and text-only: they are imported by the export
contract module as well, so they must not depend on the graph, the tool
registry, or configuration.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- negation
# A clause may explicitly forbid an action ("不要核验", "不用导出"). Every
# intent predicate runs through this helper so a forbidden capability is never
# advertised. The check looks at the text BEFORE the intent match inside the
# same clause, which is where the negation naturally sits in Chinese.
_CLAUSE_SPLIT = re.compile(r"[，,。！？!?；;\n]+|但是|不过|然而|\bbut\b|\bhowever\b", re.I)
_NEGATION_TAIL = re.compile(
    r"(?:不要|不需要|无需|不用|不必|不想|不希望|暂不|先不|别|请勿|勿|禁止|"
    r"do\s+not|don't|no\s+need\s+to|without)\s*(?:(?:再|自动|进行|继续|直接|额外)\s*)*$", re.I)
# "只列出参考文献，不要核验" — the negation follows the keyword, so a predicate
# that only looks backwards would miss it. These patterns catch the forward case.
_NEGATION_AFTER = re.compile(
    r"(?:不要|不需要|无需|不用|不必|请勿|勿)\s*(?:再|进行|做)?\s*"
    r"(?:核验|核查|检查|审查|导出|保存|验证|verify\b|check\b|export\b)", re.I)


def intent_clauses(text: str) -> list[str]:
    return [c for c in _CLAUSE_SPLIT.split(str(text or "")) if c.strip()]


def negated_before(clause: str, start: int) -> bool:
    """True when the text immediately preceding the match forbids the action."""
    return bool(_NEGATION_TAIL.search(clause[:start].rstrip()))


def negated_after(clause: str, end: int) -> bool:
    """True when the match itself is followed by an explicit prohibition."""
    return bool(_NEGATION_AFTER.match(clause[end:].lstrip()))


def intent_with_negation(query: str, pattern: re.Pattern) -> bool:
    """Return True only if some clause requests the intent without negating it."""
    for clause in intent_clauses(query):
        for match in pattern.finditer(clause):
            if negated_before(clause, match.start()):
                continue
            if negated_after(clause, match.end()):
                continue
            return True
    return False
