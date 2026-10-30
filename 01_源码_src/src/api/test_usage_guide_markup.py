import io, pathlib, re, unittest
BASE = str(pathlib.Path(__file__).resolve().parents[2] / "web")

class GuidePages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html=io.open(BASE+r"\index.html",encoding="utf-8").read()
        cls.js=io.open(BASE+r"\assets\app.js",encoding="utf-8").read()
        cls.css=io.open(BASE+r"\assets\app.css",encoding="utf-8").read()
        s=cls.html.index('<div class="onboarding-overlay"')
        e=cls.html.index('<div class="memory-settings-overlay"')
        cls.guide=cls.html[s:e]

    def test_six_pages_present(self):
        self.assertEqual(len(re.findall(r'class="onb-page"', self.guide)), 6)

    def test_page_numbers_are_1_to_6(self):
        nums=[int(x) for x in re.findall(r'data-page="(\d+)"', self.guide)]
        self.assertEqual(nums, [1,2,3,4,5,6])

    def test_only_first_page_visible_initially(self):
        blocks=re.findall(r'<article class="onb-page" data-page="(\d+)" aria-hidden="(true|false)"', self.guide)
        self.assertEqual(blocks[0], ("1","false"))
        for n,hidden in blocks[1:]:
            self.assertEqual(hidden,"true", f"page {n} should start hidden")

    def test_nav_controls_exist(self):
        for token in ["onbPrev","onbNext","onbIndex","onbViewport"]:
            self.assertIn(token, self.guide)
        self.assertIn("onbStep(-1)", self.guide); self.assertIn("onbStep(1)", self.guide)

    def test_pager_logic_declared(self):
        for fn in ["function onbRender(", "function onbStep(", "function onbFillExample("]:
            self.assertIn(fn, self.js)
        self.assertIn("ONB_TOTAL = 6", self.js)

    def test_keyboard_and_focus_trap(self):
        for key in ["event.key === 'Escape'", "event.key === 'ArrowRight'", "event.key === 'ArrowLeft'", "event.key === 'Tab'"]:
            self.assertIn(key, self.js)

    def test_deep_link_supported(self):
        self.assertIn("#guide=", self.js)
        self.assertIn("history.replaceState", self.js)

    def test_examples_only_fill_input(self):
        self.assertNotIn("sendMsg(", self.guide)
        self.assertIn("input.value = text", self.js)

    def test_guide_itself_has_no_external_assets(self):
        self.assertNotIn("http://", self.guide)
        self.assertNotIn("https://", self.guide)
        self.assertNotIn("cdn.", self.guide.lower())

    def test_pager_css_present(self):
        self.assertIn(".onb-page.active", self.css)
        self.assertIn(".onb-nav", self.css)

    def test_no_overclaim_phrasing(self):
        # 只禁“正向夸大”，允许“不预测产量”这类否定表述
        for forbidden in ["保证准确", "自动核验全部引用", "自动确诊", "准确率100%", "百分百准确"]:
            self.assertNotIn(forbidden, self.guide)

    def test_each_page_has_heading(self):
        self.assertEqual(len(re.findall(r'<article class="onb-page"[^>]*>\s*<h2', self.guide)), 6)

    def test_each_page_mentions_limits_or_state(self):
        # 每页都要有可执行信息，不能是空壳
        for page in re.findall(r'<article class="onb-page".*?</article>', self.guide, re.S):
            self.assertGreater(len(page), 300)

if __name__=="__main__":
    unittest.main(verbosity=1)
