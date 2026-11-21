"""Deterministic post-turn checkpoints: model-free build, strict restore."""
import asyncio
import hashlib
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.core import tracing
from src.core.context_manager import ContextManager, finalize_load_result
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

    def restore(self, *, user_id=None, memory=None):
        with patch.object(manager.SessionManager, "_instance", None):
            reopened = manager.SessionManager()
            loader = ContextManager(session_manager=reopened, memory_store=memory)
            return asyncio.run(loader.load(self.sid, "follow-up", "general_agent",
                                          user_id=user_id or self.owner, generate_hints=False))

    def test_reopened_preview_only_checkpoint_falls_back_and_can_be_rebuilt(self):
        self.assertTrue(self.build())
        old = self.stored()
        old["schema"] = "citrus-turn-checkpoint/1"
        with manager._connect_db(self.manager.db_path) as conn:
            conn.execute("UPDATE sessions SET checkpoint_summary=? WHERE session_id=?",
                         (json.dumps(old), self.sid))
            conn.commit()
        ctx = self.restore()
        self.assertEqual((ctx.history_load_status, ctx.history_load_reason),
                         ("degraded", "legacy_checkpoint_untrusted"))
        self.assertEqual([m.content for m in ctx.history_messages], ["What changed?", "A" * 300])
        self.assertTrue(self.build())
        restored = self.restore()
        self.assertEqual((restored.history_load_status, restored.history_load_reason), ("loaded", ""))
        self.assertEqual(len(restored.history_messages), 1)
        self.assertTrue(restored.history_messages[0].content.startswith("<conversation_checkpoint>"))

    def test_missing_covered_row_preserves_remaining_history(self):
        self.assertTrue(self.build())
        with manager._connect_db(self.manager.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id=? AND msg_type='human'", (self.sid,))
            conn.commit()
        ctx = self.restore()
        self.assertEqual(ctx.history_load_reason, "raw_history_incomplete")
        self.assertEqual([m.content for m in ctx.history_messages], ["A" * 300])

    def test_corrupt_body_outside_checkpoint_excerpts_is_excluded(self):
        asyncio.run(self.manager.save_messages(self.sid, [AIMessage(
            content="final", additional_kwargs={"request_id": "req-2"})], idempotency_key="crid:req-2"))
        self.state.update(request_id="req-2", answer="final")
        self.assertTrue(self.build())
        with manager._connect_db(self.manager.db_path) as conn:
            conn.execute("UPDATE messages SET content=? WHERE id=?",
                         ("A" * 200 + "X" + "A" * 99, self.watermark))
            conn.commit()
        ctx = self.restore()
        self.assertEqual(ctx.history_load_reason, "source_integrity_failed")
        self.assertEqual([m.content for m in ctx.history_messages], ["What changed?", "final"])

    def test_different_checkpoint_owner_preserves_authorized_messages(self):
        self.assertTrue(self.build())
        checkpoint = self.stored()
        checkpoint["owner_user_id"] = self.owner + 1
        with manager._connect_db(self.manager.db_path) as conn:
            conn.execute("UPDATE sessions SET checkpoint_summary=? WHERE session_id=?",
                         (json.dumps(checkpoint), self.sid))
            conn.commit()
        ctx = self.restore()
        self.assertTrue(ctx.history_scope_authorized)
        self.assertEqual(ctx.history_load_reason, "owner_scope_mismatch")
        self.assertEqual([m.content for m in ctx.history_messages], ["What changed?", "A" * 300])

    def test_wrong_request_scope_performs_no_account_or_evidence_reads(self):
        memory = Mock()
        tracing.set_user_id(self.owner + 1)
        with patch("src.session.files.list_account_files") as files:
            ctx = self.restore(user_id=self.owner + 1, memory=memory)
            files.assert_not_called()
        with patch.object(self.manager, "build_evidence_block") as evidence:
            result = finalize_load_result(ctx, session_manager=self.manager, session_id=self.sid,
                                          budget=None, node_label="session_load", log_prefix="test")
            evidence.assert_not_called()
        self.assertFalse(memory.mock_calls)
        self.assertFalse(ctx.history_scope_authorized)
        self.assertEqual(result["history_load_reason"], "owner_scope_mismatch")
        self.assertEqual(result["history_load_status"], "failed")
        self.assertNotIn("messages", result)

    def test_reopened_legacy_tool_groups_keep_only_exactly_paired_rows(self):
        def batch(call_id):
            return [AIMessage(content="", tool_calls=[{"id": call_id, "name": "read_data", "args": {}}]),
                    ToolMessage(content=call_id, tool_call_id=call_id, name="read_data")]

        asyncio.run(self.manager.save_messages(self.sid, [*batch("intact"), *batch("broken"),
                                                           AIMessage(content="final")]))
        with manager._connect_db(self.manager.db_path) as conn:
            conn.execute("UPDATE messages SET msg_type='AIMessageChunk' "
                         "WHERE session_id=? AND tool_calls_json LIKE '%intact%'", (self.sid,))
            conn.execute("UPDATE messages SET tool_call_id='wrong' "
                         "WHERE session_id=? AND tool_call_id='broken'", (self.sid,))
            conn.commit()
        ctx = self.restore()
        self.assertEqual(ctx.history_load_reason, "raw_history_incomplete")
        self.assertEqual(ctx.history_messages[2].tool_calls[0]["id"], "intact")
        self.assertEqual(ctx.history_messages[3].tool_call_id, "intact")
        self.assertEqual([m.content for m in ctx.history_messages],
                         ["What changed?", "A" * 300, "", "intact", "final"])
        self.assertNotIn("host_publication", ctx.history_messages[3].additional_kwargs)

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
        self.assertEqual(doc["tool_result_sha256"], {"call-1": row["full_sha256"]})
        with tool_results._connect() as conn:
            conn.execute("UPDATE tool_results SET pinned=0 WHERE tool_call_id='call-1'")
            conn.commit()
        self.assertEqual(self.restore().history_load_reason, "raw_history_incomplete")
        with tool_results._connect() as conn:
            conn.execute("UPDATE tool_results SET pinned=1, full_sha256=? WHERE tool_call_id='call-1'",
                         ("0" * 64,))
            conn.commit()
        self.assertEqual(self.restore().history_load_reason, "source_integrity_failed")
        # Losing complete evidence invalidates the checkpoint instead of hiding rows.
        with tool_results._connect() as conn:
            conn.execute("UPDATE tool_results SET retained=0 WHERE tool_call_id='call-1'")
            conn.commit()
        self.assertFalse(validate_checkpoint(doc, self.manager, self.sid, watermark))


if __name__ == "__main__":
    unittest.main()
