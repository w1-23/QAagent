import hashlib
import json
import unittest
from unittest.mock import patch

from langchain_core.messages import ToolMessage, HumanMessage
from src.core.tool_result_view import first_delivery


class FirstDeliveryTests(unittest.TestCase):
    def message(self, name="audit_uploaded_data"):
        text = json.dumps({"rows": ["value 1.234567890123456789"] * 1000})
        ref = {"tool_call_id": "c1", "original_chars": len(text),
               "full_sha256": hashlib.sha256(text.encode()).hexdigest(),
               "storage": {"complete": True}}
        return ToolMessage(content=text, name=name, tool_call_id="c1",
                           additional_kwargs={"result_reference": ref},
                           artifact={"op_receipt": {"status": "complete"}})

    def test_first_view_has_exact_previews_and_immutable_original(self):
        original = self.message()
        view = first_delivery(original, call={}, reader_available=True)
        body = json.loads(view.content)
        self.assertNotEqual(view.content, original.content)
        for preview in body["previews"]:
            self.assertEqual(preview["text"], original.content[preview["start_char"]:preview["end_char"]])
        self.assertEqual(body["field_index"], [{"pointer": "/rows", "type": "array"}])
        self.assertEqual(view.artifact, original.artifact)
        self.assertIs(first_delivery(view, call={}, reader_available=True), view)

    def test_failed_storage_or_missing_reader_keeps_entire_result(self):
        for mutate in (lambda m: m.additional_kwargs.clear(),
                       lambda m: m.additional_kwargs["result_reference"].update(full_sha256="wrong"),
                       lambda m: m.additional_kwargs["result_reference"]["storage"].update(complete=False)):
            original = self.message()
            mutate(original)
            self.assertIs(first_delivery(original, call={}, reader_available=True), original)
        original = self.message()
        self.assertIs(first_delivery(original, call={}, reader_available=False), original)

    def test_reader_and_skill_pages_never_fold_again(self):
        for name, args in (("read_tool_result", {}), ("read_candidate", {}),
                           ("research_workspace", {"operation": "read_skill_resource"})):
            original = self.message(name)
            self.assertIs(first_delivery(original, call={"args": args}, reader_available=True), original)

    def test_active_limit_independent_of_history_threshold_and_window_bounded(self):
        from src.config import settings
        from src.graph.request_plan import bounded_tool_context
        messages = [HumanMessage(content="full input")]
        with patch.object(settings, "CONTEXT_ACTIVE_MAX_INPUT_TOKENS", 128000), \
             patch.object(settings, "CONTEXT_WORKING_MAX_INPUT_TOKENS", 64000), \
             patch("src.core.request_context.estimate_request", return_value={"estimated_input_tokens": 67000}):
            view, status = bounded_tool_context(messages, max_tokens=1000000, output_tokens=4096)
            self.assertTrue(status["fits"])
            self.assertIs(view[0], messages[0])
            _, status = bounded_tool_context(messages, max_tokens=64000, output_tokens=4096)
            self.assertFalse(status["fits"])


if __name__ == "__main__":
    unittest.main()
