# -*- coding: utf-8 -*-
"""P0 上下文概览（恢复 + 按设计图重做）静态契约测试。

覆盖：DOM 骨架恢复、细分按需加载、连续同角色合并、真实用量与估算区分、
本轮产出标记、禁止伪造 token 数字、按需打开不预取。
"""
import io, pathlib, re, unittest

BASE = str(pathlib.Path(__file__).resolve().parents[2] / "web")


class ContextOverviewMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = io.open(BASE + r"\index.html", encoding="utf-8").read()
        cls.js = io.open(BASE + r"\assets\app.js", encoding="utf-8").read()
        cls.css = io.open(BASE + r"\assets\app.css", encoding="utf-8").read()
        s = cls.html.index('<div class="ctx-drawer" id="ctxDrawer">')
        e = cls.html.index('<script src="/assets/app.js', s)
        cls.drawer = cls.html[s:e]
        start = cls.js.index("function renderContextPanel(data) {")
        end = cls.js.index("function handleStatusEvent(data) {", start)
        cls.panel_js = cls.js[start:end]

    # --- DOM 骨架恢复（c1f79f7 删掉的节点必须回来）---
    def test_restored_nodes_exist(self):
        for node_id in ("ctx-empty", "ctx-body", "ctxBar", "ctxBarLabel", "ctxItems",
                        "ctxSummary", "ctxSuggestions", "ctxDetail", "ctxCompressed", "ctx-pct"):
            self.assertIn('id="%s"' % node_id, self.drawer, node_id)

    def test_usage_cards_exist(self):
        for node_id in ("ctxInTokens", "ctxInNote", "ctxOutTokens", "ctxOutNote"):
            self.assertIn('id="%s"' % node_id, self.drawer, node_id)

    def test_scope_line_is_honest(self):
        self.assertIn('id="ctxScope"', self.drawer)
        self.assertIn("最近一轮", self.drawer)

    # --- 渲染逻辑 ---
    def test_render_fills_restored_nodes(self):
        for node_id in ("ctxItems", "ctxSummary", "ctxBarLabel", "ctx-pct"):
            self.assertIn("getElementById('%s')" % node_id, self.panel_js, node_id)

    def test_no_fabricated_token_numbers(self):
        # 面板不得内置任何具体 token 数字；只能来自数据或显示占位符
        for fake in ("12,480", "1,920", "12.480"):
            self.assertNotIn(fake, self.panel_js)

    def test_estimate_vs_provider_is_labelled(self):
        self.assertIn("服务商统计", self.panel_js)
        self.assertIn("按会话文本估算", self.panel_js)

    def test_output_tokens_come_from_usage_event(self):
        self.assertIn("output_tokens", self.panel_js)
        self.assertIn("data.call.output_tokens", self.js)

    # --- 细分加载 ---
    def test_detail_loaded_on_demand_only(self):
        self.assertIn("function loadCtxDetail()", self.js)
        toggle = self.js[self.js.index("function toggleCtxDrawer() {"):]
        toggle = toggle[:toggle.index("\n}")]
        self.assertIn("if (willOpen) loadCtxDetail();", toggle)
        # 打开抽屉以外不得触发
        self.assertEqual(self.js.count("loadCtxDetail();"), 1)

    def test_segments_grouped_by_role(self):
        self.assertIn("CTX_ROLE_LABEL", self.js)
        self.assertIn("groups.push(", self.js)
        self.assertIn("本轮产出", self.js)

    def test_role_chips_are_chinese(self):
        for label in ("'系统'", "'用户'", "'助手'", "'工具'", "'记忆'"):
            self.assertIn(label, self.js, label)

    def test_detail_failure_is_reported_not_faked(self):
        self.assertIn("细分加载失败", self.js)

    # --- 样式 ---
    def test_token_card_styles(self):
        for cls in (".ctx-token-card", ".ctx-token-value", ".ctx-drawer-scope"):
            self.assertIn(cls, self.css, cls)


if __name__ == "__main__":
    unittest.main()
