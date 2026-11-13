# -*- coding: utf-8 -*-
"""前端脚本作用域契约测试。

回归背景：`toolActivityLabel` / `toolStatusText` 曾声明在 `sendMsg()` 内部，
`renderHistoryMessages()` 调用时抛 `ReferenceError: toolStatusText is not defined`，
导致刷新会话时只渲染第一条消息就中断（真实浏览器复现：1/35 个气泡）。
"""
import io, pathlib, re, unittest

BASE = str(pathlib.Path(__file__).resolve().parents[2] / "web")


class ScriptScope(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = io.open(BASE + r"\assets\app.js", encoding="utf-8").read()

    def test_helpers_declared_at_file_scope(self):
        for name in ("toolActivityLabel", "toolStatusText"):
            self.assertRegex(
                self.js, r"(?m)^function %s\(" % name,
                "%s must be declared at file scope" % name)

    def test_no_nested_shadow_declaration(self):
        for name in ("toolActivityLabel", "toolStatusText"):
            self.assertNotRegex(
                self.js, r"(?m)^\s+function %s\(" % name,
                "%s must not be nested inside another function" % name)

    def test_history_renderer_uses_helper(self):
        start = self.js.index("function renderHistoryMessages(")
        end = self.js.index("\nfunction ", start + 10)
        body = self.js[start:end]
        self.assertIn("toolStatusText(", body)

    def test_no_duplicate_definitions(self):
        for name in ("toolActivityLabel", "toolStatusText"):
            self.assertEqual(
                len(re.findall(r"(?m)^function %s\(" % name, self.js)), 1, name)


class TurnRail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = io.open(BASE + r"\index.html", encoding="utf-8").read()
        cls.css = io.open(BASE + r"\assets\app.css", encoding="utf-8").read()
        cls.rail = io.open(BASE + r"\assets\turn_rail.js", encoding="utf-8").read()
        cls.main = (pathlib.Path(BASE).parent / "src" / "api" / "main.py").read_text(
            encoding="utf-8")

    def test_script_loaded_after_app(self):
        self.assertIn("/assets/turn_rail.js", self.html)
        self.assertGreater(self.html.index("turn_rail.js"), self.html.index("/assets/app.js"))

    def test_ticks_come_from_server_index_not_loaded_dom(self):
        # D-A3: 全量清单来自服务端索引，未加载进 DOM 的轮次也有刻度
        self.assertIn("/turns?limit=", self.rail)
        self.assertNotIn("_historyMsgs", self.rail.split("function findInHistory")[0])
        self.assertIn("turn-tick", self.rail)

    def test_turn_index_endpoint_exists(self):
        self.assertIn('/api/v2/session/{session_id}/turns', self.main)

    def test_all_turns_listed_and_rail_scrolls(self):
        # 不再截断到最近 N 轮
        self.assertNotIn("RAIL_MAX", self.rail)
        self.assertIn("overflow-y: auto", self.css)
        self.assertIn("turn-rail-track::-webkit-scrollbar", self.css)

    def test_paging_when_index_truncated(self):
        self.assertIn("has_more", self.rail)
        self.assertIn("next_before", self.rail)
        self.assertIn("turn-rail-more", self.css)

    def test_ticks_are_keyboard_reachable(self):
        self.assertIn("document.createElement('button')", self.rail)
        self.assertIn("aria-label", self.rail)

    def test_preview_shows_question_text(self):
        self.assertIn("dataset.tip", self.rail)
        self.assertIn("turn-rail-tip", self.css)

    def test_missing_turn_is_reported_not_faked(self):
        self.assertIn("不在已加载的历史里", self.rail)

    def test_current_position_from_viewport(self):
        self.assertIn("getBoundingClientRect", self.rail)
        self.assertIn("is-active", self.rail)

    def test_bounded_history_walk(self):
        self.assertIn("WALK_LIMIT", self.rail)
        self.assertIn("loadEarlierHistory", self.rail)

    def test_mobile_and_reduced_motion(self):
        self.assertIn("@media (max-width: 900px)", self.css)
        self.assertIn("prefers-reduced-motion", self.css)


if __name__ == "__main__":
    unittest.main()
