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
REQUIRES_CANDIDATE_POOL = "candidate_pool"
REQUIRES_ATTACHMENT = "attachment"
REQUIRES_ACCOUNT_FILES = "account_files"
REQUIRES_SESSION = "session"
REQUIRES_RESEARCH_TOOLS = "research_tools"
REQUIRES_SCRIPT_SANDBOX = "script_sandbox"

KNOWN_REQUIRES = frozenset({
    REQUIRES_NETWORK, REQUIRES_LOCAL_CORPUS, REQUIRES_CANDIDATE_POOL,
    REQUIRES_ATTACHMENT, REQUIRES_ACCOUNT_FILES, REQUIRES_SESSION,
    REQUIRES_RESEARCH_TOOLS, REQUIRES_SCRIPT_SANDBOX,
})

# Deliverable kinds a tool actually writes to storage. Used to verify that a
# requested deliverable really exists (never inferred from a file extension).
KNOWN_PRODUCES = frozenset({
    "figure", "audit_report", "annotation_table", "verification_sheet", "supplement_archive",
    "supplement_report", "bibliography", "report_html", "report_docx", "table_file", "note", "gap_csv",
    "statistics_report", "figure_package", "report_json",
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
    REQUIRES_CANDIDATE_POOL: "本轮没有可用的候选池（预取未执行或未命中）",
    REQUIRES_ATTACHMENT: "本轮没有上传可供检查的附件",
    REQUIRES_ACCOUNT_FILES: "账号文件库里没有可读取的文件",
    REQUIRES_SESSION: "当前请求没有有效会话，无法保存成果",
    REQUIRES_RESEARCH_TOOLS: "运维已关闭科研工具",
    REQUIRES_SCRIPT_SANDBOX: "当前环境没有配置绘图脚本沙箱",
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
    script_sandbox: bool = False
    denied: frozenset[str] = field(default_factory=frozenset)
    # Explicit user constraints. They are facts about the request, not guesses
    # about its topic: "只查本地库" and "这轮别检索" are statements, not keywords
    # that stand in for one.
    source_scope: str = SCOPE_AUTO
    retrieval: bool = True
    visualization: bool = False
    capability_denials: frozenset[str] = field(default_factory=frozenset)
    # True only after this request's synchronous local prefetch produced a pool
    # (including a zero-hit pool). Candidate read/inspect tools cannot run
    # without one, so they must not be advertised when it is absent.
    candidate_pool: bool = False

    def has(self, requirement: str) -> bool:
        if requirement == REQUIRES_NETWORK:
            if self.source_scope == SCOPE_LOCAL or "network" in self.capability_denials:
                return False
            return self.network and self.retrieval
        if requirement == REQUIRES_LOCAL_CORPUS:
            if self.source_scope == SCOPE_WEB or "local_corpus" in self.capability_denials:
                return False
            return self.local_corpus and self.retrieval
        if requirement == REQUIRES_CANDIDATE_POOL:
            return bool(self.candidate_pool)
        if requirement == REQUIRES_ATTACHMENT:
            return self.attachments > 0
        if requirement == REQUIRES_ACCOUNT_FILES:
            return self.account_files > 0
        if requirement == REQUIRES_SESSION:
            return self.session
        if requirement == REQUIRES_RESEARCH_TOOLS:
            return self.research_tools
        if requirement == REQUIRES_SCRIPT_SANDBOX:
            return self.script_sandbox
        raise KeyError(f"unknown requirement token: {requirement}")


# One probe per configuration, for the life of the process.
_SANDBOX_PROBE: dict[tuple[str, str, str, str, bool], bool] = {}


def script_sandbox_available() -> bool:
    """Whether this deployment can run a custom plotting script.

    An environment fact, read from the configuration: the sandbox backend is
    named, and the backend is actually usable. ``off`` means no script may
    run at all, which is the default — the declarative figure path does not
    need a sandbox and stays available.

    The check lives here so the request plan does not have to know about the
    sandbox: a caller that wants a different answer passes
    ``script_sandbox=`` explicitly.

    The answer is cached per configuration tuple. Probing means starting a
    ``docker version`` subprocess, and this function runs once per request;
    against a stopped daemon that subprocess waits out its timeout. The
    configuration only changes on restart, and keying on its values keeps
    a deliberate reconfiguration (including a test's monkeypatch) honest.
    """
    try:
        from src.config import settings
        backend = str(getattr(settings, "FIGURES_SANDBOX_BACKEND", "") or "").strip().lower()
        image = str(getattr(settings, "FIGURES_SANDBOX_IMAGE", "") or "")
        runtime = str(getattr(settings, "FIGURES_SANDBOX_RUNTIME", "") or "")
        image_sha256 = str(getattr(settings, "FIGURES_SANDBOX_IMAGE_SHA256", "") or "")
        allow_local = bool(getattr(settings, "FIGURES_SANDBOX_ALLOW_LOCAL", False))
        key = (backend, image, image_sha256, runtime, allow_local)
        if key in _SANDBOX_PROBE:
            return _SANDBOX_PROBE[key]
        if backend in {"", "off", "none", "disabled"}:
            answer = False
        elif backend == "local":
            answer = allow_local
        else:
            from src.figures.sandbox import select_backend
            answer = select_backend(
                backend=backend, image=image, runtime=runtime,
                image_sha256=image_sha256, allow_local=allow_local,
            ) is not None
        _SANDBOX_PROBE[key] = answer
        return answer
    except Exception:
        return False


def runtime_env(*, source_scope: str = SCOPE_AUTO, attachments: int = 0,
                account_files: int = 0, session: bool = True,
                research_tools: bool = True, retrieval: bool = True,
                network: bool = True, local_corpus: bool = True,
                visualization: bool = False,
                script_sandbox: bool | None = None,
                capability_denials: frozenset[str] | set[str] | None = None,
                denied: frozenset[str] | set[str] | None = None,
                candidate_pool: bool = False) -> Env:
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
        script_sandbox=(script_sandbox_available()
                        if script_sandbox is None else bool(script_sandbox)),
        denied=frozenset(denied or ()),
        source_scope=scope,
        retrieval=bool(retrieval),
        visualization=bool(visualization),
        capability_denials=frozenset(capability_denials or ()),
        candidate_pool=bool(candidate_pool),
    )


def provider_denials(config) -> frozenset[str]:
    """Read provider readiness without exposing credentials or making requests."""
    denied = set()
    deepseek_ready = bool(getattr(config, "RESOLVED_DEEPSEEK_WEB_API_KEY", ""))
    provider = getattr(config, "RESOLVED_WEB_SEARCH_PROVIDER", "")
    search_ready = (bool(getattr(config, "RESOLVED_ZHIPU_API_KEY", ""))
                    if provider == "zhipu_web_search" else deepseek_ready)
    if not search_ready:
        denied.add("deepseek_search")
    if not deepseek_ready:
        denied.update({"deepseek_fetch", "deepseek_fetch_paper"})
    return frozenset(denied)


def declarations() -> dict[str, tuple[frozenset[str], frozenset[str], str]]:
    """name -> (requires, produces, authorization) from the single registry."""
    from src.tools import _TOOL_BINDINGS

    return {name: (frozenset(requires), frozenset(produces), authorization)
            for name, (requires, produces, authorization) in _TOOL_BINDINGS.items()}


def tool_metadata() -> dict[str, dict]:
    """name -> typed capability facts from the low-level catalog."""
    from src.core.tool_catalog import TOOL_METADATA

    return {
        name: {
            "display_label": metadata.display_label,
            "link_label": metadata.link_label,
            "capabilities": frozenset(metadata.capabilities),
        }
        for name, metadata in TOOL_METADATA.items()
    }


def matching_producer_kinds(required_kinds) -> frozenset[str]:
    """A published figure package can supply a figure, but not vice versa.

    Format support remains a separate check: draw_svg's ``figure`` kind only
    supports SVG, while figure_package producers also publish PNG and PDF.
    """
    kinds = set(required_kinds or ())
    if "figure" in kinds:
        kinds.add("figure_package")
    return frozenset(kinds)


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


def unavailable_capability(name: str, env: Env) -> str:
    """Return a capability that is explicitly unavailable, or an empty string."""
    from src.core.tool_catalog import capabilities

    caps = capabilities(name)
    # Old clients do not send the visualization flag. Keep the existing
    # capability available until the request-scoped plan explicitly opts out.
    if "visualization" in caps and "visualization" in env.capability_denials:
        return "visualization"
    for capability in caps:
        if capability in env.capability_denials:
            return capability
    if not env.retrieval and caps & {"network", "local_corpus",
                                    "web_retrieval", "local_retrieval",
                                    "literature_retrieval"}:
        return "retrieval"
    return ""


def available_tool_names(env: Env) -> list[str]:
    """Names the model may be offered, preserving declaration order."""
    names = []
    for name in declarations():
        if name in env.denied:
            continue
        if missing_requirements(name, env) or unavailable_capability(name, env):
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
        capability = unavailable_capability(name, env)
        if capability == "visualization":
            reasons[name] = "本轮未开启可视化功能"
        elif capability == "retrieval":
            reasons[name] = NO_RETRIEVAL_LABEL
        elif capability:
            reasons[name] = "本轮请求策略未授权该来源能力"
        else:
            reasons[name] = "" if not missing else "；".join(_reason(t, env) for t in missing)
    return reasons


__all__ = [
    "AUTHORIZATION_EXPLICIT_USER_REQUEST", "KNOWN_AUTHORIZATIONS", "KNOWN_PRODUCES",
    "KNOWN_REQUIRES", "KNOWN_SCOPES", "NO_RETRIEVAL_LABEL", "REQUIREMENT_LABELS",
    "SCOPE_AUTO", "SCOPE_FUSION", "SCOPE_LABELS", "SCOPE_LOCAL", "SCOPE_WEB",
    "Env", "available_tool_names", "declarations", "explain", "missing_requirements",
    "matching_producer_kinds",
    "runtime_env", "script_sandbox_available", "tool_metadata",
    "unavailable_capability",
]
