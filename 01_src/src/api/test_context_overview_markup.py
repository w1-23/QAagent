# -*- coding: utf-8 -*-
"""Small markup contract for the context popover.

Event/identity/cache behavior is exercised by web/tests/chat-turn-ui.test.cjs.
Legacy admin inspection functions can remain, but opening the popover must not
fetch private history or turn layouts or expand the old detail panels.
"""
import pathlib
import unittest

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"


class ContextOverviewMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (WEB / "index.html").read_text(encoding="utf-8")
        cls.js = (WEB / "assets" / "app.js").read_text(encoding="utf-8")
        cls.css = (WEB / "assets" / "app.css").read_text(encoding="utf-8")
        start = cls.html.index('<div class="ctx-drawer" id="ctxDrawer">')
        end = cls.html.index('<script src="/assets/app.js', start)
        cls.drawer = cls.html[start:end]
        start = cls.js.index("function renderContextPanel(data) {")
        end = cls.js.index("/* 上下文细分", start)
        cls.panel_js = cls.js[start:end]

    def test_context_display_nodes_exist(self):
        for node_id in ("ctx-empty", "ctx-body", "ctxBar", "ctxBarLabel", "ctxSummary", "ctx-pct", "ctxScope",
                        "ctxItems", "ctxTotalKind", "ctxBudgetBlock", "ctxBudgetItems", "ctxUsageBlock"):
            self.assertIn('id="%s"' % node_id, self.drawer)

    def test_usage_cards_exist(self):
        for node_id in ("ctxInTokens", "ctxInNote", "ctxOutTokens", "ctxOutNote"):
            self.assertIn('id="%s"' % node_id, self.drawer)

    def test_renderer_uses_estimate_and_recent_provider_call_separately(self):
        self.assertIn("_ctxData.estimated_tokens", self.panel_js)
        self.assertIn("_ctxData.provider_usage", self.panel_js)
        self.assertIn("最近一次主调用", self.panel_js)
        self.assertIn("full_request_estimate", self.panel_js)
        self.assertIn("工作输入限额", self.panel_js)
        self.assertNotIn("data.total.input_tokens", self.panel_js)

    def test_no_fabricated_token_numbers(self):
        for fake in ("12,480", "1,920", "12.480"):
            self.assertNotIn(fake, self.panel_js)

    def test_opening_popover_does_not_fetch_history_or_layout(self):
        start = self.js.index("function toggleCtxDrawer() {")
        end = self.js.index("function _ctxLevelClass", start)
        toggle = self.js[start:end]
        self.assertIn("renderContextPanel(_ctxData || {})", toggle)
        for legacy_fetch in ("loadCtxDetail(", "loadSentContext(", "accountFetch("):
            self.assertNotIn(legacy_fetch, toggle)

    def test_legacy_detail_sections_stay_hidden_in_popover(self):
        self.assertIn("['ctxSentBlock', 'ctxDetailBlock']", self.panel_js)
        self.assertIn("block.hidden = true", self.panel_js)
        self.assertIn("sugEl.hidden = true", self.panel_js)

    def test_breakdown_uses_existing_request_components_and_accessible_rows(self):
        for key in ("messages", "system", "tool_arguments", "tool_schemas", "images", "protocol"):
            self.assertIn("['" + key + "',", self.js)
        self.assertIn("data.components || null", self.js)
        self.assertIn('role="table"', self.drawer)
        self.assertIn("setAttribute('role', 'row')", self.panel_js)
        self.assertIn("segment.setAttribute('aria-label', label)", self.panel_js)
        self.assertIn("itemsEl.hidden = !hasEstimate", self.panel_js)
        self.assertIn("剩余空间（未知）", self.panel_js)
        self.assertIn("工作预算与统计口径", self.drawer)
        self.assertNotIn("Compact session", self.drawer)
        self.assertIn("@media (forced-colors: active)", self.css)
        self.assertIn(".ctx-disclosure > summary:focus-visible", self.css)

    def test_admin_layout_reader_remains_available(self):
        self.assertIn("function loadSentContext()", self.js)
        self.assertIn("if (!_isAdminAccount())", self.js)
        self.assertIn("/turn-context?limit=6", self.js)

    def test_token_card_styles_exist(self):
        for selector in (".ctx-token-card", ".ctx-token-value", ".ctx-drawer-scope"):
            self.assertIn(selector, self.css)


if __name__ == "__main__":
    unittest.main()
