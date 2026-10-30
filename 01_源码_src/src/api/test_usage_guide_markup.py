# -*- coding: utf-8 -*-
"""P1 六页指南（场景式重做）静态契约测试。

覆盖：六页结构、分页控件、圆点、示例只填入不发送、文案不夸大、
内联 SVG（无外链）、无障碍与响应式钩子。
"""
import io, pathlib, re, unittest

BASE = str(pathlib.Path(__file__).resolve().parents[2] / "web")


class GuidePages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = io.open(BASE + r"\index.html", encoding="utf-8").read()
        cls.js = io.open(BASE + r"\assets\app.js", encoding="utf-8").read()
        cls.css = io.open(BASE + r"\assets\app.css", encoding="utf-8").read()
        s = cls.html.index('<div class="onboarding-overlay"')
        e = cls.html.index('<div class="memory-settings-overlay"')
        cls.guide = cls.html[s:e]
        cls.pages = re.findall(r'<article class="onb-page".*?</article>', cls.guide, re.S)

    # --- 结构 ---
    def test_six_pages_present(self):
        self.assertEqual(len(self.pages), 6)

    def test_page_numbers_are_1_to_6(self):
        nums = [int(x) for x in re.findall(r'data-page="(\d+)"', self.guide)]
        self.assertEqual(nums, [1, 2, 3, 4, 5, 6])

    def test_only_first_page_visible_initially(self):
        blocks = re.findall(r'<article class="onb-page" data-page="(\d+)" aria-hidden="(true|false)"', self.guide)
        self.assertEqual(len(blocks), 6)
        self.assertEqual(blocks[0], ("1", "false"))
        for n, hidden in blocks[1:]:
            self.assertEqual(hidden, "true", "page %s should start hidden" % n)

    def test_each_page_has_heading(self):
        self.assertEqual(len(re.findall(r'<article class="onb-page"[^>]*>\s*<h2', self.guide)), 6)

    # --- 场景式内容（每页：核心价值 + 示例 + 步骤 + 可执行按钮）---
    def test_each_page_has_value_line(self):
        self.assertEqual(self.guide.count('class="onb-lead"'), 6)

    def test_each_page_has_real_example(self):
        self.assertEqual(self.guide.count('class="onb-ask-label"'), 6)
        self.assertEqual(self.guide.count('class="onb-ask-text"'), 6)
        for page in self.pages:
            self.assertRegex(page, r'<p class="onb-ask-text">“\S.{4,}”</p>')

    def test_each_page_has_three_steps(self):
        for i, page in enumerate(self.pages, 1):
            self.assertEqual(page.count('<li>'), 3, "page %d should show 3 steps" % i)

    def test_each_page_has_cta_filling_input_only(self):
        self.assertEqual(self.guide.count('class="onb-cta"'), 6)
        for page in self.pages:
            self.assertIn("onbFillExample('", page)
            self.assertNotIn("sendMsg(", page)

    def test_examples_only_fill_input(self):
        self.assertNotIn("sendMsg(", self.guide)
        self.assertIn("input.value = text", self.js)

    # --- 分页控件 ---
    def test_nav_controls_exist(self):
        for token in ["onbPrev", "onbNext", "onbIndex", "onbViewport"]:
            self.assertIn(token, self.guide)
        self.assertIn("onbStep(-1)", self.guide)
        self.assertIn("onbStep(1)", self.guide)

    def test_dots_present_and_wired(self):
        self.assertEqual(len(re.findall(r'class="onb-dot(?: active)?"', self.guide)), 6)
        self.assertEqual([int(x) for x in re.findall(r'data-dot="(\d+)"', self.guide)], [1, 2, 3, 4, 5, 6])
        self.assertIn("onbGo(", self.guide)
        self.assertIn("onbSyncChrome", self.js)
        self.assertIn("aria-current", self.js)

    def test_page_counter_and_last_page_label(self):
        self.assertIn("第 1 / 6 页", self.guide)
        self.assertIn("'开始使用'", self.js)
        self.assertIn("第 ' + onbPage + ' / ' + ONB_TOTAL + ' 页", self.js)

    def test_pager_logic_declared(self):
        for fn in ["function onbRender(", "function onbStep(", "function onbFillExample("]:
            self.assertIn(fn, self.js)
        self.assertIn("ONB_TOTAL = 6", self.js)

    # --- 已删掉的开发文档痕迹 ---
    def test_no_repeated_privacy_strip(self):
        self.assertNotIn("本指南只介绍当前已实现的能力", self.guide)
        self.assertNotIn("onboarding-privacy", self.guide)

    def test_skip_renamed_to_close(self):
        self.assertNotIn(">跳过<", self.guide)
        self.assertIn('aria-label="关闭指南"', self.guide)

    def test_no_program_status_jargon(self):
        for word in ["completed", "partial", "failed", "cancelled"]:
            self.assertNotIn(word, self.guide)

    # --- 安全与资产 ---
    def test_guide_itself_has_no_external_assets(self):
        self.assertNotIn("http://", self.guide)
        self.assertNotIn("https://", self.guide)
        self.assertNotIn("cdn.", self.guide.lower())
        self.assertNotIn("xlink:href", self.guide)

    def test_icons_are_inline_svg(self):
        self.assertEqual(self.guide.count("<svg"), self.guide.count("</svg>"))
        self.assertGreaterEqual(self.guide.count('<svg viewBox="0 0 24 24"'), 6)
        self.assertGreaterEqual(self.guide.count('<svg viewBox="0 0 16 16"'), 12)

    def test_no_overclaim_phrasing(self):
        for forbidden in ["保证准确", "自动核验全部引用", "自动确诊", "准确率100%", "百分百准确"]:
            self.assertNotIn(forbidden, self.guide)

    # --- 样式与无障碍 ---
    def test_pager_css_present(self):
        for token in [".onb-page.active", ".onb-nav", ".onb-cta", ".onb-dot.active", ".onb-hero-icon"]:
            self.assertIn(token, self.css)

    def test_reduced_motion_and_mobile(self):
        self.assertIn("prefers-reduced-motion", self.css)
        self.assertIn("@media (max-width: 640px)", self.css)

    def test_keyboard_and_focus_trap(self):
        for key in ["event.key === 'Escape'", "event.key === 'ArrowRight'", "event.key === 'ArrowLeft'", "event.key === 'Tab'"]:
            self.assertIn(key, self.js)

    def test_deep_link_supported(self):
        self.assertIn("#guide=", self.js)
        self.assertIn("history.replaceState", self.js)

    def test_pages_are_not_stubs(self):
        for i, page in enumerate(self.pages, 1):
            self.assertGreater(len(page), 700, "page %d looks empty" % i)


if __name__ == "__main__":
    unittest.main(verbosity=1)
