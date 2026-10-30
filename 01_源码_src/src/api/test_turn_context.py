# -*- coding: utf-8 -*-
"""D-A1 请求级「实际发送快照」契约测试。

覆盖：运行时记录（不是按角色重建）、只记录观测事实（不改 payload）、
敏感信息脱敏、按会话/请求检索、每会话保留上限、端点与前端接线。
"""
import io
import pathlib
import sqlite3
import tempfile
import unittest

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from src.core import turn_context
from src.core import progress_bus

BASE = pathlib.Path(__file__).resolve().parents[2]
WEB = BASE / "web"


class _TempDb(unittest.TestCase):
    """把 turn_context 的库指到临时文件，避免污染真实 sessions.db。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = turn_context._DB_PATH
        turn_context._DB_PATH = pathlib.Path(self._tmp.name) / "sessions.db"

    def tearDown(self):
        turn_context._DB_PATH = self._old
        self._tmp.cleanup()


class CaptureTests(unittest.TestCase):
    def test_capture_records_roles_chars_and_tool_names(self):
        messages = [
            SystemMessage(content="sys prompt"),
            HumanMessage(content="查一下黄龙病"),
            AIMessage(content="", tool_calls=[
                {"name": "local_search", "args": {}, "id": "c1"}]),
            ToolMessage(content="结果正文", tool_call_id="c1", name="local_search"),
        ]
        snap = turn_context.capture(messages, model="deepseek-chat")
        self.assertEqual(snap["msg_count"], 4)
        self.assertEqual(snap["tool_msgs"], 1)
        self.assertEqual(snap["model"], "deepseek-chat")
        self.assertEqual([m["kind"] for m in snap["messages"]],
                         ["system", "user", "assistant", "tool"])
        self.assertEqual(snap["messages"][2]["tool_calls"], ["local_search"])
        self.assertEqual(snap["messages"][3]["name"], "local_search")
        self.assertEqual(snap["chars"], len("sys prompt") + len("查一下黄龙病")
                         + len("结果正文"))

    def test_capture_masks_secrets_in_preview(self):
        snap = turn_context.capture([
            HumanMessage(content="key=sk-abcdef0123456789abcdef0123456789")])
        preview = snap["messages"][0]["preview"]
        self.assertNotIn("sk-abcdef0123456789abcdef0123456789", preview)

    def test_capture_truncates_preview(self):
        snap = turn_context.capture([HumanMessage(content="x" * 5000)])
        self.assertEqual(len(snap["messages"][0]["preview"]), turn_context.PREVIEW_CHARS)
        self.assertEqual(snap["messages"][0]["chars"], 5000)

    def test_capture_never_raises_on_weird_input(self):
        self.assertEqual(turn_context.capture(None)["msg_count"], 0)
        self.assertEqual(turn_context.capture([object()])["msg_count"], 1)

    def test_capture_keeps_multimodal_text_only(self):
        snap = turn_context.capture([HumanMessage(content=[
            {"type": "text", "text": "看图"}, {"type": "image_url", "image_url": {}}])])
        self.assertIn("看图", snap["messages"][0]["preview"])


class RecordReadTests(_TempDb):
    def _snap(self, n=2):
        return turn_context.capture([HumanMessage(content="q" * 10)] * n)

    def test_record_then_latest_roundtrip(self):
        row_id = turn_context.record(self._snap(), session_id="s1",
                                     request_id="r1", usage={"input_tokens": 123,
                                                             "output_tokens": 45})
        self.assertGreater(row_id, 0)
        data = turn_context.latest("s1")
        self.assertTrue(data["found"])
        self.assertEqual(data["calls"], 1)
        row = data["rows"][0]
        self.assertEqual(row["request_id"], "r1")
        self.assertEqual(row["input_tokens"], 123)
        self.assertEqual(row["output_tokens"], 45)
        self.assertEqual(len(row["payload"]["messages"]), 2)

    def test_request_filter(self):
        turn_context.record(self._snap(), session_id="s2", request_id="a")
        turn_context.record(self._snap(), session_id="s2", request_id="b")
        self.assertEqual(turn_context.latest("s2")["calls"], 2)
        only_b = turn_context.latest("s2", request_id="b")
        self.assertEqual(only_b["calls"], 1)
        self.assertEqual(only_b["rows"][0]["request_id"], "b")

    def test_session_isolation(self):
        turn_context.record(self._snap(), session_id="s3")
        self.assertFalse(turn_context.latest("other")["found"])

    def test_error_is_recorded(self):
        turn_context.record(self._snap(), session_id="s4", error="TimeoutError")
        self.assertEqual(turn_context.latest("s4")["rows"][0]["error"], "TimeoutError")

    def test_empty_session_is_unavailable_not_fake(self):
        data = turn_context.latest("")
        self.assertFalse(data["found"])
        self.assertEqual(data["scope"], "unavailable")

    def test_retention_cap_per_session(self):
        old = turn_context.KEEP_PER_SESSION
        turn_context.KEEP_PER_SESSION = 3
        try:
            for i in range(6):
                turn_context.record(self._snap(), session_id="s5", request_id=str(i))
            rows = turn_context.latest("s5", limit=200)["rows"]
            self.assertEqual(len(rows), 3)
            self.assertEqual(rows[0]["request_id"], "5")
        finally:
            turn_context.KEEP_PER_SESSION = old

    def test_record_without_session_is_noop(self):
        self.assertEqual(turn_context.record(self._snap(), session_id=""), 0)


class TurnScopeTests(unittest.TestCase):
    def test_scope_set_get_clear(self):
        progress_bus.set_turn_scope("sess-1", "req-1")
        self.assertEqual(progress_bus.get_turn_scope(), ("sess-1", "req-1"))
        progress_bus.clear_turn_scope()
        self.assertEqual(progress_bus.get_turn_scope(), ("", ""))


class WiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.main = (BASE / "src" / "api" / "main.py").read_text(encoding="utf-8")
        cls.graph = (BASE / "src" / "graph" / "general_agent_graph.py").read_text(
            encoding="utf-8")
        cls.html = io.open(WEB / "index.html", encoding="utf-8").read()
        cls.js = io.open(WEB / "assets" / "app.js", encoding="utf-8").read()
        cls.css = io.open(WEB / "assets" / "app.css", encoding="utf-8").read()

    def test_endpoint_registered(self):
        self.assertIn('/api/v2/session/{session_id}/turn-context', self.main)

    def test_scope_bound_per_request(self):
        self.assertIn("set_turn_scope(sid", self.main)
        self.assertIn("clear_turn_scope()", self.main)

    def test_call_funnel_records_without_mutating_payload(self):
        start = self.graph.index("async def _call_llm(")
        end = self.graph.index("def _provider_failure_notice", start)
        body = self.graph[start:end]
        self.assertIn("turn_context.capture(messages)", body)
        self.assertIn("turn_context.record", body)
        # 观测点必须把原 messages 原样交给 stream_llm_response
        self.assertIn("stream_llm_response(llm, messages, on_text=on_text)", body)
        # 记录失败不得中断本轮
        self.assertIn("except Exception", body)

    def test_dom_and_js_wired(self):
        self.assertIn('id="ctxSent"', self.html)
        self.assertIn("function loadSentContext()", self.js)
        self.assertIn("function renderSentContext(", self.js)
        self.assertIn("loadSentContext();", self.js)

    def test_empty_state_does_not_invent_numbers(self):
        start = self.js.index("function renderSentContext(")
        end = self.js.index("function toggleSentCall(", start)
        body = self.js[start:end]
        self.assertIn("本轮没有记录到请求快照", body)
        self.assertNotIn("0 tokens", body)

    def test_css_present(self):
        for sel in (".ctx-sent-head", ".ctx-sent-body", ".ctx-sent-empty"):
            self.assertIn(sel, self.css)


if __name__ == "__main__":
    unittest.main()
