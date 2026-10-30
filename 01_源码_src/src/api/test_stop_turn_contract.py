# -*- coding: utf-8 -*-
"""停止任务后的收尾契约测试（真实浏览器发现的两个缺陷）。

1) 停止后按钮状态必须回到可发送（曾因回调内抛 ReferenceError 卡在停止态）。
2) 停止后气泡不得是空框：服务端已持久化收尾文本，前端必须回填。
"""
import io, pathlib, re, unittest

BASE = str(pathlib.Path(__file__).resolve().parents[2] / "web")


class StopTurnContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = io.open(BASE + r"\assets\app.js", encoding="utf-8").read()
        start = cls.js.index("function stopCurrentTurn(reason) {")
        end = cls.js.index("\n  }\n", start)
        cls.body = cls.js[start:end]

    def test_send_state_restored(self):
        self.assertIn("setSendDisabled(false)", self.body)

    def test_backfill_uses_file_scope_helper(self):
        self.assertIn("fetchHistoryPage(4, 0)", self.body)
        self.assertNotIn("apiBase", self.body)

    def test_backfill_cannot_block_ui_reset(self):
        self.assertLess(self.body.index("setSendDisabled(false)"), self.body.index("fetchHistoryPage(4, 0)"))
        self.assertIn("} catch (e) { /* ignore */ }", self.body)

    def test_backfill_only_when_bubble_empty(self):
        self.assertIn("if (target.textContent && target.textContent.trim()) return;", self.body)

    def test_trace_kept_on_stop(self):
        self.assertIn("appendTerminal(", self.body)


if __name__ == "__main__":
    unittest.main()
