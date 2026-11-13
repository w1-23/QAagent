# -*- coding: utf-8 -*-
"""P0-B 回归：报告类产物的发布闸门（内容校验 / 原子发布 / 幂等 / 重试上限）。

这里跑的是真实发布链路 ``_save_artifact`` → ``src.session.files.register_file``，
只把 ``PROJECT_ROOT`` 指到 ``tmp_path``，其余逻辑不打桩。
"""
from __future__ import annotations

import hashlib
import re
import sqlite3

import pytest

from src.core import deliverable_check as dc
from src.core.tracing import (
    set_client_request_id,
    set_current_tool_name,
    set_session_id,
    set_user_id,
)
from src.research import tools as research_tools
from src.session import files as session_files

SESSION = "sess-p0b-0001"
USER = 101


def _complete_html(title: str = "温州蜜柑果实品质评价对照实验方案") -> bytes:
    sections = (
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
    document = (
        '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>{title}</title></head><body>" + sections + "</body></html>"
    )
    return document.encode("utf-8")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """把整条发布链路（artifacts sqlite + 会话文件库）都关进 tmp_path。"""
    monkeypatch.setattr(research_tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(session_files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(session_files, "DB_PATH",
                        tmp_path / "state" / "session_files.sqlite3")
    monkeypatch.setattr(session_files, "FILES_ROOT",
                        tmp_path / "state" / "session_files")
    set_session_id(SESSION)
    set_user_id(USER)
    set_client_request_id("crid-p0b-1")
    set_current_tool_name("export_paper_analysis")
    yield tmp_path
    set_session_id("")
    set_user_id(None)
    set_client_request_id("")
    set_current_tool_name("")


def _published_files(root):
    """正式产物目录里的文件（.part 不算产物）。"""
    directory = root / "state" / "artifacts" / SESSION
    if not directory.exists():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file())


def _rows(db_path, table):
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        except sqlite3.OperationalError:
            return []


def _artifact_rows(root):
    return _rows(root / "state" / "research_artifacts.sqlite3", "artifacts")


def _file_rows(root):
    return _rows(root / "state" / "session_files.sqlite3", "session_files")


# ------------------------------------------------------------------ 验收 1


def test_truncated_artifact_is_rejected_and_never_published(env):
    """15 字节的 ``<html>ok</html>``：生产库里真实出现过，必须拒绝。

    底层写入器按「结构底线」判它（缺 ``</body>``）；报告工具再按「交付契约」
    判语义完整性，见 ``test_export_tool_returns_the_real_reason_instead_of_success``。
    """
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        research_tools._save_artifact("报告.html", b"<html>ok</html>",
                                      "text/html; charset=utf-8")

    assert excinfo.value.code == "missing_document_end"
    assert _published_files(env) == []          # 没有正式产物
    assert not list(env.rglob("*.part"))        # 也没有残留临时文件
    assert _artifact_rows(env) == []            # 没有 artifacts 索引行
    assert _file_rows(env) == []                # 没有进用户资料库


def test_structural_floor_keeps_plain_stand_ins_publishable(env):
    """底线层不能误杀"用一小段 HTML 当占位符"的合法调用。

    ``src/api/test_account_profiles.py:181`` 就是拿 ``<html><body>report</body></html>``
    当占位产物验账号隔离的；语义契约（章节/长度）只属于报告工具。
    """
    url = research_tools._save_artifact("数据审查报告.html",
                                        b"<html><body>report</body></html>",
                                        "text/html")
    assert url.startswith(f"/api/v2/session/{SESSION}/artifacts/")
    assert len(_published_files(env)) == 1


def test_tail_cut_document_is_rejected_and_not_published(env):
    truncated = _complete_html()
    truncated = truncated[: truncated.index(b"</body>")]   # 尾部被砍
    with pytest.raises(dc.DeliverableRejected) as excinfo:
        research_tools._save_artifact("报告.html", truncated,
                                      "text/html; charset=utf-8")

    assert excinfo.value.code == "missing_document_end"
    assert _published_files(env) == []


def test_write_failure_leaves_no_part_file_and_no_row(env, monkeypatch):
    """写入中途失败（磁盘/IO）：正式文件与索引行都不允许出现。"""

    def boom(fd):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr(research_tools.os, "fsync", boom)
    with pytest.raises(OSError):
        research_tools._save_artifact("方案.html", _complete_html(),
                                      "text/html; charset=utf-8")

    assert _published_files(env) == []
    assert not list(env.rglob("*.part"))
    assert _artifact_rows(env) == []


# ------------------------------------------------------------------ 验收 2


def test_complete_artifact_is_published_with_identical_bytes(env):
    payload = _complete_html()
    url = research_tools._save_artifact("方案.html", payload,
                                        "text/html; charset=utf-8")

    assert re.fullmatch(rf"/api/v2/session/{SESSION}/artifacts/[a-f0-9]{{32}}", url)
    on_disk = _published_files(env)
    assert len(on_disk) == 1
    assert on_disk[0].name.endswith("_方案.html")
    assert on_disk[0].read_bytes() == payload          # 字节一致
    assert not list(env.rglob("*.part"))

    artifacts = _artifact_rows(env)
    assert len(artifacts) == 1
    files = _file_rows(env)
    assert len(files) == 1
    assert files[0]["size_bytes"] == len(payload)
    assert files[0]["sha256"] == hashlib.sha256(payload).hexdigest()
    assert files[0]["origin"] == session_files.ORIGIN_GENERATED
    assert files[0]["producer_tool"] == "export_paper_analysis"


# ------------------------------------------------------------------ 验收 3


@pytest.mark.parametrize("new_request,changed_bytes", [(False, True), (True, False), (True, True)])
def test_new_request_or_changed_bytes_publishes_an_immutable_version(
        env, new_request, changed_bytes):
    """跨轮次或内容变更发布新版本，旧下载地址继续保留原字节。"""
    first = _complete_html()
    url_first = research_tools._save_artifact("方案.html", first,
                                              "text/html; charset=utf-8")

    first_path = _published_files(env)[0]
    if new_request:
        set_client_request_id("crid-p0b-2")
    second = first.replace(b"0.05", b"0.01") if changed_bytes else first
    url_second = research_tools._save_artifact("方案.html", second,
                                               "text/html; charset=utf-8")

    assert url_second != url_first
    on_disk = _published_files(env)
    assert len(on_disk) == 2
    assert first_path.read_bytes() == first
    assert next(path for path in on_disk if path != first_path).read_bytes() == second
    artifacts = sorted(_artifact_rows(env), key=lambda row: row["version"])
    assert [row["version"] for row in artifacts] == [1, 2]
    assert artifacts[0]["artifact_id"] == url_first.rsplit("/", 1)[-1]
    assert artifacts[1]["artifact_id"] == url_second.rsplit("/", 1)[-1]
    assert artifacts[1]["logical_artifact_id"] == artifacts[0]["logical_artifact_id"]
    assert artifacts[1]["supersedes_artifact_id"] == artifacts[0]["artifact_id"]
    files = {row["artifact_id"]: row for row in _file_rows(env)}
    assert len(files) == 2
    for row, payload in zip(artifacts, (first, second)):
        registered = files[row["artifact_id"]]
        assert registered["size_bytes"] == len(payload)
        assert registered["sha256"] == hashlib.sha256(payload).hexdigest()
    assert {row["request_id"] for row in artifacts} == (
        {"crid-p0b-1", "crid-p0b-2"} if new_request else {"crid-p0b-1"})


def test_same_request_and_identical_bytes_reuses_the_published_file(env):
    payload = _complete_html()
    first_url = research_tools._save_artifact("方案.html", payload, "text/html; charset=utf-8")
    second_url = research_tools._save_artifact("方案.html", payload, "text/html; charset=utf-8")
    assert second_url == first_url
    assert len(_published_files(env)) == 1
    assert _published_files(env)[0].read_bytes() == payload
    assert len(_artifact_rows(env)) == 1
    files = _file_rows(env)
    assert len(files) == 1
    assert files[0]["client_request_id"] == "crid-p0b-1"
    assert files[0]["sha256"] == hashlib.sha256(payload).hexdigest()


def test_different_tool_or_filename_is_a_separate_product(env):
    research_tools._save_artifact("方案.html", _complete_html(),
                                  "text/html; charset=utf-8")
    set_current_tool_name("export_experiment_design")
    research_tools._save_artifact("方案.html", _complete_html(),
                                  "text/html; charset=utf-8")
    assert len(_published_files(env)) == 2


# ------------------------------------------------------------------ 验收 4


def test_retry_beyond_the_limit_returns_an_explicit_failure(env):
    bad = b"<html>ok</html>"
    for _ in range(2):                                  # 上限 2 次都给了机会
        with pytest.raises(dc.DeliverableRejected) as excinfo:
            research_tools._save_artifact("报告.html", bad,
                                          "text/html; charset=utf-8")
        assert excinfo.value.code == "missing_document_end"

    with pytest.raises(dc.RetryLimitExceeded) as excinfo:   # 第 3 次拒绝
        research_tools._save_artifact("报告.html", bad,
                                      "text/html; charset=utf-8")

    assert excinfo.value.code == "retry_limit"
    assert "上限 2 次" in str(excinfo.value)
    assert isinstance(excinfo.value, dc.DeliverableRejected)   # 调用方 except 仍能捕获
    assert _published_files(env) == []


# ------------------------------------------------- 端到端：工具返回值不能撒谎


def test_export_tool_returns_the_real_reason_instead_of_success(env):
    out = research_tools.export_paper_analysis.func(
        analysis_markdown="ok", title="报告")
    assert "[DELIVERABLE_REJECTED:payload_too_short]" in out
    assert "已生成" not in out
    assert _published_files(env) == []


def test_export_tool_publishes_one_report_and_reuses_it_on_retry(env):
    markdown = "## 1 目的\n\n" + "比较两组合的果实品质差异，每处理三次重复。" * 3
    out = research_tools.export_paper_analysis.func(
        analysis_markdown=markdown, title="温州蜜柑方案")
    assert "已生成" in out
    url = re.search(r"\((/api/v2/session/[^)]+)\)", out).group(1)

    # 同一 request、同样正文和标题的精确重试复用原产物。
    again = research_tools.export_paper_analysis.func(
        analysis_markdown=markdown, title="温州蜜柑方案")
    assert url in again
    assert len(_published_files(env)) == 1

    published = _published_files(env)[0]
    document = published.read_text(encoding="utf-8")
    assert document.rstrip().endswith("</html>")
    assert "温州蜜柑方案" in document


# ------------------------------------------- OOXML 容器：同内容不得产生重复版本


def _workbook_bytes(value: str = "合成项") -> bytes:
    """真实 openpyxl 工作簿；每次调用都会带上当前墙钟。"""
    import io

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "合成表"
    sheet.append(["样本", "值"])
    sheet.append([value, 1])
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_reexported_workbook_reuses_its_row_instead_of_a_duplicate_version(env):
    """审核包 76 个产物里 25 个是重复版本，根因是容器时间戳而非重试逻辑。"""
    import io
    import zipfile

    from openpyxl import load_workbook

    base = _workbook_bytes()

    def _restamped(seconds: int) -> bytes:
        """同样的内容，换一个容器时间戳——正是两次真实重导之间发生的事。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(base)) as source, \
                zipfile.ZipFile(buffer, "w") as target:
            for info in source.infolist():
                shifted = zipfile.ZipInfo(
                    info.filename, date_time=(2026, 11, 12, 10, 0, seconds))
                shifted.compress_type = info.compress_type
                target.writestr(shifted, source.read(info.filename))
        return buffer.getvalue()

    first_payload, second_payload = _restamped(0), _restamped(2)
    assert first_payload != second_payload, "容器时间戳必须真的不同，否则这条断言无意义"

    first_url = research_tools._save_artifact("审查.xlsx", first_payload, XLSX_MIME)
    second_url = research_tools._save_artifact("审查.xlsx", second_payload, XLSX_MIME)

    assert second_url == first_url
    assert len(_published_files(env)) == 1
    assert len(_artifact_rows(env)) == 1
    assert len(_file_rows(env)) == 1

    stored = _published_files(env)[0].read_bytes()
    book = load_workbook(io.BytesIO(stored))
    assert book.sheetnames == ["合成表"]
    assert book["合成表"]["A2"].value == "合成项"
    assert _file_rows(env)[0]["sha256"] == hashlib.sha256(stored).hexdigest()


def test_core_properties_timestamps_are_fixed_too(env):
    """core.xml 的 created/modified 也是墙钟，必须一起规范化。"""
    import io
    import zipfile

    from src.core.ooxml_determinism import FIXED_CORE_TIMESTAMP

    payload = research_tools.normalize_ooxml_bytes(_workbook_bytes())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        core = archive.read("docProps/core.xml").decode("utf-8")
        assert core.count(FIXED_CORE_TIMESTAMP) == 2
        assert all(info.date_time == (1980, 1, 1, 0, 0, 0)
                   for info in archive.infolist())


def test_changed_workbook_content_still_publishes_a_new_immutable_version(env):
    first_url = research_tools._save_artifact("审查.xlsx", _workbook_bytes("甲"), XLSX_MIME)
    first_path = _published_files(env)[0]
    first_bytes = first_path.read_bytes()
    second_url = research_tools._save_artifact("审查.xlsx", _workbook_bytes("乙"), XLSX_MIME)

    assert second_url != first_url
    assert len(_published_files(env)) == 2
    assert first_path.read_bytes() == first_bytes      # 历史链接仍返回原字节
    assert [row["version"] for row in
            sorted(_artifact_rows(env), key=lambda r: r["version"])] == [1, 2]


def test_non_ooxml_bytes_are_never_rewritten(env):
    payload = _complete_html()
    research_tools._save_artifact("方案.html", payload, "text/html; charset=utf-8")
    assert _published_files(env)[0].read_bytes() == payload


@pytest.mark.parametrize("manual", [False, True])
def test_audit_metadata_matches_actual_published_workbook(env, monkeypatch, manual):
    """Both audit paths must describe normalized published bytes, not an earlier ZIP."""
    set_current_tool_name("audit_uploaded_data")
    monkeypatch.setattr(research_tools, "get_request_tables",
                        lambda: [{"name": "合成.xlsx", "data": _workbook_bytes()}])
    annotations = [{"row": 2, "verdict": "正常", "reason": "合成复核"}] if manual else []
    _text, artifact = research_tools.audit_uploaded_data.func(annotations_json=annotations)
    stored = _published_files(env)[0].read_bytes()
    metadata = artifact["results"][0]
    assert metadata["artifact_sha256"] == hashlib.sha256(stored).hexdigest()
    assert metadata["artifact_bytes"] == len(stored)
    assert _artifact_rows(env)[0]["artifact_sha256"] == metadata["artifact_sha256"]
    assert metadata["coverage"]["input_rows"] == 1
    assert metadata["coverage"]["processed_rows"] == 1
    assert artifact["_tool_outcome"]["status"] == "complete"


def test_unreadable_container_is_published_unchanged(env):
    """规范化只做确定性，不做校验；读不懂的字节原样交给下游校验器。"""
    from src.core.ooxml_determinism import normalize_ooxml_bytes

    for raw in (b"", b"<html>x</html>", b"PK\x03\x04not-a-real-zip"):
        assert normalize_ooxml_bytes(raw) == raw
