# -*- coding: utf-8 -*-
"""S4 补：PDF 预览的失败状态必须是"诚实且可行动"的。

用户反馈过"PDF 预览空白"。S4 修的是真因（blob 前校验魔数 + 403/404/超大/断网分流），
这里把前端分支钉死，避免以后被顺手删掉又退回"一片空白"。
"""
import io
import re
import unittest
from pathlib import Path

JS = Path(__file__).resolve().parents[2] / "web" / "assets" / "session_files.js"


def _js() -> str:
    return io.open(JS, encoding="utf-8").read()


class PdfPreviewStates(unittest.TestCase):
    def setUp(self):
        self.js = _js()

    def test_preview_has_size_cap_before_reading_body(self):
        self.assertIn("MAX_INLINE_PDF", self.js)
        self.assertLess(self.js.index("MAX_INLINE_PDF"), self.js.index("return r.arrayBuffer();"))

    def test_each_failure_reason_has_its_own_message(self):
        for reason in ("'not-pdf'", "'empty'", "'forbidden'", "'not-found'",
                       "'too-large:'", "indexOf('HTTP ')", "Failed to fetch"):
            self.assertIn(reason, self.js, reason)

    def test_failure_text_tells_user_what_to_do(self):
        block = self.js[self.js.index("var reason = String(err"):]
        block = block[: block.index("\n  }\n") if "\n  }\n" in block else len(block)]
        self.assertIn("\u4e0b\u8f7d", block)        # 下载
        self.assertIn("\u91cd\u8bd5", block)        # 重试
        self.assertNotIn("\u672a\u77e5\u9519\u8bef", block)

    def test_pdf_branch_declares_sandbox_free_iframe_only_for_html(self):
        self.assertIn('sandbox=""', self.js)

    def test_no_silent_blank_path_left(self):
        # 每个 fail() 调用都必须带一句话，不允许 fail() / fail('') 这种"空白"
        self.assertEqual([m for m in re.findall(r"fail\('\'\)", self.js)], [])
        self.assertGreaterEqual(self.js.count("fail('"), 7, "失败状态分支少于 7 个")


class PreviewDoctrine(unittest.TestCase):
    """支持格式与不支持格式都要说清楚，不支持时必须给下载入口。"""

    def test_unsupported_format_offers_download(self):
        js = _js()
        idx = js.find("sf-preview-fallback")
        self.assertGreater(idx, 0)
        self.assertIn("download", js[idx: idx + 400])


if __name__ == "__main__":
    unittest.main(verbosity=1)
