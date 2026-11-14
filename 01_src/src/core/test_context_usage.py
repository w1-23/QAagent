"""Focused offline cache/attempt observations; no clients, model calls or fixtures."""
import asyncio
import copy
import json
import pathlib
import tempfile
import unittest
from types import SimpleNamespace

from src.core import cache_metrics, progress_bus, turn_context


class UsageTests(unittest.TestCase):
    def test_missing_cache_is_unknown_and_missing_tokens_are_not_zero(self):
        usage = cache_metrics.extract_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 10}})
        self.assertEqual(usage["input_tokens"], 100)
        self.assertEqual(usage["total_tokens"], 110)
        self.assertEqual(usage["cache_status"], "unavailable")
        for key in ("cache_read", "cache_write", "cache_uncached", "cache_hit", "cache_miss", "cache_ratio"):
            self.assertIsNone(usage[key])
        self.assertIsNone(cache_metrics.extract_usage({"usage": {"output_tokens": 3}})["input_tokens"])
        self.assertIsNone(cache_metrics.extract_usage({}))

    def test_explicit_all_zero_usage_remains_a_reported_response(self):
        usage = cache_metrics.extract_usage({"usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}})
        self.assertIsNotNone(usage)
        self.assertEqual(usage["input_tokens"], 0)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertEqual(usage["total_tokens"], 0)
        self.assertEqual(usage["total_tokens_source"], "usage.total_tokens")
        self.assertEqual(usage["cache_status"], "unavailable")
        self.assertIsNone(usage["cache_read"])

    def test_openai_cached_tokens_do_not_imply_uncached_input(self):
        response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=100, completion_tokens=5,
            prompt_tokens_details=SimpleNamespace(cached_tokens=40)))
        usage = cache_metrics.extract_usage(response)
        self.assertEqual(usage["cache_read"], 40)
        self.assertEqual(usage["cache_status"], "partial")
        self.assertIsNone(usage["cache_uncached"])
        self.assertIsNone(usage["cache_ratio"])
        self.assertEqual(usage["cache_sources"]["read"], "usage.prompt_tokens_details.cached_tokens")

    def test_creation_is_write_never_uncached_or_miss(self):
        usage = cache_metrics.extract_usage({"usage": {"input_tokens": 10, "output_tokens": 2,
            "cache_read_input_tokens": 40, "cache_creation_input_tokens": 30}})
        self.assertEqual(usage["cache_read"], 40)
        self.assertEqual(usage["cache_write"], 30)
        self.assertIsNone(usage["cache_uncached"])
        self.assertIsNone(usage["cache_miss"])
        self.assertIsNone(usage["cache_ratio"])
        self.assertEqual(usage["total_tokens"], 12)  # Provider input semantics are preserved.

    def test_deepseek_reported_miss_and_zero_are_preserved(self):
        usage = cache_metrics.extract_usage({"response_metadata": {"token_usage": {
            "prompt_tokens": 100, "completion_tokens": 0,
            "prompt_cache_hit_tokens": 40, "prompt_cache_miss_tokens": 60}}})
        self.assertEqual(usage["cache_read"], 40)
        self.assertEqual(usage["cache_uncached"], 60)
        self.assertIsNone(usage["cache_write"])
        self.assertEqual(usage["cache_ratio"], .4)
        self.assertEqual(usage["output_tokens"], 0)
        self.assertIn("reported_read", usage["cache_ratio_basis"])

    def test_langchain_details_and_bad_counts(self):
        usage = cache_metrics.extract_usage({"usage_metadata": {"input_tokens": 0,
            "output_tokens": 0, "input_token_details": {"cache_read": 0, "cache_creation": 20}}})
        self.assertEqual(usage["cache_write"], 20)
        self.assertEqual(usage["cache_read"], 0)
        self.assertIsNone(usage["cache_uncached"])
        usage = cache_metrics.extract_usage({"usage": {"prompt_tokens": 4,
            "cached_tokens": -1, "cache_creation_input_tokens": "NaN", "uncached_tokens": True}})
        self.assertEqual(usage["cache_status"], "unavailable")

    def test_emitter_carries_identity_and_only_explicit_dimensions(self):
        queue = asyncio.Queue()
        progress_bus.set_request_queue(queue)
        progress_bus.set_turn_scope("session", "request")
        try:
            cache_metrics.emit_usage_from_response("session", "supervisor", {"usage": {
                "prompt_tokens": 100, "completion_tokens": 2, "cache_creation_input_tokens": 30}},
                attempt_id="attempt-2", logical_call_id="logical-1", epoch=3,
                parent_purpose="supervisor")
            event = json.loads(queue.get_nowait()["data"])
            self.assertEqual(event["scope"], "single_call")
            self.assertEqual(event["observation_scope"], "physical_attempt")
            self.assertEqual(event["request_id"], "request")
            self.assertEqual(event["session_id"], "session")
            self.assertEqual(event["attempt_id"], "attempt-2")
            self.assertEqual(event["epoch"], 3)
            self.assertEqual(event["parent_purpose"], "supervisor")
            self.assertEqual(event["call"]["cache_write"], 30)
            self.assertIsNone(event["call"]["cache_miss"])
            self.assertIsNone(event["call"]["cache_ratio"])
            self.assertEqual(event["total"], 102)
        finally:
            progress_bus.clear_request_queue()
            progress_bus.clear_turn_scope()


class PreparedAttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_db = turn_context._DB_PATH
        turn_context._DB_PATH = pathlib.Path(self.temp.name) / "test.db"
        progress_bus.set_turn_scope("session", "request")
        self.layout_token = turn_context._previous_layout.set(None)
        self.payload = {"model": "offline", "messages": [
            {"role": "system", "content": "private-system"},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": "private-image"}}]},
            {"role": "assistant", "content": "", "reasoning_content": "private-reasoning",
             "tool_calls": [{"id": "call", "function": {"name": "stub", "arguments": "private-args"}}]}],
            "tools": [{"type": "function", "function": {"name": "stub", "description": "private-schema"}}],
            "response_format": {"type": "json_object"}, "extra_body": {"credential": "private-secret"}}

    def tearDown(self):
        turn_context._DB_PATH = self.old_db
        turn_context._previous_layout.reset(self.layout_token)
        progress_bus.clear_turn_scope()
        self.temp.cleanup()

    def capture(self, attempt_id, payload=None, **kwargs):
        return turn_context.capture_request(payload or self.payload, model="offline", purpose="supervisor",
                                            attempt_id=attempt_id, logical_call_id="logical", **kwargs)

    def test_prepared_request_complete_payload_is_covered_without_content(self):
        original = copy.deepcopy(self.payload)
        snapshot = self.capture("one")
        self.assertEqual(self.payload, original)
        self.assertEqual(snapshot["scope"], "physical_attempt")
        self.assertEqual(snapshot["msg_count"], 3)
        self.assertGreater(snapshot["bytes"], 100)
        self.assertEqual(snapshot["parts"][0]["kind"], "tools")
        self.assertIsNone(snapshot["common_prefix"])
        for field in ("tools", "response_format", "extra_body"):
            changed = copy.deepcopy(self.payload)
            changed[field] = {"changed": True}
            self.assertNotEqual(self.capture(field, changed)["hash"], snapshot["hash"])
        for field in ("reasoning_content", "tool_calls"):
            changed = copy.deepcopy(self.payload)
            changed["messages"][2][field] = "changed"
            self.assertNotEqual(self.capture(field, changed)["messages"][2]["hash"], snapshot["messages"][2]["hash"])
        turn_context.record(snapshot, session_id="session", request_id="request", usage={"input_tokens": 10})
        serialized = json.dumps(turn_context.latest("session"))
        self.assertNotIn("private-", serialized)
        self.assertNotIn("credential", serialized)
        self.assertNotIn("preview", serialized)

    def test_retries_and_epoch_capture_physical_attempts_and_prefix(self):
        first = self.capture("one", epoch=0)
        turn_context.record(first, session_id="session", request_id="request", error="TimeoutError")
        retry = self.capture("two", epoch=0)
        self.assertEqual(retry["change_reason"], "unchanged")
        self.assertEqual(retry["common_prefix"]["parts"], len(first["parts"]))
        self.assertEqual(retry["common_prefix"]["previous_attempt_id"], "one")
        turn_context.record(retry, session_id="session", request_id="request", usage={"input_tokens": 20})
        changed = copy.deepcopy(self.payload)
        changed["messages"].append({"role": "tool", "content": "private-result"})
        checkpoint = self.capture("three", changed, epoch=1)
        self.assertEqual(checkpoint["change_reason"], "epoch_changed")
        self.assertEqual(checkpoint["common_prefix"]["parts"], 4)  # tools + 3 unchanged messages
        turn_context.record(checkpoint, session_id="session", request_id="request", usage={"input_tokens": 30})
        window = turn_context.latest("session", limit=1)
        self.assertEqual(window["physical_attempts"], 1)
        self.assertEqual(window["input_tokens"], 30)
        self.assertEqual(window["scope"], "returned_window")
        self.assertFalse(window["is_task_total"])
        all_rows = turn_context.latest("session")
        self.assertEqual(all_rows["physical_attempts"], 3)
        self.assertEqual(all_rows["totals_by_scope"]["physical_attempt"]["input_tokens_reported_calls"], 2)
        self.assertIsNone(all_rows["rows"][-1]["input_tokens"])

    def test_logical_and_physical_observations_are_not_added_together(self):
        logical = turn_context.capture([SimpleNamespace(type="human", content="private-user")])
        turn_context.record(logical, session_id="session", usage={"input_tokens": 10})
        turn_context.record(self.capture("one"), session_id="session", usage={"input_tokens": 10})
        result = turn_context.latest("session")
        self.assertEqual(result["logical_calls"], 1)
        self.assertEqual(result["physical_attempts"], 1)
        self.assertIsNone(result["input_tokens"])
        self.assertEqual(result["totals_by_scope"]["physical_attempt"]["input_tokens"], 10)

    def test_prefix_journal_survives_separate_transport_tasks(self):
        async def exercise():
            async def observe(attempt_id):
                return self.capture(attempt_id)
            first = await asyncio.wait_for(observe("one"), timeout=1)
            second = await asyncio.wait_for(observe("two"), timeout=1)
            self.assertEqual(second["common_prefix"]["previous_attempt_id"], "one")
            self.assertEqual(second["common_prefix"]["parts"], len(first["parts"]))
            async def other_request():
                progress_bus.set_turn_scope("session", "isolated")
                return self.capture("isolated")
            isolated = await asyncio.create_task(other_request())
            self.assertIsNone(isolated["common_prefix"])
            third = await asyncio.wait_for(observe("three"), timeout=1)
            self.assertEqual(third["common_prefix"]["previous_attempt_id"], "two")
        asyncio.run(exercise())

    def test_sdk_observation_bridge_only_captures_an_active_attempt(self):
        from src.core.request_context import _observation, observe_prepared_request
        scope = {"purpose": "supervisor", "attempt_id": "one", "epoch": 2, "logical_call_id": "logical"}
        token = _observation.set(scope)
        try:
            observe_prepared_request(self.payload)
        finally:
            _observation.reset(token)
        self.assertEqual(scope["snapshot"]["attempt_id"], "one")
        self.assertEqual(scope["snapshot"]["epoch"], 2)
        self.assertEqual(scope["snapshot"]["hash"], self.capture("two", epoch=2)["hash"])
        token = _observation.set(None)
        try:
            observe_prepared_request(self.payload)  # Admission estimates are not attempts.
        finally:
            _observation.reset(token)

    def test_scope_and_purpose_isolate_automatic_prefix_comparison(self):
        self.capture("one")
        progress_bus.set_turn_scope("session", "other-request")
        other = self.capture("two")
        self.assertIsNone(other["common_prefix"])
        self.assertEqual(other["change_reason"], "initial_attempt")
        snapshot = turn_context.capture_request(self.payload, purpose="helper", attempt_id="three")
        self.assertIsNone(snapshot["common_prefix"])


if __name__ == "__main__":
    unittest.main()
