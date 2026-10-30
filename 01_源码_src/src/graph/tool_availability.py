"""Environment-driven tool availability for the General Agent.

This module answers exactly one question: **may this tool be offered to the model
in the current environment?** It looks only at facts the runtime actually knows
(network reachability, whether a local index is loaded, how many files the user
attached, whether the account library has files, whether a session exists, and
whether the operator enabled the research tools) plus the two constraints the
user can state explicitly (source scope and "do not search this turn").

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

# What the user said about sources for this turn. "auto" and "fusion" both allow
# every source; the two restricted values remove one family of retrieval tools
# because the user asked for it in so many words.
SCOPE_AUTO = "auto"
SCOPE_LOCAL = "local"
SCOPE_WEB = "web"
SCOPE_FUSION = "fusion"
KNOWN_SCOPES = frozenset({SCOPE_AUTO, SCOPE_LOCAL, SCOPE_WEB, SCOPE_FUSION})

# User-facing reason shown in logs/tests (never shown as a fake tool step).
REQUIREMENT_LABELS = {
    REQUIRES_NETWORK: "当前环境没有可用的联网出口",
    REQUIRES_LOCAL_CORPUS: "本地语料索引未就绪",
    REQUIRES_ATTACHMENT: "本轮没有上传可供检查的附件",
    REQUIRES_ACCOUNT_FILES: "账号文件库里没有可读取的文件",
    REQUIRES_SESSION: "当前请求没有有效会话，无法保存成果",
    REQUIRES_RESEARCH_TOOLS: "运维已关闭科研工具",
}

SCOPE_LABELS = {
    SCOPE_LOCAL: "用户本轮限定只用本地资料",
    SCOPE_WEB: "用户本轮限定只用联网资料",
}

NO_RETRIEVAL_LABEL = "用户本轮要求不检索"


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
    # Explicit user constraints. They are facts about the request, not guesses
    # about its topic: "只查本地库" and "这轮别检索" are statements, not keywords
    # that stand in for one.
    source_scope: str = SCOPE_AUTO
    retrieval: bool = True

    def has(self, requirement: str) -> bool:
        if requirement == REQUIRES_NETWORK:
            if self.source_scope == SCOPE_LOCAL:
                return False
            return self.network and self.retrieval
        if requirement == REQUIRES_LOCAL_CORPUS:
            if self.source_scope == SCOPE_WEB:
                return False
            return self.local_corpus and self.retrieval
        if requirement == REQUIRES_ATTACHMENT:
            return self.attachments > 0
        if requirement == REQUIRES_ACCOUNT_FILES:
            return self.account_files > 0
        if requirement == REQUIRES_SESSION:
            return self.session
        if requirement == REQUIRES_RESEARCH_TOOLS:
            return self.research_tools
        raise KeyError(f"unknown requirement token: {requirement}")


def runtime_env(*, source_scope: str = SCOPE_AUTO, attachments: int = 0,
                account_files: int = 0, session: bool = True,
                research_tools: bool = True, retrieval: bool = True,
                network: bool = True, local_corpus: bool = True,
                denied: frozenset[str] | set[str] | None = None) -> Env:
    """Build an `Env` from the values the request handler already computed.

    Kept here (not in the graph module) so the mapping from request state to
    availability facts has exactly one implementation and one place to test.
    """
    scope = str(source_scope or SCOPE_AUTO)
    if scope not in KNOWN_SCOPES:
        scope = SCOPE_AUTO
    return Env(
        network=bool(network),
        local_corpus=bool(local_corpus),
        attachments=max(0, int(attachments or 0)),
        account_files=max(0, int(account_files or 0)),
        session=bool(session),
        research_tools=bool(research_tools),
        denied=frozenset(denied or ()),
        source_scope=scope,
        retrieval=bool(retrieval),
    )


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


def _reason(token: str, env: Env) -> str:
    """Explain why a requirement is unmet, preferring the explicit constraint."""
    if token in (REQUIRES_NETWORK, REQUIRES_LOCAL_CORPUS) and not env.retrieval:
        return NO_RETRIEVAL_LABEL
    if token == REQUIRES_NETWORK and env.source_scope == SCOPE_LOCAL:
        return SCOPE_LABELS[SCOPE_LOCAL]
    if token == REQUIRES_LOCAL_CORPUS and env.source_scope == SCOPE_WEB:
        return SCOPE_LABELS[SCOPE_WEB]
    return REQUIREMENT_LABELS.get(token, token)


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
    declared = declarations()
    for name in declared:
        if name in env.denied:
            reasons[name] = "用户在本轮禁用了该工具"
            continue
        missing = missing_requirements(name, env)
        reasons[name] = "" if not missing else "；".join(_reason(t, env) for t in missing)
    return reasons


__all__ = [
    "AUTHORIZATION_EXPLICIT_USER_REQUEST", "KNOWN_AUTHORIZATIONS", "KNOWN_PRODUCES",
    "KNOWN_REQUIRES", "KNOWN_SCOPES", "NO_RETRIEVAL_LABEL", "REQUIREMENT_LABELS",
    "SCOPE_AUTO", "SCOPE_FUSION", "SCOPE_LABELS", "SCOPE_LOCAL", "SCOPE_WEB",
    "Env", "available_tool_names", "declarations", "explain", "missing_requirements",
    "runtime_env",
]
