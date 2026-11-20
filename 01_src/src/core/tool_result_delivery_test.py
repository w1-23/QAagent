"""Original evidence survives delivery, SQLite reopen, and scoped paging."""
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_openai import ChatOpenAI

from src.agent.context_projection import WorkingContextProjection
from src.agent.identity import ExecutionIdentity
from src.agent.model import ModelDriver
from src.agent.runtime import AdmissionStopped
from src.core import jobs, task_events, tool_results, tracing
from src.core.context_manager import ContextManager
from src.core.llm_pool import _ObservedDeepSeekChatOpenAI
from src.core.provider_protocol import _DeepSeekMessage, _ProtocolState
from src.core.request_context import model_request
from src.core.tool_result_view import FIRST_DELIVERY_BYTES, first_delivery
from src.research.result_tools import read_tool_result
from src.session import manager as sessions


class FixtureCatalog:
    def schemas(self, allowed):
        assert allowed == {"analyze"}
        return [{"type": "function", "function": {"name": "analyze", "description": "Synthetic analysis",
                 "parameters": {"type": "object", "properties": {}}}}]

    def execution_mode(self, name):
        assert name == "analyze"
        return "interaction"


class ToolResultDeliveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(sessions, "STATE_DIR", root))
        self.enterContext(patch.object(sessions, "DB_PATH", root / "history.sqlite3"))
        self.enterContext(patch.object(sessions.SessionManager, "_instance", None))
        self.enterContext(patch.object(tool_results, "_schema_ready", False))
        self.session_store = sessions.SessionManager()
        self.enterContext(patch.object(sessions, "session_manager", self.session_store))
        self.sid = await self.session_store.get_or_create_session(None, 7)
        self.previous_identity = (tracing.get_user_id(), tracing.get_session_id())
        self.addCleanup(tracing.set_user_id, self.previous_identity[0])
        self.addCleanup(tracing.set_session_id, self.previous_identity[1])
        tracing.set_user_id(7)
        tracing.set_session_id(self.sid)

    def delivery(self, call_id, content):
        reference = tool_results.record_with_reference(tool_call_id=call_id, session_id=self.sid,
            request_id="request", tool_name="analyze", status="complete", result_text=content)
        self.assertIsNotNone(reference)
        original = ToolMessage(content=content, name="analyze", tool_call_id=call_id,
                               additional_kwargs={"result_reference": reference},
                               artifact={"op_receipt": {"status": "complete"}})
        return first_delivery(original, call={}, reader_available=True)

    def serialized(self, text):
        return len(json.dumps(text, ensure_ascii=False).encode("utf-8"))

    async def test_ten_and_thirty_outputs_are_bounded_in_real_adapter_payload(self):
        body = '{"status":"complete","next_action":"read","counts":{"rows":1200},"data":"' + "F" * 10240 + '"}'
        calls = [{"name": "analyze", "args": {}, "id": f"call-{i}"} for i in range(30)]
        messages = [self.delivery(call["id"], body) for call in calls]
        identity = ExecutionIdentity(7, self.sid, "request", "run")
        model = ChatOpenAI(model="fixture", api_key="fixture", max_retries=0, max_tokens=4096,
                          base_url="http://127.0.0.1:9/v1")
        driver = ModelDriver(model, FixtureCatalog(), lambda _: {"analyze"})
        for count in (10, 30):
            with self.subTest(count=count):
                view = [HumanMessage(content="Analyze synthetic data"),
                        AIMessage(content="", tool_calls=calls[:count]), *messages[:count]]
                payload, estimate = model_request(model.bind_tools(driver.schemas_for(identity)), view)
                self.assertTrue(payload["tools"][0]["function"]["strict"])
                self.assertEqual(estimate["tool_message_count"], count)
                self.assertEqual(estimate["output_tokens"], 4096)
                self.assertLess(estimate["estimated_input_tokens"], 48000)
                tool_views = [m for m in payload["messages"] if m["role"] == "tool"]
                self.assertLessEqual(sum(self.serialized(m["content"]) for m in tool_views),
                                     count * FIRST_DELIVERY_BYTES)
                for n, m in enumerate(tool_views):
                    data = json.loads(m["content"])
                    self.assertEqual(m["tool_call_id"], calls[n]["id"])
                    self.assertEqual(data["control_observation"]["next_action"], "read")
                    self.assertEqual(data["control_observation"]["counts"]["rows"], "1200")
                    self.assertEqual(data["tool_result_ref"]["full_sha256"], hashlib.sha256(body.encode()).hexdigest())

    async def test_reopened_legacy_checkpoint_preserves_history_and_exact_paged_evidence(self):
        content = '{"p":0.012345678901234567,"text":' + json.dumps("研究数据" * 4000, ensure_ascii=False) + '}'
        first = self.delivery("persisted-call", content)
        self.assertLessEqual(self.serialized(first.content), FIRST_DELIVERY_BYTES)
        self.assertTrue(tool_results.pin_references(self.sid, [first.tool_call_id]))
        expected_history = [HumanMessage(content="old question"), AIMessage(content="old answer"),
                            HumanMessage(content="new question")]
        self.assertTrue(await self.session_store.save_messages(self.sid, expected_history))
        _, ids = await self.session_store.get_messages_with_ids(self.sid)
        with sessions._connect_db(self.session_store.db_path) as conn:
            conn.execute("UPDATE sessions SET checkpoint_msg_id=?, checkpoint_summary=? WHERE session_id=?",
                         (ids[1], "UNVERIFIED legacy summary", self.sid))
            conn.commit()
        sessions.SessionManager._instance = None
        tool_results._schema_ready = False
        reopened = sessions.SessionManager()
        with patch.object(sessions, "session_manager", reopened):
            history, reopened_ids = await reopened.get_messages_with_ids(self.sid)
            view, compacted = await ContextManager(session_manager=reopened)._build_send_view(
                self.sid, history, reopened_ids, "new question")
            self.assertEqual(reopened_ids, ids)
            self.assertEqual([m.content for m in view], [m.content for m in expected_history])
            self.assertEqual(reopened.get_checkpoint(self.sid)["message_watermark"], ids[-1])
            self.assertFalse(compacted)
            selected = json.loads(read_tool_result.invoke({"tool_call_id": first.tool_call_id, "fields": ["/p"]}))
            self.assertIn("0.012345678901234567", selected["result_text"])
            parts, offset = [], 0
            while offset is not None:
                page = json.loads(read_tool_result.invoke({"tool_call_id": first.tool_call_id,
                                                          "start_char": offset, "max_chars": 4096}))
                self.assertEqual(page["status"], "ok")
                self.assertEqual(page["page"]["start_char"], offset)
                parts.append(page["result_text"])
                offset = page["page"]["next_start_char"]
            self.assertEqual("".join(parts), content)
            self.assertEqual(page["storage"]["full_sha256"], hashlib.sha256(content.encode()).hexdigest())
            tracing.set_user_id(8)
            self.assertEqual(json.loads(read_tool_result.invoke({"tool_call_id": first.tool_call_id}))["status"], "unavailable")
            tracing.set_user_id(7)
            tracing.set_session_id(await reopened.get_or_create_session(None, 7))
            self.assertEqual(json.loads(read_tool_result.invoke({"tool_call_id": first.tool_call_id}))["status"], "unavailable")

    async def test_exact_latest_readback_stops_before_summary_when_it_exceeds_budget(self):
        class FixtureArchive:
            provider_scope = ("http://127.0.0.1:9/v1", "fixture")

        messages = [HumanMessage(content="Read evidence"), AIMessage(content="", tool_calls=[
                    {"name": "read_tool_result", "args": {}, "id": "page-a"},
                    {"name": "read_tool_result", "args": {}, "id": "page-b"}]),
                    ToolMessage(content="a" * 2600, name="read_tool_result", tool_call_id="page-a"),
                    ToolMessage(content="b" * 2600, name="read_tool_result", tool_call_id="page-b")]
        projector = WorkingContextProjection(archive=FixtureArchive(), summary_model=None, enabled=True,
            trigger_tokens=1000, target_tokens=650, deadline=time.monotonic() + 3, readback_available=True)
        with self.assertRaisesRegex(AdmissionStopped, "context_budget_exhausted:tool_observations"):
            await projector.project(messages)
        self.assertEqual(len(messages[-1].content), 2600)
        projector.enabled = False
        self.assertEqual(await projector.project(messages), tuple(messages))

    async def test_sdk_wire_pair_preserves_private_state_and_meters_fixture_usage(self):
        self.enterContext(patch.object(jobs, "_DB_PATH", Path(self.session_store.db_path)))
        claim = jobs.claim_job(self.sid, "request", owner_user_id=7, client_request_id="request", payload={})
        self.addCleanup(tracing.set_job_id, tracing.get_job_id())
        tracing.set_job_id(claim.job_id)
        task_events.init_budget(claim.job_id, time.time() + 12, max_calls=4, max_tokens=1_000_000)
        identity = ExecutionIdentity(7, self.sid, "request", claim.job_id)
        base, model_name = "https://api.deepseek.com/v1", "deepseek-fixture"
        wire_requests = []

        def respond(request):
            wire_requests.append(json.loads(request.content))
            chunks = [
                {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "Synthetic result"}, "finish_reason": None}]},
                {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                {"choices": [], "usage": {"prompt_tokens": 109, "completion_tokens": 7, "total_tokens": 116}},
            ]
            stream = "".join("data: " + json.dumps({"id": "fixture-reply", "object": "chat.completion.chunk",
                "model": model_name, "created": 1, **chunk}) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            return httpx.Response(200, text=stream, headers={"Content-Type": "text/event-stream"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(respond), trust_env=False) as transport:
            model = _ObservedDeepSeekChatOpenAI(model=model_name, api_key="fixture", base_url=base,
                max_retries=0, max_tokens=4096, stream_usage=True, http_async_client=transport)
            driver = ModelDriver(model, FixtureCatalog(), lambda _: {"analyze"})
            call = {"name": "analyze", "args": {}, "id": "native-call"}
            content = self.delivery(call["id"], "synthetic payload " * 600)
            native = _DeepSeekMessage(content="", tool_calls=[call],
                                      provider_protocol=_ProtocolState((base, model_name), "synthetic private protocol"))
            messages = (HumanMessage(content="Read the attached data"), native, content)
            response = await driver(messages, identity)
            self.assertEqual(response.usage_metadata["total_tokens"], 116)
            self.assertEqual(len(wire_requests), 1)
            wire = wire_requests[0]
            self.assertEqual([item["role"] for item in wire["messages"]], ["user", "assistant", "tool"])
            self.assertEqual(wire["messages"][1]["reasoning_content"], "synthetic private protocol")
            self.assertEqual(wire["messages"][1]["tool_calls"][0]["id"], wire["messages"][2]["tool_call_id"])
            self.assertTrue(wire["tools"][0]["function"]["strict"])
            self.assertLessEqual(self.serialized(wire["messages"][2]["content"]), FIRST_DELIVERY_BYTES)
            oversized = messages + (HumanMessage(content="x" * 700_000),)
            with self.assertRaisesRegex(task_events.TaskConflict, "context_budget_exhausted"):
                await driver(oversized, identity)
            self.assertEqual(len(wire_requests), 1)
            budget = task_events.projection(claim.job_id)["budget"]
            self.assertEqual((budget["calls"], budget["tokens"]), (1, 116))
