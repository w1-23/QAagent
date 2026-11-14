"""Independent S2 fixtures for figure delivery; no historical prompts or outputs."""
from __future__ import annotations

import asyncio
import io

import pytest

from src.graph.export_contract import (
    artifact_matches_requirement,
    deliverable_contract,
    deliverable_match_map,
    deliverable_tool_groups,
    deliverables_satisfied,
    forced_export_plan,
)

FIGURE_PRODUCERS = {"create_research_figure", "run_research_figure_script"}


def validation(fmt, producer="create_research_figure", **overrides):
    return {"valid": True, "request_matches": True, "artifact_id": "a" * 32,
            "filename": "independent-figure." + fmt, "producer_tool": producer,
            **overrides}


@pytest.mark.parametrize("fmt", ["png", "pdf", "svg"])
@pytest.mark.parametrize("producer", sorted(FIGURE_PRODUCERS))
def test_natural_language_figure_requests_match_verified_package_exports(fmt, producer):
    query = f"请生成一张{fmt.upper()}柱状图"
    requirements = deliverable_contract(query)
    assert len(requirements) == 1
    assert requirements[0]["formats"] == [fmt]
    assert requirements[0]["kinds"] == ["figure"]
    assert deliverables_satisfied([validation(fmt, producer)], requirements)
    producers = deliverable_tool_groups(requirements)[0]
    assert FIGURE_PRODUCERS <= producers
    assert ("draw_svg" in producers) == (fmt == "svg")


@pytest.mark.parametrize("query", ["请导出PNG", "Export a PNG chart", "请输出PNG图表"])
def test_png_is_an_explicit_format_and_not_an_implicit_svg(query):
    requirements = deliverable_contract(query)
    assert len(requirements) == 1
    assert requirements[0]["formats"] == ["png"]
    assert deliverable_tool_groups(requirements) == [FIGURE_PRODUCERS]


@pytest.mark.parametrize("query", ["不要导出PNG图表", "如何导出PNG图表？", "读取附件 input.png"])
def test_png_discussion_negation_and_inputs_are_not_output_requests(query):
    assert deliverable_contract(query) == []


@pytest.mark.parametrize("fmt", ["png", "pdf", "svg"])
def test_package_acceptance_keeps_registry_request_and_format_guards(fmt):
    requirement = {"kinds": ["figure"], "formats": [fmt]}
    assert artifact_matches_requirement(validation(fmt), requirement)
    assert not artifact_matches_requirement(validation(fmt, valid=False), requirement)
    assert not artifact_matches_requirement(validation(fmt, request_matches=False), requirement)
    assert not artifact_matches_requirement(validation(fmt, producer="export_paper_analysis"), requirement)
    assert not artifact_matches_requirement(validation("json"), requirement)
    assert not artifact_matches_requirement(validation("png" if fmt != "png" else "svg"), requirement)
    assert not artifact_matches_requirement(
        validation(fmt), {"kinds": ["report_html"], "formats": [fmt]})


def test_package_kind_does_not_turn_draw_svg_into_a_package_producer():
    requirement = {"kinds": ["figure_package"], "formats": ["svg"]}
    assert deliverable_tool_groups([requirement]) == [FIGURE_PRODUCERS]
    assert not artifact_matches_requirement(validation("svg", producer="draw_svg"), requirement)
    assert artifact_matches_requirement(validation("svg"), requirement)


def test_separately_requested_formats_need_distinct_actual_exports():
    requirements = deliverable_contract("请生成PNG图表和PDF图表和SVG图表")
    assert [item["formats"] for item in requirements] == [["png"], ["pdf"], ["svg"]]
    published = [validation(fmt, artifact_id=str(index) * 32)
                 for index, fmt in enumerate(["png", "pdf", "svg"], start=1)]
    assert deliverables_satisfied(published, requirements)
    assert len(deliverable_match_map(published[:2], requirements)) == 2
    assert not deliverables_satisfied(published[:2] + [dict(published[0])], requirements)


