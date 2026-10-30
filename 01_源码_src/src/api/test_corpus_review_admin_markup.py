# -*- coding: utf-8 -*-
"""管理员「文献入库审核」页面的静态接线测试（不启动服务、不调模型）。"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
SRC = ROOT / "src"


def _read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def test_admin_page_has_corpus_review_panel():
    html = _read(WEB / "admin.html")
    assert "文献入库审核" in html
    for node in ('id="corpusList"', 'id="corpusBatches"', 'id="corpusApply"',
                 'id="corpusRefresh"', 'id="corpusActivate"', 'id="corpusSummary"'):
        assert node in html, node


def test_admin_js_calls_every_corpus_endpoint():
    js = _read(WEB / "assets" / "admin.js")
    for path in ("/api/v2/admin/corpus/candidates?status=pending",
                 "/api/v2/admin/corpus/candidates/' + button.dataset.id + '/' + action",
                 "/api/v2/admin/corpus/batches'",
                 "/api/v2/admin/corpus/batches?limit=10",
                 "/api/v2/admin/corpus/batches/' + button.dataset.batch + '/rollback"):
        assert path in js, path
    assert "loadCorpus" in js and "applyCorpusBatch" in js


def test_corpus_css_present():
    css = _read(WEB / "assets" / "admin.css")
    for rule in (".corpus-item", ".corpus-badge", ".corpus-summary", ".corpus-subheader" if False else ".corpus-subhead"):
        assert rule in css, rule


def test_every_admin_corpus_route_requires_admin():
    source = _read(SRC / "api" / "main.py")
    routes = [
        ('@app.get("/api/v2/admin/corpus/candidates")', 'admin_corpus_candidates'),
        ('@app.get("/api/v2/admin/corpus/candidates/{candidate_id}")', 'admin_corpus_candidate'),
        ('@app.post("/api/v2/admin/corpus/candidates/{candidate_id}/approve")', 'admin_corpus_approve'),
        ('@app.post("/api/v2/admin/corpus/candidates/{candidate_id}/reject")', 'admin_corpus_reject'),
        ('@app.post("/api/v2/admin/corpus/candidates/{candidate_id}/reopen")', 'admin_corpus_reopen'),
        ('@app.post("/api/v2/admin/corpus/batches")', 'admin_corpus_apply'),
        ('@app.get("/api/v2/admin/corpus/batches")', 'admin_corpus_batches'),
        ('@app.post("/api/v2/admin/corpus/batches/{batch_id}/rollback")', 'admin_corpus_rollback'),
    ]
    for decorator, func in routes:
        start = source.index(decorator)
        body = source[start:start + 900]
        assert f"async def {func}(" in body, func
        assert "require_admin(request)" in body, func


def test_capture_hooks_are_guarded():
    graph = _read(SRC / "graph" / "general_agent_graph.py")
    assert "capture_fetch(" in graph
    assert graph.count('if name == "deepseek_fetch_paper":') >= 1
    api = _read(SRC / "api" / "main.py")
    assert "capture_upload(" in api


def test_decide_uses_conditional_update_for_idempotency():
    module = _read(SRC / "core" / "corpus_review.py")
    assert re.search(r"WHERE id = \? AND status IN \(", module)
    assert "rowcount" in module
    assert "apply_batch" in module and "rollback_batch" in module
    # 不允许把全文写进 tool_results（不得 import 该模块）
    assert "import tool_results" not in module
    assert "from .tool_results" not in module
    assert "from src.core.tool_results" not in module
