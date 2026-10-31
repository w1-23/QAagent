# -*- coding: utf-8 -*-
"""P0-B 回归：交付物完整性校验 / 发布幂等键 / 重试预算。

这些测试直接锁住三件事，不允许回退成"模型说生成好了就算完成"：

1. 截断产物（空文件、只剩外壳、尾部被砍、缺章节、围栏/图形未闭合）必须被拒绝；
2. 完整产物必须被接受（不能被校验误杀）；
3. 同一逻辑生成任务只允许一个正式产物（幂等键），重试最多 2 次。
"""
from __future__ import annotations

import pytest

from src.core import deliverable_check as dc

COMPLETE_SECTIONS = (
    "<h1>温州蜜柑果实品质评价对照实验方案</h1>"
    "<h2>1 目的</h2><p>比较两个砧木组合下温州蜜柑果实的可溶性固形物、"
    "可滴定酸与单果重差异。</p>"
    "<h2>2 处理</h2><p>处理组与对照组各 30 株，随机区组排列，重复 3 次，"
    "取样期一致。</p>"
    "<h2>3 观测</h2><p>每株取 10 果测定固酸比，记录糖酸比与外观等级，"
    "缺失值单独标注。</p>"
    "<h2>4 统计</h2><p>采用双因素方差分析，显著性水平 0.05，"
    "报告效应量与置信区间。</p>"
)


def _complete_html(title: str = "温州蜜柑果实品质评价对照实验方案") -> bytes:
    """一份结构完整、章节齐备的报告（`_export_markdown` 实际产物的形状）。"""
    document = (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{title}</title><style>body{{max-width:980px}}</style></head><body>"
        + COMPLETE_SECTIONS
        + "</body></html>"
    )
    return document.encode("utf-8")


# ---------------------------------------------------------------- HTML 文档


def test_complete_html_document_is_accepted():
    payload = _complete_html()
    result = dc.validate_html_document(payload, filename="方案.html")
    assert result["kind"] == "html"
    assert result["bytes"] == len(payload)
    assert result["headings"] == 5


@pytest.mark.parametrize(
    "case, content, code",
    [
        # 真实生产里出现过的那份 15 字节 "<html>ok</html>"
        ("stub", b"<html>ok</html>", "document_too_small"),
        # 整段 HTML 作为工具参数在 JSON 中途被截断：外壳没写完
        ("tail_cut", _complete_html()[: _complete_html().index(b"</body>")],
         "missing_document_end"),
        # 正文停在 <div>/<p> 中间
        ("unclosed_markup",
         ("<html><body><h1>方案</h1><div><p>" + "正文" * 200).encode("utf-8"),
         "missing_document_end"),
        # 两段拼接造成的标记破坏
        ("orphan_close",
         ("<html><body><h1>方案</h1><div>" + "正文" * 200
          + "</div></span></body></html>").encode("utf-8"),
         "unbalanced_markup"),
        # 有外壳但没有任何章节标题
        ("no_heading",
         ("<html><body><p>" + "处理组与对照组各三十株，随机区组排列。" * 10
          + "</p></body></html>").encode("utf-8"),
         "missing_sections"),
        # 外壳 + 一个标题，正文没写（"看起来完整"的空壳）
        ("shell_only",
         b"<html><body><h1>x</h1></body></html>", "document_too_small"),
    ],
)
def test_truncated_html_document_is_rejected(case, content, code):
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        dc.validate_html_document(content, filename="报告.html")
    assert excinfo.value.code == code, case
    assert str(excinfo.value).startswith("[DELIVERABLE_REJECTED:")


def test_shell_with_only_a_heading_is_rejected_as_thin():
    """外壳齐全、只有一个标题：正是"看起来完整其实没内容"的那类产物。"""
    document = (
        "<html><body><h1>温州蜜柑方案</h1><p>待补充。</p><!-- "
        + "填充" * 120 + " --></body></html>"
    ).encode("utf-8")
    assert len(document) > dc.MIN_HTML_BYTES          # 字节数过得去
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        dc.validate_html_document(document, filename="报告.html")
    assert excinfo.value.code == "content_too_thin"


def test_empty_file_is_rejected():
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        dc.validate_html_document(b"", filename="报告.html")
    assert excinfo.value.code == "empty_content"


def test_figure_only_export_stays_legal():
    """export_paper_analysis 允许只导出一张示意图，不能因为"正文少"被误杀。"""
    document = (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>技术路线图</title></head><body>"
        "<h1>温州蜜柑品质评价技术路线图</h1><h2>配图</h2>"
        '<svg xmlns="http://www.w3.org/2000/svg" width="80" height="40">'
        '<rect width="80" height="40"/></svg>'
        "<p>图 1 技术路线（采样→测定→统计）。</p>"
        "</body></html>"
    ).encode("utf-8")
    assert len(document) > dc.MIN_HTML_BYTES
    assert dc.validate_html_document(document, filename="图.html")["kind"] == "html"


# ------------------------------------------------------------ Markdown 负载
#
# `_export_markdown` 会在正文外面套一层 <html> 外壳，所以"正文被截断"在最终
# 文档层面是看不出来的：必须在套壳之前校验模型给的原始负载。

GOOD_MARKDOWN = "## 1 目的\n\n比较两组合的果实品质差异，每处理三次重复。"


def test_complete_markdown_payload_is_accepted():
    result = dc.validate_markdown_payload(GOOD_MARKDOWN, filename="报告.html")
    assert result["kind"] == "markdown"
    assert result["headings"] == 1