@pytest.mark.parametrize("fmt", ["png", "pdf"])
def test_missing_raster_or_pdf_cannot_be_fulfilled_by_drawing_svg(fmt):
    requirement = deliverable_contract(f"请生成{fmt.upper()}图表")[0]
    name, _args, reason = forced_export_plan(
        requirement, "", '<svg xmlns="http://www.w3.org/2000/svg"></svg>', [], {"draw_svg": object()})
    assert name == ""
    assert reason


def test_pdf_report_remains_a_report_not_a_figure():
    requirement = deliverable_contract("请生成PDF报告")[0]
    assert requirement["kinds"] == ["report_html"]
    assert not artifact_matches_requirement(validation("pdf"), requirement)
    assert not deliverable_tool_groups([requirement])


@pytest.fixture
def publication_environment(tmp_path, monkeypatch):
    from src import auth
    from src.core import account_leases
    monkeypatch.setattr(auth, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(auth, "DB_PATH", tmp_path / "state/accounts.sqlite3")
    monkeypatch.setattr(account_leases, "_states", {})
    auth.init_store()
    with auth._connect() as conn:
        conn.execute(
            "INSERT INTO users(user_id,username,salt,password_hash,created_at) VALUES(?,?,?,?,?)",
            (101, "synthetic_delivery_owner", b"salt", b"hash", "2026-11-13"))
    from src.core import tracing
    from src.research import tools
    from src.session import files
    from src.session.manager import session_manager

    monkeypatch.setattr(session_manager, "db_path", str(tmp_path / "sessions.sqlite3"))
    session_manager._init_db_sync()
    session_manager._create_session_sync("s2-contract-session", 101)
    monkeypatch.setattr(tools, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(files, "DB_PATH", tmp_path / "state/session_files.sqlite3")
    monkeypatch.setattr(files, "FILES_ROOT", tmp_path / "state/session_files")
    previous = (tracing.get_session_id(), tracing.get_user_id(),
                tracing.get_client_request_id(), tracing.get_current_tool_name())
    tracing.set_session_id("s2-contract-session")
    tracing.set_user_id(101)
    tracing.set_client_request_id("s2-contract-request")
    files.init_store()
    yield tools, tracing
    tracing.set_session_id(previous[0])
    tracing.set_user_id(previous[1])
    tracing.set_client_request_id(previous[2])
    tracing.set_current_tool_name(previous[3])


@pytest.mark.parametrize("fmt", ["svg", "pdf", "png"])
@pytest.mark.parametrize("producer", sorted(FIGURE_PRODUCERS))
def test_actual_published_package_prevents_an_extra_draw_svg(publication_environment, monkeypatch, fmt, producer):
    from PIL import Image
    from src.graph import general_agent_graph as graph

    tools, tracing = publication_environment
    tracing.set_current_tool_name(producer)
    if fmt == "svg":
        data = b'<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2"/></svg>'
        mime = "image/svg+xml"
    else:
        stream = io.BytesIO()
        Image.new("RGB", (2, 2), "white").save(stream, format=fmt.upper())
        data = stream.getvalue()
        mime = "application/pdf" if fmt == "pdf" else "image/png"
    link = tools._save_artifact("contract." + fmt, data, mime)
    records = tools.validate_artifact_links(
        [link], session_id="s2-contract-session", owner_user_id=101,
        request_id="s2-contract-request")
    assert len(records) == 1 and records[0]["valid"], records
    assert records[0]["producer_tool"] == producer
    query = f"请生成{fmt.upper()}图表"
    requirements = deliverable_contract(query)
    assert deliverables_satisfied(records, requirements)

    def unexpected_export(*args, **kwargs):
        pytest.fail("a verified published figure must not trigger another exporter")
    monkeypatch.setattr(graph, "_forced_export_plan", unexpected_export)
    attempts = asyncio.run(graph._force_deliverable_export(
        query, "图件已发布", {"draw_svg": object()}, requirements,
        existing_validations=records, timeout=1))
    assert attempts == []
