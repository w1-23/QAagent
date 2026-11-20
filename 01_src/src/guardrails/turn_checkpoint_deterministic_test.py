"""Deterministic post-turn checkpoints: model-free build, strict restore."""
import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage

from src.core import tracing
from src.core.checkpoint_restore import (GENERATOR, SCHEMA, navigation_view,
                                         structured_checkpoint, validate_checkpoint)
from src.guardrails import turn_checkpoint
from src.session import manager


class DeterministicCheckpointTests(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(TemporaryDirectory()))
        self.enterContext(patch.object(manager, "STATE_DIR", root))
        self.enterContext(patch.object(manager, "DB_PATH", root / "history.db"))
        self.enterContext(patch.object(manager.SessionManager, "_instance", None))
        self.manager = manager.SessionManager()
        self.sid = "det-session"
        self.owner = 77
        asyncio.run(self.manager.get_or_create_session(self.sid, owner_user_id=self.owner))
        prior_session, prior_user = tracing.get_session_id(), tracing.get_user_id()
        self.addCleanup(tracing.set_session_id, prior_session)
        self.addCleanup(tracing.set_user_id, prior_user)
        tracing.set_session_id(self.sid)
        tracing.set_user_id(self.owner)
        asyncio.run(self.manager.save_messages(self.sid, [
            HumanMessage(content="What changed?",
                         additional_kwargs={"request_id": "req-1"}),
            AIMessage(content="A" * 300,
                      additional_kwargs={"request_id": "req-1"}),
        ], idempotency_key="crid:req-1"))
        self.watermark = self.manager.get_checkpoint(self.sid)["message_watermark"]
        self.state = {
            "answer": "A" * 300,
            "query": "What changed?",
            "request_id": "req-1",
            "task_contract": {"query": "What changed?", "goal": "report"},
            "coverage": {"known": 1},
            "turn_outcome": {"status": "completed", "completion": {"remaining": []}},
        }

    def build(self, snapshot=None):
        snapshot = snapshot or self.manager.get_checkpoint(self.sid)
        return asyncio.run(turn_checkpoint.build_checkpoint(
            self.manager, self.sid, dict(self.state), snapshot))

    def stored(self):
        return json.loads(self.manager.get_checkpoint(self.sid)["summary"])

    def test_build_is_deterministic_and_validates(self):
        self.assertTrue(self.build())
        doc = self.stored()
        self.assertEqual(doc["schema"], SCHEMA)
        self.assertEqual(doc["generator"], GENERATOR)
        self.assertIs(doc["navigation_authoritative"], False)
        self.assertEqual(doc["host_task_status"], "completed")
        self.assertNotIn("query", doc["host_task_state"]["task_contract"])
        self.assertEqual(doc["source_inventory"][-1]["source_id"], str(self.watermark))
        inventory = doc["source_inventory"]
        canonical = json.dumps(inventory, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"))
        self.assertEqual(doc["inventory_sha256"],
                         hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        self.assertTrue(doc["exact_excerpts"])
        for item in doc["exact_excerpts"]:
            self.assertEqual(item["quote_sha256"], hashlib.sha256(
                item["quote"].encode("utf-8")).hexdigest())
        self.assertTrue(validate_checkpoint(doc, self.manager, self.sid, self.watermark))

    def test_legacy_prose_is_never_authoritative(self):
        self.assertIsNone(structured_checkpoint("legacy model summary"))
        self.assertFalse(validate_checkpoint("legacy model summary", self.manager,
                                             self.sid, self.watermark))
        self.assertTrue(self.build())
        doc = self.stored()
        legacy = dict(doc)
        legacy.pop("generator")
        self.assertFalse(validate_checkpoint(legacy, self.manager, self.sid, self.watermark))

    def test_tampered_documents_are_rejected(self):
        self.assertTrue(self.build())
        doc = self.stored()
        cases = []
        wrong_generator = json.loads(json.dumps(doc))
        wrong_generator["generator"] = "model"
        cases.append(wrong_generator)
        wrong_owner = json.loads(json.dumps(doc))
        wrong_owner["owner_user_id"] = self.owner + 1
        cases.append(wrong_owner)
        wrong_inventory = json.loads(json.dumps(doc))
        wrong_inventory["source_inventory"][0]["preview_sha256"] = "0" * 64
        cases.append(wrong_inventory)
        wrong_digest = json.loads(json.dumps(doc))
        wrong_digest["inventory_sha256"] = "0" * 64
        cases.append(wrong_digest)
        wrong_quote = json.loads(json.dumps(doc))
        wrong_quote["exact_excerpts"][0]["quote"] = "tampered"
        cases.append(wrong_quote)
        for case in cases:
            self.assertFalse(validate_checkpoint(case, self.manager, self.sid,
                                                 self.watermark))

    def test_owner_unavailable_defers_without_writing(self):
        before = self.manager.get_checkpoint(self.sid)["summary"]
        with patch.object(turn_checkpoint, "_owner", return_value=0):
            self.assertFalse(self.build())
        self.assertEqual(self.manager.get_checkpoint(self.sid)["summary"], before)

    def test_stale_snapshot_is_rejected_by_cas(self):
        snapshot = self.manager.get_checkpoint(self.sid)
        asyncio.run(self.manager.save_messages(self.sid, [
            HumanMessage(content="Later turn", additional_kwargs={"request_id": "req-2"}),
        ], idempotency_key="crid:req-2"))
        self.assertFalse(self.build(snapshot))
        self.assertEqual(self.manager.get_checkpoint(self.sid)["summary"], "")

    def test_navigation_view_is_bounded(self):
        data = {"navigation": {"goal": "G" * 5000,
                               "decisions": ["D" * 1000] * 40,
                               "unfinished": ["U" * 1000] * 40},
                "source_inventory": [{"source_id": str(i), "role": "human",
                                      "total_chars": 10, "preview_sha256": "x"}
                                     for i in range(200)],
                "exact_excerpts": [{"source_id": "1", "start_char": 0,
                                    "end_char": 2000, "quote": "Q" * 2000}] * 5,
                "tool_result_call_ids": []}
        text = navigation_view(data, limit=600)
        self.assertLessEqual(len(text), 600)
        self.assertNotIn("preview_sha256", text)

    def test_tool_evidence_is_pinned_and_bound(self):
        from langchain_core.messages import ToolMessage
        from src.core import tool_results
        tool_results._schema_ready = False
        body = "FULL-ORIGINAL-" * 500
        asyncio.run(self.manager.save_messages(self.sid, [
            AIMessage(content="", additional_kwargs={"request_id": "req-tool"},
                      tool_calls=[{"id": "call-1", "name": "read_data", "args": {}}]),
            ToolMessage(content=body, tool_call_id="call-1", name="read_data",
                        additional_kwargs={"request_id": "req-tool"}),
            AIMessage(content="final", additional_kwargs={"request_id": "req-tool"}),
        ], idempotency_key="crid:req-tool"))
        self.assertTrue(tool_results.record(
            tool_call_id="call-1", session_id=self.sid, request_id="req-tool",
            tool_name="read_data", status="ok", result_text=body))
        watermark = self.manager.get_checkpoint(self.sid)["message_watermark"]
        state = dict(self.state, request_id="req-tool", answer="final")
        self.assertTrue(asyncio.run(turn_checkpoint.build_checkpoint(
            self.manager, self.sid, state, self.manager.get_checkpoint(self.sid))))
        doc = self.stored()
        self.assertEqual(doc["tool_result_call_ids"], ["call-1"])
        self.assertTrue(validate_checkpoint(doc, self.manager, self.sid, watermark))
        row = tool_results.fetch("call-1")
        self.assertTrue(row["pinned"])
        self.assertEqual(row["full_sha256"],
                         hashlib.sha256(body.encode("utf-8")).hexdigest())
        # Losing complete evidence invalidates the checkpoint instead of hiding rows.
        with tool_results._connect() as conn:
            conn.execute("UPDATE tool_results SET retained=0 WHERE tool_call_id='call-1'")
            conn.commit()
        self.assertFalse(validate_checkpoint(doc, self.manager, self.sid, watermark))


if __name__ == "__main__":
    unittest.main()
