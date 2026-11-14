"""The agent's figure tools: what they produce and where it lands.

A figure tool call ends with files in the account's library. That is the
contract a user relies on: the drawing they see in the chat is downloadable,
and the download is the same drawing.

These tests therefore check the *landing*, not the shape of the JSON: that
every format arrived, that the files belong to the calling account, that the
bytes on disk are the bytes that were produced, and that the recorded
project can be reopened from the workspace.

No model and no chat turn is involved. The tools are called directly inside
a tracing context, which is what the executor does for them.
"""
from __future__ import annotations

import hashlib
import json

import pytest


def _workspace(tmp_path, monkeypatch):
    """Point the service's stores at a temporary directory."""
    import src.config as config
    monkeypatch.setattr(config, "PROJECT_ROOT", tmp_path)

    from src import auth
    from src.core import account_leases
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        for owner in (101, 202):
            conn.execute(
                "INSERT OR IGNORE INTO users(user_id,username,salt,password_hash,created_at) "
                "VALUES(?,?,?,?,?)",
                (owner, f"figure_fixture_{owner}", b"salt", b"hash", "2026-01-01"))

    from src.session import files
    from src.research import tools
    from src.figures import store

    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(store, "_STATE_DIR", tmp_path / "state")
    files.init_store()

    # Publication requires a real active account/session relationship. Keep
    # that server-side store in this fixture's isolated state directory too.
    from src.session import manager
    monkeypatch.setattr(manager, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(manager, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(manager, "DB_PATH", tmp_path / "state/sessions.db")
    monkeypatch.setattr(manager.session_manager, "db_path", str(tmp_path / "state/sessions.db"))
    manager.session_manager._init_db_sync()


@pytest.fixture()
def account(tmp_path, monkeypatch):
    """A signed-in account with a valid session."""
    _workspace(tmp_path, monkeypatch)

    from src.core import tracing
    from src.session.manager import session_manager
    session_manager._create_session_sync("figure-tool-session-001", 101)
    assert session_manager.session_belongs_to("figure-tool-session-001", 101)

    tracing.set_user_id(101)
    tracing.set_session_id("figure-tool-session-001")
    tracing.set_client_request_id("req-figure-tool-001")
    yield {"user_id": 101, "session_id": "figure-tool-session-001"}
    tracing.set_user_id(None)
    tracing.set_session_id("")


def _inline_spec() -> str:
    return json.dumps({
        "chart": "bar", "title": "果实重量", "x_label": "处理", "y_label": "g",
        "x": {"kind": "inline", "column": "处理", "values": ["CK", "T1"]},
        "y": {"kind": "inline", "column": "重量", "values": [10.1, 12.2]},
        "error": "none", "legend": True}, ensure_ascii=False)


# ── The declarative path ──────────────────────────────────────────────────

def test_the_figure_lands_in_the_account_library(account):
    from src.research.figure_tools import create_research_figure
    from src.session import files as session_files

    content, result = create_research_figure.func(spec_json=_inline_spec())
    payload = json.loads(content)

    formats = {item["format"] for item in payload["exports"]}
    assert {"svg", "png", "pdf"} <= formats

    for item in payload["exports"]:
        if item["format"] not in {"svg", "png", "pdf"}:
            continue
        entry = session_files.get_account_file(item["file_id"], 101)
        assert entry is not None, "产物必须在当前账号的资料库里"
        assert entry["owner_user_id"] == 101 if "owner_user_id" in entry else True
        digest = hashlib.sha256(entry["path"].read_bytes()).hexdigest()
        assert digest == item["sha256"], "登记的摘要必须描述磁盘上的字节"


def test_the_exported_files_are_real_files(account):
    from src.research.figure_tools import create_research_figure
    from src.session import files as session_files

    _content, result = create_research_figure.func(spec_json=_inline_spec())
    exports = {item["format"]: item for item in result["exports"]}

    svg = session_files.get_account_file(exports["svg"]["file_id"], 101)
    png = session_files.get_account_file(exports["png"]["file_id"], 101)
    pdf = session_files.get_account_file(exports["pdf"]["file_id"], 101)

    assert svg["path"].read_bytes().lstrip().startswith(b"<?xml")
    assert b"<svg" in svg["path"].read_bytes()
    assert png["path"].read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert pdf["path"].read_bytes().startswith(b"%PDF-")


def test_the_project_can_be_reopened_from_the_workspace(account):
    """The chat's figure and the workspace's figure are the same project."""
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    _content, result = create_research_figure.func(spec_json=_inline_spec())
    figure_id = result["figure_id"]
    assert figure_id

    detail = projects.detail_for_owner(figure_id, 101)
    assert detail["manifest"]["objects"]
    assert detail["exports"]["svg"]["file_id"]
    assert detail["preview_url"].endswith(f"/{figure_id}/preview.svg")


def test_a_chat_figure_can_be_edited_in_the_workspace(account):
    """The stored source must be the shape the editor re-renders from.

    ``save_overrides`` renders ``source["spec"]``. Storing the bare spec let the
    project open in the workspace but rejected every edit with ``missing_spec``.
    """
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    _content, result = create_research_figure.func(spec_json=_inline_spec())
    figure_id = result["figure_id"]

    saved = projects.save_overrides(
        figure_id, 101,
        [{"gid": "axes_0.title", "prop": "fontsize", "value": 22}])
    assert [o.get("prop") for o in saved["overrides"]] == ["fontsize"]
    assert saved["render_version"] == 2


def test_a_figure_from_this_turns_upload_survives_the_turn(account):
    """An upload-sourced figure must stay editable after the request ends.

    ``attachment`` references only exist inside the turn that carried the
    file. The tool copies the bytes into the account library and stores an
    ``account_file`` reference, so the workspace can re-render it later.
    """
    from src.research.context import reset_request_tables, set_request_tables
    from src.research.figure_tools import create_research_figure
    from src.figures import projects

    csv = "处理,果实重量_g\nCK,72.4\nCK,71.9\nT1,85.1\nT1,84.6\n".encode("utf-8")
    spec = json.dumps({
        "chart": "bar", "title": "果实重量", "x_label": "处理", "y_label": "g",
        "x": {"kind": "attachment", "index": 0, "column": "处理"},
        "y": {"kind": "attachment", "index": 0, "column": "果实重量_g"},
        "error": "none", "legend": True}, ensure_ascii=False)

    token = set_request_tables([{"name": "fruit.csv", "data": csv,
                                 "type": "text/csv"}])
    try:
        _content, result = create_research_figure.func(spec_json=spec)
    finally:
        reset_request_tables(token)

    assert result["figure_id"]
    # The turn's uploads are gone now; the project still has to render.
    saved = projects.save_overrides(
        result["figure_id"], 101,
        [{"gid": "axes_0.title", "prop": "fontsize", "value": 22}])
    assert saved["render_version"] == 2


def test_the_figure_is_registered_for_the_inline_preview(account):
    """The chat bubble shows the figure without the model pasting code."""
    from src.core import figures
    from src.research.figure_tools import create_research_figure

    before = figures.count()
    _content, result = create_research_figure.func(spec_json=_inline_spec())
    assert figures.count() == before + 1
    # The registry hands back the instruction that names the placeholder,
    # so the model is told to write ``[[FIGURE:n]]`` rather than the SVG.
    assert "[[FIGURE:" in str(result["figure_placeholder"])
    assert result["_tool_outcome"]["status"] == "complete"
    assert result["_tool_outcome"]["artifacts"]


def test_the_tool_says_where_the_edits_happen(account):
    """The user is pointed at the workspace; the script is not shown."""
    from src.research.figure_tools import create_research_figure

    _content, result = create_research_figure.func(spec_json=_inline_spec())
    assert "/figures" in result["note"]
    assert "工作台" in result["note"]


# ── Refusals ──────────────────────────────────────────────────────────────

def test_an_invalid_specification_is_refused_with_the_reason(account):
    from src.research.figure_tools import create_research_figure

    content, result = create_research_figure.func(
        spec_json=json.dumps({"chart": "pie"}))
    assert content.startswith("[FIGURE_SPEC]")
    assert result["_tool_outcome"]["status"] == "failed"


def test_a_specification_that_is_not_json_is_refused(account):
    from src.research.figure_tools import create_research_figure

    content, result = create_research_figure.func(spec_json="not json at all")
    assert content.startswith("[FIGURE_ARG]")
    assert result["_tool_outcome"]["status"] == "failed"


def test_a_data_source_the_account_does_not_own_is_refused(account):
    """Naming a file id does not grant access to it.

    The account library enforces ownership, so a figure cannot be built from
    another account's table by naming its id.
    """
    from src.research.figure_tools import create_research_figure

    spec = json.dumps({
        "chart": "bar", "title": "T",
        "x": {"kind": "account_file", "file_id": "0" * 32, "column": "处理"},
        "y": {"kind": "account_file", "file_id": "0" * 32, "column": "重量"}})
    content, result = create_research_figure.func(spec_json=spec)
    assert content.startswith("[FIGURE_")
    assert result["_tool_outcome"]["status"] == "failed"


def test_the_script_path_is_closed_without_a_sandbox(account):
    """The free-script path fails closed, and says so."""
    from src.research.figure_tools import run_research_figure_script

    content, result = run_research_figure_script.func(
        script="import matplotlib\nmatplotlib.use('Agg')\n")
    assert content.startswith("[FIGURE_SANDBOX_UNAVAILABLE]")
    assert result["_tool_outcome"]["reason_code"] == "SANDBOX_UNAVAILABLE"


# ── The statistics tool ───────────────────────────────────────────────────

# The table every statistics test below reads: two treatments, three
# observations each.
TRIAL_CSV = (b"treatment,weight\n"
             b"CK,10.1\nCK,10.4\nCK,9.9\n"
             b"T1,12.2\nT1,12.6\nT1,11.8\n")

ONE_PER_GROUP_CSV = b"treatment,weight\nCK,10.1\nT1,12.2\n"


def _attach(monkeypatch, payload: bytes):
    """Make one uploaded table available to the statistics tool."""
    from src.research import tools as research_tools

    monkeypatch.setattr(research_tools, "_attachment",
                        lambda index: {"name": "trial.csv", "data": payload})


def test_the_statistics_tool_reports_real_numbers(account, monkeypatch):
    """The p-value must be computed, and the report must say how.

    The value is checked against an independent SciPy call, so a report
    that merely *looks* statistical cannot pass.
    """
    from src.research.statistics_tools import analyze_research_data

    _attach(monkeypatch, TRIAL_CSV)

    content = analyze_research_data.func(
        file_index=0, group_by_json=json.dumps(["treatment"]),
        metrics_json=json.dumps(["weight"]), test="ttest_ind")
    report = json.loads(content)

    grouped = {(row["treatment"], row["指标"]): row for row in report["grouped"]}
    ck = grouped[("CK", "weight")]
    t1 = grouped[("T1", "weight")]
    assert ck["n"] == 3
    assert ck["mean"] == pytest.approx((10.1 + 10.4 + 9.9) / 3, abs=1e-6)
    assert t1["mean"] == pytest.approx((12.2 + 12.6 + 11.8) / 3, abs=1e-6)

    from scipy import stats
    # The documented default is Welch's independent t-test; it does not select
    # Student/Welch by pretesting the observed variances.
    expected = stats.ttest_ind([10.1, 10.4, 9.9], [12.2, 12.6, 11.8],
                               equal_var=False)
    assert report["test"]["applicable"] is True
    assert report["test"]["p"] == pytest.approx(float(expected.pvalue), abs=1e-6)
    assert report["test"]["significant"] is True


def test_the_statistics_tool_reports_an_effect_size_with_the_p_value(account, monkeypatch):
    """A probability alone says how surprising; the effect size says how much."""
    from src.research.statistics_tools import analyze_research_data

    _attach(monkeypatch, TRIAL_CSV)

    content = analyze_research_data.func(
        file_index=0, group_by_json=json.dumps(["treatment"]),
        metrics_json=json.dumps(["weight"]), test="ttest_ind")
    report = json.loads(content)
    effect = report["test"]["effect_size"]
    assert effect["name"] == "hedges_g"
    assert effect["value"] is not None
    assert effect["direction"]
    interpretations = report["presentation"]["effect_interpretations"]
    assert any(item["calculation"] == 0 and item["text"] for item in interpretations)


def test_the_statistics_tool_refuses_an_unknown_test(account, monkeypatch):
    """An unknown test is refused with the vocabulary, not guessed at."""
    from src.research.statistics_tools import analyze_research_data

    _attach(monkeypatch, b"a\n1\n2\n3\n")
    content = analyze_research_data.func(file_index=0, test="wilcoxon")
    assert content.startswith("[STATS_ARG]")
    assert "wilcoxon" in content
    assert "ttest_ind" in content, "应当列出可用的检验方式"


def test_the_statistics_tool_refuses_a_missing_column(account, monkeypatch):
    from src.research.statistics_tools import analyze_research_data

    _attach(monkeypatch, b"treatment,weight\nCK,10.1\n")
    content = analyze_research_data.func(
        file_index=0, group_by_json=json.dumps(["missing_column"]))
    assert content.startswith("[STATS_ARG]")
    assert "missing_column" in content


def test_a_test_that_cannot_be_run_reports_no_p_value(account, monkeypatch):
    """A group of one has no variance, so there is no p-value to report.

    Reporting one anyway would be the worst possible failure for a
    statistical tool.
    """
    from src.research.statistics_tools import analyze_research_data

    _attach(monkeypatch, ONE_PER_GROUP_CSV)
    content = analyze_research_data.func(
        file_index=0, group_by_json=json.dumps(["treatment"]),
        metrics_json=json.dumps(["weight"]), test="ttest_ind")
    report = json.loads(content)
    assert report["test"]["applicable"] is False
    assert report["test"].get("p") is None
    assert report["test"]["reason"]


def test_the_statistics_tool_publishes_a_result_table(account, monkeypatch):
    """The numbers can be taken away as a file, not only read in the chat."""
    from src.research.statistics_tools import analyze_research_data
    from src.session import files as session_files

    _attach(monkeypatch, TRIAL_CSV)
    content = analyze_research_data.func(
        file_index=0, group_by_json=json.dumps(["treatment"]),
        metrics_json=json.dumps(["weight"]), test="ttest_ind",
        output_format="csv")
    report = json.loads(content)
    assert report.get("download_url")

    entries = session_files.list_account_files(101)
    tables = [item for item in entries if item["filename"].endswith(".csv")]
    assert tables, "结果表应当进入账号资料库"
    assert session_files.get_account_file(tables[0]["file_id"], 101) is not None
