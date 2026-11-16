import asyncio
import json
from langchain_core.messages import HumanMessage, SystemMessage
from src.guardrails.test_turn_checkpoint import store, fake_model
from src.guardrails.turn_checkpoint import build_checkpoint
from src.core.context_manager import ContextManager


def test_restored_checkpoint_reads_exact_sources_and_rejects_changed_quote(store, fake_model):
    manager, raw = store
    state = {"answer": "done", "request_id": "r1", "turn_outcome": {"status": "completed"}}
    assert asyncio.run(build_checkpoint(manager, "s", state, manager.get_checkpoint("s")))
    messages, ids = asyncio.run(manager.get_messages_with_ids("s"))
    loader = ContextManager(session_manager=manager)
    view, _ = asyncio.run(loader._build_send_view("s", messages, ids, "next"))
    assert len(view) == 1 and isinstance(view[0], SystemMessage)
    assert "精确 1.23456789" in view[0].content
    assert len(fake_model) == 1
    snapshot = manager.get_checkpoint("s")
    forged = json.loads(snapshot["summary"])
    forged["exact_excerpts"][0]["quote"] = "changed value"
    assert manager.set_checkpoint("s", ids[-1], json.dumps(forged))
    view, _ = asyncio.run(loader._build_send_view("s", messages, ids, "next"))
    assert any(isinstance(m, HumanMessage) and m.content == raw for m in view)
    assert not any("changed value" in str(m.content) for m in view)
    assert len(fake_model) == 1
