"""Send view: verified navigation only, raw history otherwise, no model work."""
import asyncio
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.core import checkpoint_restore
from src.core.context_manager import ContextManager


class FakeSession:
    def __init__(self, summary, working_state=None):
        self.summary = summary
        self.working_state = working_state or {}

    def get_checkpoint(self, session_id):
        return {"msg_id": 2, "summary": self.summary, "revision": 1,
                "checkpoint_watermark": 2, "message_watermark": 4,
                "working_state": self.working_state}


class ExplodingBudget:
    def check(self, *args, **kwargs):
        raise AssertionError("load-time model compaction must not run")


def document():
    return {
        "schema": checkpoint_restore.SCHEMA,
        "generator": checkpoint_restore.GENERATOR,
        "navigation_authoritative": False,
        "owner_user_id": 5,
        "session_id": "s",
        "navigation": {"goal": "goal", "decisions": ["one"], "unfinished": []},
        "host_task_status": "completed",
        "host_task_state": {"turn_outcome": {"status": "completed"}},
        "source_inventory": [{"source_id": "1", "role": "human", "total_chars": 3,
                              "preview_sha256": "a"},
                             {"source_id": "2", "role": "ai", "total_chars": 3,
                              "preview_sha256": "b"}],
        "inventory_sha256": "digest",
        "tool_result_call_ids": [],
        "exact_excerpts": [{"source_id": "2", "start_char": 0, "end_char": 3,
                            "quote": "UNIQUE-EXCERPT", "quote_sha256": "hash"}],
    }


class SendViewTests(unittest.TestCase):
    def setUp(self):
        self.raw = [HumanMessage(content="u1"), AIMessage(content="a1"),
                    HumanMessage(content="u2"), AIMessage(content="a2")]
        self.ids = [1, 2, 3, 4]

    def view(self, summary, *, valid=None, working_state=None):
        manager = ContextManager(session_manager=FakeSession(summary, working_state),
                                 budget=ExplodingBudget())
        context = (patch.object(checkpoint_restore, "validate_checkpoint",
                                return_value=True) if valid is True else
                   patch.object(checkpoint_restore, "validate_checkpoint",
                                return_value=False) if valid is False else
                   patch.object(checkpoint_restore, "validate_checkpoint"))
        with context:
            return asyncio.run(manager._build_send_view("s", self.raw, self.ids, "u2"))

    def test_verified_deterministic_checkpoint_injects_bounded_navigation(self):
        summary = json.dumps(document())
        messages, compacted = self.view(summary, valid=True)
        self.assertFalse(compacted)
        self.assertEqual(messages[0].content.splitlines()[0], "<conversation_checkpoint>")
        rendered = "\n".join(str(m.content) for m in messages)
        self.assertIn("UNIQUE-EXCERPT", rendered)
        self.assertNotIn("preview_sha256", rendered)
        self.assertNotIn("u1", rendered)
        self.assertNotIn("a1", rendered)
        self.assertIn("u2", rendered)
        self.assertIn("a2", rendered)

    def test_legacy_prose_is_not_injected_and_raw_history_is_kept(self):
        messages, compacted = self.view("legacy model summary")
        self.assertFalse(compacted)
        self.assertEqual([m.content for m in messages], ["u1", "a1", "u2", "a2"])
        self.assertNotIn("<conversation_checkpoint>",
                         "\n".join(str(m.content) for m in messages))

    def test_failed_validation_keeps_every_raw_message(self):
        messages, compacted = self.view(json.dumps(document()), valid=False)
        self.assertFalse(compacted)
        self.assertEqual([m.content for m in messages], ["u1", "a1", "u2", "a2"])
        self.assertNotIn("<conversation_checkpoint>",
                         "\n".join(str(m.content) for m in messages))

    def test_working_state_is_kept_without_authorizing_prose(self):
        messages, _ = self.view("legacy model summary",
                                working_state={"verified": 1})
        self.assertTrue(messages[0].content.startswith("<context_working_state>"))
        self.assertEqual([m.content for m in messages[1:]], ["u1", "a1", "u2", "a2"])


if __name__ == "__main__":
    unittest.main()
