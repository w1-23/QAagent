"""The send-view estimate includes tool schemas and paged readback text."""
import asyncio
import time
import unittest

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from src.agent.context_projection import WorkingContextProjection, _tokens
from src.core.request_context import estimate_request


class FakeTransport:
    max_retries = 0
    max_tokens = 4096


class SchemaBudgetTests(unittest.TestCase):
    def setUp(self):
        self.schemas = [{"type": "function", "function": {
            "name": "tool_%d" % index, "description": "x" * 200,
            "parameters": {"type": "object", "properties": {}}}} for index in range(20)]
        self.messages = [HumanMessage(content="hello"), AIMessage(content="world")]

    def projector(self, schemas):
        return WorkingContextProjection(
            archive=object(), summary_model=FakeTransport(), enabled=False,
            deadline=time.monotonic() + 5, trigger_tokens=1000, target_tokens=600,
            tool_schemas=schemas)

    def test_schema_is_part_of_the_send_view_count(self):
        plain = _tokens(self.messages)
        counted = self.projector(self.schemas)._count(self.messages)
        self.assertEqual(counted, estimate_request(
            self.messages, tools=self.schemas)["estimated_input_tokens"])
        self.assertGreater(counted, plain)

    def test_without_schemas_matches_the_previous_estimate(self):
        self.assertEqual(self.projector([])._count(self.messages),
                         estimate_request(self.messages)["estimated_input_tokens"])

    def test_paged_readback_is_counted_cumulatively(self):
        readback = ToolMessage(content="R" * 6000, tool_call_id="call-1")
        before = self.projector(self.schemas)._count(self.messages)
        after = self.projector(self.schemas)._count(self.messages + [readback])
        self.assertGreater(after - before, 1000)

    def test_callable_schemas_resolve_once_per_projection(self):
        calls = []

        def schemas():
            calls.append(1)
            return self.schemas
        projector = self.projector(schemas)
        projector._count(self.messages)
        projector._count(self.messages)
        self.assertEqual(len(calls), 1)
        asyncio.run(projector.project(self.messages))
        projector._count(self.messages)
        self.assertEqual(len(calls), 2)


if __name__ == "__main__":
    unittest.main()
