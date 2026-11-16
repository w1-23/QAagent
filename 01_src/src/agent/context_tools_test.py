"""Public context readback preserves original pages and enforces server scope."""
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from src.agent.host_test import environment
from src.agent.context_projection import EncryptedContextArchive
from src.agent.context_tools import read_context_evidence
from src.agent.identity import ExecutionIdentity
from src.core import tracing


def test_context_reader_pages_exact_public_bytes_and_rejects_other_owner(environment):
    state, job_id = environment
    from src.config import PROJECT_ROOT, settings, get_deepseek_model
    identity = ExecutionIdentity(17, state["session_id"], state["request_id"], job_id)
    scope = (settings.RESOLVED_MAIN_BASE_URL, get_deepseek_model())
    async def scenario():
        archive = EncryptedContextArchive(PROJECT_ROOT / "state", identity, scope)
        original = HumanMessage(content="原始数值 0.123456789\n" * 20)
        ref = await archive.archive([original, AIMessage(content="observed")])
        args = {"checkpoint_id": ref.checkpoint_id, "message_count": 2,
                "message_index": 0, "start_char": 0, "max_chars": 71}
        pages, hashes = [], set()
        while True:
            value = json.loads(await read_context_evidence.ainvoke(args))
            pages.append(value["text"])
            hashes.add(value["public_message_sha256"])
            if value["next_start_char"] is None:
                break
            args["start_char"] = value["next_start_char"]
        assert json.loads("".join(pages))["data"]["content"] == original.content
        assert len(hashes) == 1
        tracing.set_user_id(18)
        with pytest.raises(PermissionError, match="scope_unavailable"):
            await read_context_evidence.ainvoke(args)
    asyncio.run(scenario())
