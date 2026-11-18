"""Pinned Deep Agents factory with host-owned execution and file boundaries.

The SDK owns the graph. HostTools owns every actual tool invocation, and the
existing working-context projection owns compression. No native filesystem,
subagent, synthetic tool repair or second summarizer is installed in the graph.
"""
from __future__ import annotations

from functools import cache

from deepagents import (GeneralPurposeSubagentProfile, HarnessProfile,
                        create_deep_agent, register_harness_profile)
from deepagents.backends.protocol import BackendProtocol
from langchain.agents.middleware import AgentMiddleware

SDK_VERSION = "0.7.38"
EXECUTOR_ID = "citrus-deepagents-0.7.38-host-v1"
MODEL_PROVIDER = "citrus_metered"


class HostOnlyBackend(BackendProtocol):
    """No native SDK I/O. Typed workspace tools carry scope, CAS and receipts.

    Deliberately not a SandboxBackendProtocol. The typed file tools provide the
    projected mounts; native writes cannot infer a current CAS or bypass them.
    """
    def __init__(self, identity):
        self.identity = identity

    def _deny(self, *args, **kwargs):
        raise PermissionError("host_scoped_file_tool_required")

    ls = read = grep = glob = write = edit = delete = upload_files = download_files = _deny


class HostFilesystemSlot(AgentMiddleware):
    """Replace the SDK filesystem slot with the registered host file tools.

    Same-name replacement is supported by the pinned SDK. Stock filesystem
    permission/offload hooks are intentionally absent; the host enforces access.
    """
    tools = ()

    @property
    def name(self):
        return "FilesystemMiddleware"

    def bind_backend(self, backend):
        self.backend = backend

    async def awrap_model_call(self, request, handler):
        # The factory authors an empty system message by default. Existing host
        # controls already live in history; do not add an extra empty control.
        if request.system_message is not None and not request.system_message.content:
            request = request.override(system_message=None)
        return await handler(request)


@cache
def configure_host_profile():
    import deepagents
    if deepagents.__version__ != SDK_VERSION:
        raise RuntimeError("unsupported_deepagents_version")
    # These private SDK hooks are deliberately version-pinned. Using the exact
    # assembled caching names also handles optional provider integrations without
    # unmatched profile exclusions or global patches to the SDK.
    from deepagents.middleware._parent_notifier import _ParentNotifierMiddleware
    from deepagents.middleware._prompt_caching import append_prompt_caching_middleware
    caching = []
    append_prompt_caching_middleware(caching)
    excluded = {"SummarizationMiddleware", "PatchToolCallsMiddleware",
                "UnsupportedContentMiddleware", "ModelRefusalMiddleware",
                _ParentNotifierMiddleware, *(item.name for item in caching)}
    register_harness_profile(MODEL_PROVIDER, HarnessProfile(
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False),
        excluded_middleware=frozenset(excluded)))


def create_host_agent(model, tools, policy, saver, state_schema):
    configure_host_profile()
    return create_deep_agent(model=model, tools=tools,
        middleware=[HostFilesystemSlot(), policy],
        subagents=[], skills=None, memory=None,
        backend=HostOnlyBackend(model.identity),
        checkpointer=saver, state_schema=state_schema,
        name="citrus_main_loop")
