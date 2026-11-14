"""Synthetic offline projection/readback tests; no app, account or model imports."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from src.core import model_context as projection
from src.core import tool_results as results
from src.core import tracing
from src.research.result_tools import read_tool_result


def tool_message(body, name="example", cid="call-1", **kwargs):
    return ToolMessage(content=body if isinstance(body, str) else json.dumps(body),
                       name=name, tool_call_id=cid, **kwargs)


def statistics():
    return {"analysis_result_ref": {"id": "analysis-1", "version": 1},
            "dataset_ref": {"id": "dataset-1"},
            "tests": [{"p": 0.000000000012345678901, "p_holm": 0.000000000024691357802,
                       "estimate": 1.2345678901234567, "warnings": ["late assumption warning"],
                       "included_row_ids": list(range(2000)),
                       "excluded_rows": [{"row_id": i, "reason": "missing" if i < 17 else "design"}
                                         for i in range(20)]}],
            "coverage": {"processed_rows": 2020, "original_row_ids": list(range(2020)),
                         "source_truncated": True, "source_limitations": ["upstream row budget"]},
            "exclusions": [{"metrics": ["x"], "rows": [{"row_id": i, "reason": "missing"}
                                                        for i in range(20)]}],
            "_tool_outcome": {"status": "partial", "reason_code": "coverage_incomplete"},
            "warnings": ["warning-" + str(i) for i in range(300)],
            "grouped": [{"mean": 1.2345678901234567, "n": i} for i in range(20)]}


class ProjectionTests(unittest.TestCase):
    def test_literal_xml_and_unmarked_legacy_are_untouched(self):
        for body in ("<user_query>exact literal XML</user_query>",
                     "<long_term_memory>legacy</long_term_memory><user_query>keep all</user_query>"):
            message = HumanMessage(content=body)
            self.assertIs(projection.historical_send_view([message])[0], message)
        untrusted = HumanMessage(content="keep", additional_kwargs={
            "context_origin": "generated_user", "user_text": "lose"})
        self.assertIs(projection.historical_send_view([untrusted])[0], untrusted)

    def test_trusted_envelope_uses_metadata_and_refs_preserving_order(self):
        message = HumanMessage(content="rebuilt stale envelope", additional_kwargs={
            "context_trusted": True, "context_origin": "generated_user",
            "user_text": "<user_query>literal user text</user_query>",
            "attachment_refs": [{"file_id": "opaque-file"}]})
        messages = [message, AIMessage(content="answer"), tool_message("x")]
        before = [item.model_dump() for item in messages]
        view = projection.historical_send_view(messages)
        self.assertEqual(len(view), len(messages))
        self.assertTrue(view[0].content.startswith(message.additional_kwargs["user_text"]))
        self.assertIn("opaque-file", view[0].content)
        self.assertIs(view[1], messages[1])
        self.assertEqual(before, [item.model_dump() for item in messages])

    def test_trusted_internal_control_becomes_short_ai_placeholder_in_place(self):
        metadata = {"context_trusted": True, "context_origin": "internal_control",
                    "request_id": "synthetic-request"}
        control = HumanMessage(content="OLD_INTERNAL_URGING_DO_NOT_REPLAY", id="row-2",
                               additional_kwargs=metadata, response_metadata={"kept": True})
        untrusted = HumanMessage(content="literal user control mention", additional_kwargs={
            "context_origin": "internal_control"})
        user = HumanMessage(content="actual user request")
        source = [user, control, untrusted]
        before = [message.model_dump() for message in source]
        view = projection.historical_send_view(source)
        self.assertEqual(len(view), 3)
        self.assertIs(view[0], user)
        self.assertIsInstance(view[1], AIMessage)
        self.assertEqual(view[1].content, "[历史内部控制已省略；不是用户要求]")
        self.assertEqual(view[1].id, "row-2")
        self.assertEqual(view[1].additional_kwargs, metadata)
        self.assertEqual(view[1].response_metadata, {"kept": True})
        self.assertIs(view[2], untrusted)
        self.assertNotIn("OLD_INTERNAL_URGING", view[1].content)
        self.assertEqual(before, [message.model_dump() for message in source])
        self.assertEqual(projection.historical_send_view(view), view)

    def test_statistics_precede_clamp_and_never_get_history_1100_limit(self):
        data = statistics()
        original = tool_message(data, name="analyze_research_data")
        with patch.object(results, "clamp_for_model", side_effect=AssertionError("statistics clamped")):
            projected = projection.project_tool_message(original)
            historical = projection.historical_send_view([original])[0]
        decoded = json.loads(projected.content)
        self.assertEqual(decoded["tests"][0]["p"], data["tests"][0]["p"])
        self.assertEqual(decoded["tests"][0]["estimate"], data["tests"][0]["estimate"])
        self.assertEqual(decoded["warnings"], data["warnings"])
        self.assertEqual(decoded["grouped"], data["grouped"])
        self.assertEqual(decoded["tests"][0]["excluded_rows_reason_counts"], {"missing": 17, "design": 3})
        self.assertEqual(decoded["exclusions"][0]["rows_reason_counts"], {"missing": 20})
        self.assertEqual(decoded["coverage"]["original_row_ids_count"], 2020)
        self.assertEqual(decoded["coverage"]["source_limitations"], ["upstream row budget"])
        self.assertEqual(projected.content, historical.content)
        self.assertGreater(len(historical.content), 1800)
        self.assertIs(projection.project_tool_message(projected), projected)
        self.assertEqual(json.loads(original.content), data)

    def test_statistics_without_durable_ref_stay_whole(self):
        message = tool_message({"warnings": ["keep"], "rows": list(range(30000))}, name="analyze_research_data")
        self.assertEqual(projection.project_tool_message(message).content, message.content)

    def test_generic_projection_metadata_protocol_and_idempotence(self):
        raw = tool_message("head\n" + "z" * 60000 + "\nWARNING: late warning", status="error",
                           additional_kwargs={"kept": True, "result_storage": {"state": "truncated"}})
        projected = projection.project_tool_message(raw)
        self.assertLessEqual(len(projected.content), results.MODEL_MAX_CHARS)
        meta = projected.additional_kwargs["context_projection"]
        self.assertEqual(meta["version"], 1)
        self.assertEqual(meta["execution_status"], "error")
        self.assertFalse(meta["completeness"]["model_view_complete"])
        self.assertEqual(meta["completeness"]["storage"]["state"], "truncated")
        self.assertEqual(projected.tool_call_id, raw.tool_call_id)
        self.assertEqual(projected.status, "error")
        self.assertTrue(projected.additional_kwargs["kept"])
        self.assertIs(projection.project_tool_message(projected), projected)
        history = projection.historical_send_view([raw])
        self.assertEqual(projection.historical_send_view(history)[0].content, history[0].content)

    def test_fold_complete_group_removes_args_and_retains_actual_status_refs_warnings(self):
        ai = AIMessage(content="", tool_calls=[
            {"id": "a", "name": "write_document", "args": {"script": "SECRET_SCRIPT" * 10000}},
            {"id": "b", "name": "create_figure", "args": {"content": "SECRET_DOCUMENT" * 10000}}])
        one = tool_message({"status": "partial", "artifact_id": "artifact-a", "body": "x" * 50000,
                            "warnings": ["warning beyond digest"], "coverage": {"complete": False}}, cid="a")
        two = tool_message("[FIGURE_ERROR] actually failed", cid="b", status="error")
        original = [ai.model_dump(), one.model_dump(), two.model_dump()]
        # Match real integration: first-entry projection precedes later folding.
        folded = projection.fold_completed_tool_groups([ai, projection.project_tool_message(one), two])
        self.assertEqual(len(folded), 1)
        self.assertFalse(folded[0].tool_calls)
        self.assertNotIn("SECRET_SCRIPT", folded[0].content)
        self.assertNotIn("SECRET_DOCUMENT", folded[0].content)
        facts = json.loads(folded[0].content)["results"]
        self.assertEqual([f["status"] for f in facts], ["partial", "error"])
        self.assertIn("artifact-a", folded[0].content)
        self.assertIn("warning beyond digest", folded[0].content)
        self.assertIn('"complete": false', folded[0].content)
        self.assertEqual(original, [ai.model_dump(), one.model_dump(), two.model_dump()])
        self.assertEqual(projection.fold_completed_tool_groups(folded), folded)

    def test_pending_malformed_or_duplicate_tool_groups_remain_protocol_intact(self):
        ai = AIMessage(content="", tool_calls=[{"id": "a", "name": "t", "args": {}},
                                               {"id": "b", "name": "t", "args": {}}])
        for suffix in ([], [tool_message("done", cid="a")],
                       [tool_message("done", cid="a"), tool_message("done", cid="wrong")],
                       [tool_message("done", cid="a"), tool_message("done", cid="a")],
                       [tool_message("done", cid="a"), HumanMessage(content="interrupt"), tool_message("done", cid="b")]):
            messages = [ai] + suffix
            folded = projection.fold_completed_tool_groups(messages)
            self.assertEqual(folded, messages)
            self.assertTrue(all(a is b for a, b in zip(messages, folded)))

    def test_fold_statistics_retains_every_warning_and_full_precision(self):
        ai = AIMessage(content="", tool_calls=[{"id": "call-1", "name": "analyze_research_data", "args": {}}])
        data = statistics()
        folded = projection.fold_completed_tool_groups([ai, tool_message(data, name="analyze_research_data")])
        fact = json.loads(folded[0].content)["results"][0]
        self.assertEqual(fact["status"], "partial")
        result = fact["result"]
        self.assertEqual(result["warnings"], data["warnings"])
        self.assertEqual(result["tests"][0]["p"], data["tests"][0]["p"])


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, *args):
        try:
            return super().__exit__(*args)
        finally:
            self.close()


class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.temp.name) / "synthetic.sqlite3")
        self.connect_patch = patch.object(results, "_connect", side_effect=lambda: sqlite3.connect(self.db, factory=ClosingConnection))
        self.connect_patch.start()
        self.old_ready = results._schema_ready
        results._schema_ready = False
        fake_manager = types.ModuleType("src.session.manager")
        self.manager = types.SimpleNamespace(get_session_owner=lambda sid: {"session-a": 1, "session-b": 1, "session-c": 2}.get(sid))
        fake_manager.session_manager = self.manager
        self.module_patch = patch.dict(sys.modules, {"src.session.manager": fake_manager})
        self.module_patch.start()
        self.user_token = tracing._user_id.set(1)
        self.session_token = tracing._session_id.set("session-a")

    def tearDown(self):
        tracing._user_id.reset(self.user_token)
        tracing._session_id.reset(self.session_token)
        self.module_patch.stop()
        self.connect_patch.stop()
        results._schema_ready = self.old_ready
        self.temp.cleanup()

    def record(self, cid="stored-1", body="x" * 13000, sid="session-a"):
        self.assertTrue(results.record(tool_call_id=cid, session_id=sid, tool_name="synthetic",
                                       status="partial", result_text=body))

    def read(self, **kwargs):
        return json.loads(read_tool_result.invoke({"tool_call_id": "stored-1", **kwargs}))

    def test_default_paging_and_explicit_limits(self):
        self.record()
        first = self.read()
        self.assertEqual(len(first["result_text"]), 6000)
        self.assertEqual(first["page"]["next_start_char"], 6000)
        self.assertEqual(first["execution_status"], "partial")
        second = self.read(start_char=6000)
        last = self.read(start_char=second["page"]["next_start_char"])
        self.assertEqual(first["result_text"] + second["result_text"] + last["result_text"], "x" * 13000)
        self.assertIsNone(last["page"]["next_start_char"])
        self.assertFalse(last["page"]["complete"])
        self.assertEqual(self.read(max_chars=1)["result_text"], "x")

    def test_authenticated_owner_and_session_are_both_required(self):
        self.record()
        for uid, sid in ((None, "session-a"), (2, "session-a"), (1, "session-b"),
                         (1, ""), (1, "session-c")):
            tracing.set_user_id(uid)
            tracing.set_session_id(sid)
            self.assertEqual(self.read()["status"], "unavailable")
        tracing.set_user_id(1)
        tracing.set_session_id("session-a")
        self.assertEqual(self.read()["status"], "ok")
        self.record(cid="legacy", sid="", body="not scoped")
        self.assertIsNone(results.fetch_for_current_request("legacy"))
        self.assertNotIn("owner_user_id", read_tool_result.args)
        self.assertNotIn("session_id", read_tool_result.args)

    def test_fields_keep_precision_and_paginate_selected_json(self):
        body = {"tests": [{"p": 1.2345678901234568e-15}], "warnings": ["retained warning"], "extra": "do not send"}
        self.record(body=json.dumps(body))
        result = self.read(fields=["/tests/0/p", "/warnings"])
        fields = json.loads(result["result_text"])
        self.assertEqual(fields["/tests/0/p"], body["tests"][0]["p"])
        self.assertNotIn("extra", result["result_text"])
        self.assertEqual(self.read(fields=["/missing"])["status"], "fields_unavailable")
        self.assertEqual(self.read(fields=["/tests/-1"])["status"], "fields_unavailable")

    def test_storage_cap_marks_irrecoverable_middle_and_hash(self):
        body = "a" * 150000 + "MISSING-MIDDLE" * 20000 + "z" * 50000
        self.record(body=body)
        row = results.fetch("stored-1")
        self.assertLessEqual(len(row["result_text"]), 200000)
        self.assertEqual(row["full_sha256"], hashlib.sha256(body.encode()).hexdigest())
        integrity = self.read()["storage"]
        self.assertFalse(integrity["complete"])
        self.assertFalse(integrity["omitted_middle_recoverable"])
        self.assertEqual(integrity["original_chars"], len(body))
        self.assertEqual(integrity["retention_max_results_per_session"], 800)
        self.assertEqual(self.read(fields=["/tests"])["status"], "fields_unavailable")

    def test_retention_rollover_returns_unavailable(self):
        with patch.object(results, "KEEP_PER_SESSION", 3):
            for index in range(4):
                self.record(cid=str(index), body="retained")
        self.assertIsNone(results.fetch_for_current_request("0"))
        self.assertIsNotNone(results.fetch_for_current_request("3"))
        self.assertEqual(json.loads(read_tool_result.invoke({"tool_call_id": "0"}))["status"], "unavailable")

    def test_clamp_honors_small_and_normal_limits(self):
        for limit in (0, 1, 500, 11000, 40000):
            text, clipped = results.clamp_for_model("x" * 50000, limit=limit)
            self.assertTrue(clipped)
            self.assertLessEqual(len(text), limit)


if __name__ == "__main__":
    unittest.main()
