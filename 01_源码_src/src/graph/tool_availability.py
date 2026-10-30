"""Environment-driven tool availability for the General Agent.

This module answers exactly one question: **may this tool be offered to the model
in the current environment?** It looks only at facts the runtime actually knows
(network reachability, whether a local index is loaded, how many files the user
attached, whether the account library has files, whether a session exists, and
whether the operator enabled the research tools).

It deliberately does NOT contain topic keywords, intent detection, or "if the
question mentions X then hide tool Y" logic. Choosing *which* available tool to
call is the model's job; this module only removes tools that cannot possibly
succeed. Execution-time authorization (e.g. writing a note) is a separate
concern handled by the executor, not by visibility.

Keep it a pure function of `Env` so it can be unit-tested without a server.
"""
from __future__ import annotations

from dataclasses import dataclass, field

# Requirement tokens. Each one must correspond to a real precondition that the
# tool implementation will hit, not to a guess from the tool's name.
REQUIRES_NETWORK = "network"
REQUIRES_LOCAL_CORPUS = "local_corpus"
REQUIRES_ATTACHMENT = "attachment"
REQUIRES_ACCOUNT_FILES = "account_files"
REQUIRES_SESSION = "session"
REQUIRES_RESEARCH_TOOLS = "research_tools"

KNOWN_REQUIRES = frozenset({
    REQUIRES_NETWORK, REQUIRES_LOCAL_CORPUS, REQUIRES_ATTACHMENT,
    REQUIRES_ACCOUNT_FILES, REQUIRES_SESSION, REQUIRES_RESEARCH_TOOLS,
})

# Deliverable kinds a tool actually writes to storage. Used to verify that a
# requested deliverable really exists (never inferred from a file extension).
KNOWN_PRODUCES = frozenset({
    "figure", "audit_report", "verification_sheet", "supplement_archive",
    "supplement_report", "bibliography", "report_html", "note", "gap_csv",
})

# Execution-time authorizations. Visibility is unaffected; the executor must ask
# for these before performing the side effect.
AUTHORIZATION_EXPLICIT_USER_REQUEST = "explicit_user_request"
KNOWN_AUTHORIZATIONS = frozenset({AUTHORIZATION_EXPLICIT_USER_REQUEST})

# User-facing reason shown in logs/tests (never shown as a fake tool step).
REQUIREMENT_LABELS = {
    REQUIRES_NETWORK: "当前环境没有可用的联网出口",
    REQUIRES_LOCAL_CORPUS: "本地语料索引未就绪",
    REQUIRES_ATTACHMENT: "本轮没有上传可供检查的附件",
    REQUIRES_ACCOUNT_FILES: "账号文件库里没有可读取的文件",
    REQUIRES_SESSION: "当前请求没有有效会话，无法保存成果",
    REQUIRES_RESEARCH_TOOLS: "运维已关闭科研工具",
}


@dataclass(frozen=True)
class Env:
    """Facts about the current runtime. Defaults describe a healthy environment."""

    network: bool = True
    local_corpus: bool = True
    attachments: int = 0
    account_files: int = 0
    session: bool = True
    research_tools: bool = True
    denied: frozenset[str] = field(default_factory=frozenset)

    def has(self, requirement: str) -> bool:
        if requirement == REQUIRES_NETWORK:
            return self.network
        if requirement == REQUIRES_LOCAL_CORPUS:
            return self.local_corpus
        if requirement == REQUIRES_ATTACHMENT:
            return self.attachments > 0
        if requirement == REQUIRES_ACCOUNT_FILES:
            return self.account_files > 0
        if requirement == REQUIRES_SESSION:
            return self.session
        if requirement == REQUIRES_RESEARCH_TOOLS:
            return self.research_tools
        raise KeyError(f"unknown requirement token: {requirement}")


def declarations() -> dict[str, tuple[frozenset[str], frozenset[str], str]]:
    """name -> (requires, produces, authorization) from the single registry."""
    from src.tools import _TOOL_DECLARATIONS

    out: dict[str, tuple[frozenset[str], frozenset[str], str]] = {}
    for tool, _category, _key, _scope, requires, produces, authorization in _TOOL_DECLARATIONS:
        out[tool.name] = (frozenset(requires), frozenset(produces), authorization)
    return out


def missing_requirements(tool_name: str, env: Env) -> tuple[str, ...]:
    """Requirement tokens this tool needs but `env` cannot satisfy."""
    requires = declarations()[tool_name][0]
    return tuple(sorted(token for token in requires if not env.has(token)))


def available_tool_names(env: Env) -> list[str]:
    """Names the model may be offered, preserving declaration order."""
    names = []
    for name in declarations():
        if name in env.denied:
            continue
        if missing_requirements(name, env):
            continue
        names.append(name)
    return names


def explain(env: Env) -> dict[str, str]:
    """name -> "" when available, otherwise a short human-readable reason."""
    reasons: dict[str, str] = {}
    for name in declarations():
        if name in env.denied:
            reasons[name] = "用户在本轮禁用了该工具"
            continue
        missing = missing_requirements(name, env)
        reasons[name] = "" if not missing else "；".join(
            REQUIREMENT_LABELS.get(token, token) for token in missing)
    return reasons


__all__ = [
    "AUTHORIZATION_EXPLICIT_USER_REQUEST", "KNOWN_AUTHORIZATIONS", "KNOWN_PRODUCES",
    "KNOWN_REQUIRES", "REQUIREMENT_LABELS", "Env", "available_tool_names",
    "declarations", "explain", "missing_requirements",
]
