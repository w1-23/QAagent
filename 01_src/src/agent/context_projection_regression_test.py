"""P0-A: local model fixtures through deployed middleware and durable accounting."""
import asyncio
import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.agent.context_projection import EncryptedContextArchive, WorkingContextProjection, _complete_tool_groups
from src.agent.context_tools import read_context_evidence
from src.agent.identity import ExecutionIdentity
from src.agent.runtime import AdmissionStopped
from src.core import jobs, task_events, tracing
from src.core.provider_protocol import _DeepSeekMessage, _ProtocolState
from src.core.request_context import model_request
from src.core.tool_result_view import INLINE_CHARS, first_delivery


class FakeTransport:
    max_retries = 0
    max_tokens = 4096

    def __init__(self, calls=None, replies=None):
        self.calls = [] if calls is None else calls
        self.replies = list(replies) if replies is not None else []

    def model_copy(self, *, update):
        copied = FakeTransport(self.calls)
        copied.replies = self.replies
        copied.max_tokens = update["max_tokens"]
        return copied

    async def ainvoke(self, messages):
        self.calls.append((self.max_tokens, messages))
        if self.replies:
            item = self.replies.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        return AIMessage(content="Historical context", response_metadata={"finish_reason": "stop"},
                         usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12})


def tool_group(body):
    return [AIMessage(content="", id="call", tool_calls=[{"id": "tool-1", "name": "read_data", "args": {}}]),
            ToolMessage(content=body, id="result", tool_call_id="tool-1", name="read_data")]


class ProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(jobs, "_DB_PATH", self.root / "jobs.sqlite"))
        self.enterContext(patch("src.config.PROJECT_ROOT", self.root))
        self.enterContext(patch("src.config.get_deepseek_model", return_value="fixture"))
        from src.config import settings
        claim = jobs.claim_job("fixture-session", "fixture-turn", owner_user_id=11,
                               client_request_id="test-req", payload={})
        self.identity = ExecutionIdentity(11, "fixture-session", "fixture-turn", claim.job_id)
        tracing.set_user_id(11)
        tracing.set_session_id(self.identity.session_id)
        tracing.set_job_id(self.identity.run_id)
        self.events = []
        self.model = FakeTransport()
        self.archive = EncryptedContextArchive(self.root / "state", self.identity,
                                               (settings.RESOLVED_MAIN_BASE_URL, "fixture"))
        self.projector = WorkingContextProjection(archive=self.archive, summary_model=self.model,
            enabled=True, deadline=time.monotonic() + 35, trigger_tokens=128, target_tokens=100,
            readback_available=True, on_projection=self.observe)

    async def observe(self, event):
        self.events.append(event)

    def budget(self, tokens=1_000_000, calls=10):
        task_events.init_budget(self.identity.run_id, time.time() + 35, max_calls=calls, max_tokens=tokens)

    def metered_calls(self):
        return task_events.projection(self.identity.run_id)["budget"]["calls"]

    def raw(self, groups):
        return [SystemMessage(content="control", id="sys"), *groups,
                HumanMessage(content="latest user request", id="tail")]

    async def test_giant_tool_group_archive_only_and_paginated_hash(self):
        self.budget()
        raw = self.raw(tool_group('"\t\\字' * 160_000))
        _, estimate = model_request(self.model, raw, output_tokens=4096)
        self.assertGreater(estimate["estimated_input_tokens"], 167109)
        view = await self.projector.project(raw)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.metered_calls(), 0)
        _complete_tool_groups(view)
        self.assertEqual([m.id for m in view if not isinstance(m, SystemMessage)], ["tail"])
        self.assertEqual(self.events[0]["event"], "working_context_archived")
        receipt = json.loads(view[1].content.split("Raw evidence readback: ", 1)[1])
        self.assertEqual(receipt["archive_only_indices"], [1, 2])
        with self.assertRaises(PermissionError):
            tracing.set_user_id(12)
            try:
                await read_context_evidence.coroutine(receipt["checkpoint_id"], receipt["message_count"], 2)
            finally:
                tracing.set_user_id(11)
        start, pages = 0, []
        while start is not None:
            text, _ = await read_context_evidence.coroutine(receipt["checkpoint_id"],
                receipt["message_count"], 2, start_char=start, max_chars=20000)
            self.assertLessEqual(len(text.encode()), INLINE_CHARS)
            tool = ToolMessage(content=text, tool_call_id="page", name="read_context_evidence")
            self.assertIs(first_delivery(tool, call={}, reader_available=True), tool)
            page = json.loads(text)
            self.assertEqual(page["start_char"], start)
            pages.append(page["text"])
            start = page["next_start_char"]
        joined = "".join(pages)
        self.assertEqual(hashlib.sha256(joined.encode()).hexdigest(), page["public_message_sha256"])
        self.assertEqual(json.loads(joined)["data"]["content"], raw[2].content)

    async def test_mixed_source_uses_ledger_and_never_sends_giant_group(self):
        self.budget()
        raw = self.raw([*tool_group("x" * 680_000), AIMessage(id="later", content="small observation " * 60)])
        view = await self.projector.project(raw)
        self.assertEqual(len(self.model.calls), 1)
        allowance, payload = self.model.calls[0]
        _, estimate = model_request(self.model, payload, output_tokens=allowance)
        self.assertLessEqual(estimate["estimated_input_tokens"], task_events.model_input_limit(allowance))
        self.assertNotIn("x" * 100, payload[-1].content)
        self.assertIn("small observation", payload[-1].content)
        self.assertEqual(self.metered_calls(), 1)
        self.assertEqual(task_events.projection(self.identity.run_id)["budget"]["tokens"], 12)
        self.assertEqual(await self.projector.project(raw), view)
        self.assertEqual(self.metered_calls(), 1)

    async def test_ledger_rejection_remains_specific_and_does_not_dispatch(self):
        self.budget(tokens=100)
        raw = self.raw([AIMessage(id="past", content="prior evidence " * 100)])
        with self.assertRaisesRegex(AdmissionStopped, "model_budget_exhausted:tokens"):
            await self.projector.project(raw)
        self.assertEqual(self.metered_calls(), 0)
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.events, [])

    async def test_protected_tail_unchanged_and_hard_gate_rejects(self):
        self.budget()
        raw = self.raw([])
        raw[-1] = raw[-1].model_copy(update={"content": "y" * 680_000})
        view = await self.projector.project(raw)
        self.assertIs(view[-1], raw[-1])
        with self.assertRaisesRegex(task_events.TaskConflict, "context_budget_exhausted"):
            await task_events.run_model_attempt(self.model, view, lambda: self.model.ainvoke(view),
                                                purpose="main_loop")
        self.assertEqual(self.model.calls, [])
        self.assertEqual(self.metered_calls(), 0)

    async def test_length_retry_is_metered_and_increases_output_allowance(self):
        self.budget()
        self.model.replies.append(AIMessage(content="truncated", response_metadata={"finish_reason": "length"}))
        await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual([out for out, _ in self.model.calls], [4096, 8192])
        self.assertEqual(self.metered_calls(), 2)
        self.assertGreater(task_events.projection(self.identity.run_id)["budget"]["tokens"], 4096)

    async def test_cancel_is_not_reported_as_model_failure(self):
        self.budget()
        events = self.capture_diag()
        self.model.replies.append(asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(len(self.model.calls), 1)
        self.assertEqual(self.events, [])
        self.assertEqual(events, [])

    def capture_diag(self):
        events = []

        def record(event, **fields):
            events.append((event, fields))

        self.enterContext(patch("src.core.diag.diag", side_effect=record))
        return events

    async def test_program_error_diagnostic_is_bounded_and_sanitized(self):
        self.budget()
        sentinel = "SYNTHETIC-PRIVATE-SENTINEL user prose"
        events = self.capture_diag()

        async def fail(_messages):
            raise RuntimeError(sentinel)

        with patch.object(self.archive, "archive", fail):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_failed"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(len(events), 1)
        event, fields = events[0]
        self.assertEqual(event, "context_projection_failed")
        self.assertEqual(fields["stage"], "archive")
        self.assertEqual(fields["error_type"], "RuntimeError")
        self.assertEqual(fields["message_count"], 3)
        self.assertEqual(fields["protected_count"], 2)
        self.assertEqual(fields["eligible_units"], 1)
        self.assertIsInstance(fields["estimated_input_tokens"], int)
        self.assertFalse(fields["archived"])
        self.assertFalse(fields["summarized"])
        self.assertGreaterEqual(fields["remaining_s"], 0.0)
        rendered = repr(fields)
        for private in (sentinel, "history", "latest user request", "fixture-turn"):
            self.assertNotIn(private, rendered)

    async def test_middleware_failure_reports_middleware_stage(self):
        self.budget()
        sentinel = "MIDDLEWARE-PRIVATE-SENTINEL"
        events = self.capture_diag()

        class FailingMiddleware:
            def __init__(self, **_kwargs):
                pass

            async def abefore_model(self, _state, _runtime):
                raise RuntimeError(sentinel)

        with patch("src.agent.context_projection.SummarizationMiddleware", FailingMiddleware):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_failed"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(len(events), 1)
        _, fields = events[0]
        self.assertEqual(fields["stage"], "middleware")
        self.assertTrue(fields["archived"])
        self.assertFalse(fields["summarized"])
        self.assertNotIn(sentinel, repr(fields))

    async def test_summary_failure_reports_summary_stage(self):
        self.budget()
        sentinel = "SUMMARY-PRIVATE-SENTINEL"
        events = self.capture_diag()

        def fail(*_args, **_kwargs):
            raise RuntimeError(sentinel)

        with patch("src.agent.context_projection._summary_source", fail):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_failed"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(len(events), 1)
        _, fields = events[0]
        self.assertEqual(fields["stage"], "summary")
        self.assertTrue(fields["archived"])
        self.assertFalse(fields["summarized"])
        self.assertNotIn(sentinel, repr(fields))

    async def test_classified_budget_and_protocol_failures_are_not_program_errors(self):
        events = self.capture_diag()
        with self.assertRaisesRegex(AdmissionStopped, "context_projection_budget_required"):
            await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))

        archive = EncryptedContextArchive(self.root / "state-deepseek", self.identity,
                                          ("https://api.deepseek.com/v1", "deepseek-chat"))
        projector = WorkingContextProjection(archive=archive, summary_model=self.model,
            enabled=True, deadline=time.monotonic() + 35, trigger_tokens=128, target_tokens=100,
            readback_available=True)
        raw = [SystemMessage(content="control", id="sys"),
               _DeepSeekMessage(content="thought", id="deep",
                   provider_protocol=_ProtocolState(("https://other.example/v1", "deepseek-chat"), "r")),
               HumanMessage(content="latest", id="tail")]
        with self.assertRaisesRegex(AdmissionStopped, "context_projection_private_state_missing_or_mismatched"):
            await projector.project(raw)
        self.assertEqual(events, [])

    async def test_timeout_is_deadline_not_program_error(self):
        self.budget()
        events = self.capture_diag()

        async def timeout(_messages):
            raise TimeoutError

        with patch.object(self.archive, "archive", timeout):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_deadline"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(events, [])

    async def test_middleware_contract_change_is_not_program_error(self):
        self.budget()
        events = self.capture_diag()

        class NoUpdateMiddleware:
            def __init__(self, **_kwargs):
                pass

            async def abefore_model(self, _state, _runtime):
                return None

        with patch("src.agent.context_projection.SummarizationMiddleware", NoUpdateMiddleware):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_middleware_contract_changed"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
        self.assertEqual(events, [])

    async def test_diagnostic_failure_does_not_change_admission(self):
        self.budget()
        self.enterContext(patch("src.core.diag.diag", side_effect=RuntimeError("sink failure")))

        async def fail(_messages):
            raise RuntimeError("archive failure")

        with patch.object(self.archive, "archive", fail):
            with self.assertRaisesRegex(AdmissionStopped, "context_projection_failed"):
                await self.projector.project(self.raw([AIMessage(id="past", content="history " * 100)]))
