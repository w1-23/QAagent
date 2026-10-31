# -*- coding: utf-8 -*-
"""管理员「文献候审库」页面的静态接线测试（不启动服务、不调模型）。

守住两条边界：
1. 管理端只能「看原文 / 保留 / 拒绝」，不得再出现入库、激活、索引回滚入口；
2. 归档只发生在「抓取胜出完成后」与「上传解析后」，且必须吞掉异常。
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SRC = ROOT / "src"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_admin_page_has_corpus_review_panel():
    html = _read(WEB / "admin.html")
    assert "文献候审库" in html
    for node in ('id="corpusList"', 'id="corpusRefresh"', 'id="corpusStatus"',
                 'id="corpusSummary"'):
        assert node in html, node
    # 旧入口必须消失
    for gone in ('id="corpusApply"', 'id="corpusActivate"', 'id="corpusBatches"'):
        assert gone not in html, gone


def test_admin_js_uses_read_and_decide_endpoints_only():
    js = _read(WEB / "assets" / "admin.js")
    assert "/api/v2/admin/corpus/candidates?status=" in js
    assert "/raw\"" in js and "查看原文" in js
    assert "'/' + action" in js
    for gone in ("corpus/batches", "applyCorpusBatch", "rollback", "批量入库"):
        assert gone not in js, gone
    assert "loadCorpus" in js and "handleCorpusAction" in js


def test_corpus_css_present():
    css = _read(WEB / "assets" / "admin.css")
    for rule in (".corpus-item", ".corpus-badge", ".corpus-summary",
                 ".corpus-filter", ".corpus-doi"):
        assert rule in css, rule


def _corpus_route_blocks(source: str) -> list[tuple[str, str]]:
    pattern = re.compile(r'@app\.(?:get|post|delete)\("(/api/v2/admin/corpus[^"]*)"\)\n(async def \w+\()')
    blocks = []
    for match in pattern.finditer(source):
        path, head = match.group(1), match.group(2)
        tail = source[match.end():match.end() + 700]
        blocks.append((path, head + tail))
    return blocks


def test_every_admin_corpus_route_requires_admin():
    source = _read(SRC / "api" / "main.py")
    blocks = dict(_corpus_route_blocks(source))
    assert set(blocks) == {
        "/api/v2/admin/corpus/candidates",
        "/api/v2/admin/corpus/candidates/{candidate_id}",
        "/api/v2/admin/corpus/candidates/{candidate_id}/approve",
        "/api/v2/admin/corpus/candidates/{candidate_id}/reject",
        "/api/v2/admin/corpus/candidates/{candidate_id}/reopen",
        "/api/v2/admin/corpus/candidates/{candidate_id}/raw",
    }, sorted(blocks)
    for path, body in blocks.items():
        assert "require_admin(request)" in body, path
    # 不得再有入库/批次/回滚入口
    for gone in ("admin_corpus_apply", "admin_corpus_rollback", "admin_corpus_batches",
                 "CorpusBatchRequest", "corpus/batches"):
        assert gone not in source, gone


def test_archive_hooks_sit_where_the_data_exists():
    fetch = _read(SRC / "tools" / "deepseek_search_fetch.py")
    # 必须在 winner 判定之后、组装返回文本之前归档（此时有未截断全文）
    winner_pos = fetch.index('if winner is not None:')
    archive_pos = fetch.index('_corpus_review.archive_fetch(')
    lines_pos = fetch.index('lines: list[str] = []')
    assert winner_pos < archive_pos < lines_pos
    assert 'from src.core.progress_bus import get_turn_scope' in fetch
    assert "logger.debug(" in fetch

    api = _read(SRC / "api" / "main.py")
    assert "_corpus_review.archive_upload(" in api
    body = api.split("def _capture_upload_candidate(")[1].split("\n\n\ndef ")[0]
    assert "except Exception:" in body and "logger.debug" in body


def test_upload_capture_covers_both_attachment_branches():
    main_src = _read(SRC / "api" / "main.py")
    assert main_src.count("_capture_upload_candidate(") == 3, "一个定义 + 两个调用点"
    assert "def _capture_upload_candidate(name: str, text: str, raw: bytes | None = None)" in main_src
    # 两个调用点都要把原始字节传进去
    assert main_src.count("_capture_upload_candidate(name, extracted, content)") == 2


def test_module_keeps_archiving_scope_only():
    module = _read(SRC / "core" / "corpus_review.py")
    code = module.split('"""', 2)[2]      # 去掉模块 docstring，只看真实代码
    # 不切块、不建索引、不调入库脚本
    for gone in ("ingest.py", "apply_batch", "rollback_batch", "active_index",
                 "subprocess", "chunk_size"):
        assert gone not in code, gone
    assert "archive_fetch" in module and "archive_upload" in module
    # 决策必须是条件更新（幂等）
    assert re.search(r"WHERE id=\? AND status IN \(", module)
    assert "rowcount" in module
    # 不得把全文写进 tool_results
    assert "import tool_results" not in module


def test_html_to_markdown_is_deterministic_and_lossless():
    from src.core.corpus_review import html_to_markdown
    source = ('<html><body><h2>方法</h2><p>样本 <strong>120</strong> 份，'
              '<a href="https://doi.org/10.1000/x">DOI 10.1000/x</a></p>'
              '<table><tr><th>组</th><th>n</th></tr><tr><td>A</td><td>60</td></tr></table>'
              '<script>evil()</script></body></html>')
    first = html_to_markdown(source)
    second = html_to_markdown(source)
    assert first == second                     # 确定性
    assert "## 方法" in first
    assert "**120**" in first
    assert "[DOI 10.1000/x](https://doi.org/10.1000/x)" in first
    assert "| 组 | n |" in first and "| A | 60 |" in first
    assert "evil()" not in first
    assert html_to_markdown("") == "\n"