@pytest.mark.parametrize(
    "case, payload, code",
    [
        ("placeholder", "ok", "payload_too_short"),
        ("blank", "        \n\n  ", "empty_payload"),
        # 代码围栏数为奇数 = 正文断在代码块内部
        ("odd_fence",
         GOOD_MARKDOWN + "\n\n```svg\n<svg xmlns='http://www.w3.org/2000/svg'>",
         "unbalanced_code_fence"),
        # SVG 代码本身就是长输出，被预算截断的典型形态
        ("half_svg",
         GOOD_MARKDOWN + "\n\n<svg xmlns='http://www.w3.org/2000/svg'><rect/>",
         "unterminated_svg"),
        ("no_structure", "处理组与对照组各三十株，随机区组。" * 2, "missing_sections"),
    ],
)
def test_truncated_markdown_payload_is_rejected(case, payload, code):
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        dc.validate_markdown_payload(payload, filename="报告.html")
    assert excinfo.value.code == code, case


def test_short_but_real_research_note_is_accepted():
    """一条很短的课题记录也是合法交付物——校验只挡空壳，不挡短内容。"""
    note = "## 2026-10-31T08:00+08:00\n\n决定改用双因素方差分析。"
    assert dc.validate_markdown_payload(note, filename="笔记.html")["headings"] == 1


def test_svg_sidecar_must_be_paired():
    unpaired = b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='4' height='4'/>"
    with pytest.raises(dc.DeliverableRejected):
        dc.validate_svg_document(unpaired, filename="图.svg")
    paired = (b"<svg xmlns='http://www.w3.org/2000/svg'>"
              b"<rect width='4' height='4'/></svg>")
    assert dc.validate_svg_document(paired, filename="图.svg")["kind"] == "svg"


def test_dispatcher_routes_by_suffix_and_mime():
    assert dc.validate_deliverable(
        filename="报告.html", content=_complete_html())["kind"] == "html"
    assert dc.validate_deliverable(
        filename="数据.csv", content=b"a,b\n1,2\n", mime="text/csv")["kind"] == "text"
    assert dc.validate_deliverable(
        filename="数据.xlsx", content=b"PK\x03\x04" + b"x" * 64)["kind"] == "binary"


# ------------------------------------------------------------ 幂等键 / 预算


def test_dedupe_key_ignores_client_request_id():
    """框架层自动重试是一次新请求（crid 变了），必须仍然落到同一个产物。"""
    first = dc.dedupe_key(session_id="8ebdadfd", tool_name="audit_uploaded_data",
                          filename="数据审查_标注.xlsx")
    retry = dc.dedupe_key(session_id="8ebdadfd", tool_name="audit_uploaded_data",
                          filename="数据审查_标注.xlsx")
    assert first == retry
    # 生产库里那两对同名重复产物，差的就是 crid —— 键里不含它才治得住
    assert first == dc.dedupe_key(session_id="8ebdadfd", tool_name="audit_uploaded_data",
                                  filename="数据审查_标注.xlsx")


def test_dedupe_key_separates_tool_file_and_session():
    base = dict(session_id="sess-aaaa1111", tool_name="export_paper_analysis",
                filename="报告.html")
    key = dc.dedupe_key(**base)
    assert key != dc.dedupe_key(**{**base, "tool_name": "export_experiment_design"})
    assert key != dc.dedupe_key(**{**base, "filename": "报告2.html"})
    assert key != dc.dedupe_key(**{**base, "session_id": "sess-bbbb2222"})


def test_logical_key_separates_user_requests():
    same = dc.logical_key(session_id="s", request_id="req-1",
                          tool_name="export_paper_analysis", filename="报告.html")
    again = dc.logical_key(session_id="s", request_id="req-1",
                           tool_name="export_paper_analysis", filename="报告.html")
    other = dc.logical_key(session_id="s", request_id="req-2",
                           tool_name="export_paper_analysis", filename="报告.html")
    assert same == again
    assert same != other


def test_retry_budget_caps_at_two_and_reports_reason(tmp_path):
    ledger = dc.RetryLedger(tmp_path / "state" / "attempts.sqlite3")
    assert ledger.begin("logical-1") == 1
    assert ledger.begin("logical-1") == 2
    with pytest.raises(dc.RetryLimitExceeded) as excinfo:
        ledger.begin("logical-1")
    assert excinfo.value.code == "retry_limit"
    assert excinfo.value.attempts == 2
    assert "上限 2 次" in str(excinfo.value)
    # 超限不累加，避免"越失败越接近永久失败"
    assert ledger.attempts("logical-1") == 2


def test_retry_budget_survives_a_new_process_and_clears_on_success(tmp_path):
    db_path = tmp_path / "state" / "attempts.sqlite3"
    dc.RetryLedger(db_path).begin("logical-2")
    dc.RetryLedger(db_path).begin("logical-2")
    # 新实例（≈ 重试发生在另一次请求/另一个进程）仍看得见已用次数
    with pytest.raises(dc.RetryLimitExceeded):
        dc.RetryLedger(db_path).begin("logical-2")
    dc.RetryLedger(db_path).clear("logical-2")
    assert dc.RetryLedger(db_path).begin("logical-2") == 1


def test_retry_budget_is_per_logical_key(tmp_path):
    ledger = dc.RetryLedger(tmp_path / "attempts.sqlite3")
    ledger.begin("a")
    ledger.begin("a")
    assert ledger.begin("b") == 1  # 另一份报告不受牵连
