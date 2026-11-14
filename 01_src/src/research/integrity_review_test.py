"""Synthetic owner-bound review, actual tool integration and independent summaries."""
from __future__ import annotations

import io
import json
import math
import socket

_LOCAL_CONNECT = socket.socket.connect

import pytest
from PIL import Image, ImageDraw

from src.research.integrity_integration_test import workspace, _pair


def test_actual_table_tool_persists_bound_evidence_and_review_cas(workspace):
    from src.research import integrity_review as reviews
    _pair(workspace)
    text, output = workspace.tools.audit_uploaded_data.func()
    receipt = output["integrity_review"]
    assert receipt["status"] == "complete", receipt
    assert receipt["review_url"] in text
    review = reviews.get_review(101, receipt["review_id"])
    assert reviews.get_review(202, receipt["review_id"]) is None
    findings = review["report"]["common_findings"]
    assert findings and any(f["rule"] == "exact_column" for f in findings)
    selected = next(f for f in findings if f["rule"] == "exact_column")
    assert all(loc["file_id"] for loc in selected["locations"])
    assert selected["applicability"] and selected["counterexamples"] and selected["coverage"]
    assert workspace.files.is_file_referenced(workspace.uploads[0]["file_id"], 101)
    fid, rid = selected["finding_id"], receipt["review_id"]
    with pytest.raises(KeyError):
        reviews.decide(202, rid, fid, state="explained", note="foreign", expected_revision=0)
    saved = reviews.decide(101, rid, fid, state="explained", note="Declared shared synthetic control", expected_revision=0)
    assert saved["revision"] == 1
    with pytest.raises(reviews.ReviewConflict):
        reviews.decide(101, rid, fid, state="dismissed", note="stale tab", expected_revision=0)
    loaded = reviews.get_review(101, rid)
    found = next(f for f in loaded["report"]["common_findings"] if f["finding_id"] == fid)
    assert found["review"]["state"] == "explained" and len(found["review_history"]) == 1
    with pytest.raises(ValueError):
        reviews.decide(101, rid, fid, state="proven_fraud", note="invalid", expected_revision=1)
    with pytest.raises(ValueError):
        reviews.decide(101, rid, fid, state="confirmed_discrepancy", note="", expected_revision=1)
    reviews.purge_account(101)
    assert reviews.get_review(101, rid) is None
    assert not workspace.files.is_file_referenced(workspace.uploads[0]["file_id"], 101)


def test_summary_compares_actual_report_cells_and_preserves_rounding(workspace):
    # Raw mean=2.5, sample SD=sqrt(5/3), SE=sqrt(5/12).
    workspace.upload([("SYNTHETIC_raw.csv", b"value\n1\n2\n3\n4\n"),
                      ("SYNTHETIC_report.csv", b"mean,n,sd,se\n2.50,4,1.29,0.65\n")])
    binding = {"raw_file_index": 0, "raw_column": "value", "reported_file_index": 1,
               "reported_row": 2, "columns": {"mean": "mean", "n": "n", "sd": "sd", "se": "se"},
               "decimals": {"mean": 2, "sd": 2, "se": 2}, "sd_kind": "sample",
               "same_units_confirmed": True, "sample_unit_confirmed": True}
    _, output = workspace.tools.audit_uploaded_data.func(checks_json={"reported_summaries": [binding]})
    report = output["summary_consistency"]
    assert report["status"] == "complete", report
    results = report["checks"][0]["results"]
    assert all(r["status"] == "checked" and not r["discrepancy"] for r in results)
    binding["same_units_confirmed"] = False
    _, output = workspace.tools.audit_uploaded_data.func(checks_json={"reported_summaries": [binding]})
    assert output["summary_consistency"]["status"] == "partial"
    assert any(r.get("reason") for r in output["summary_consistency"]["checks"][0]["results"])


def test_summary_discrepancy_is_persisted_with_both_sources(workspace):
    from src.research import integrity_review as reviews
    workspace.upload([("SYNTHETIC_raw.csv", b"value\n1\n2\n3\n4\n"),
                      ("SYNTHETIC_report.csv", b"mean,n\n9.00,12\n")])
    _, output = workspace.tools.audit_uploaded_data.func(checks_json={"reported_summaries": [{
        "raw_file_index": 0, "raw_column": "value", "reported_file_index": 1,
        "reported_row": 2, "columns": {"mean": "mean", "n": "n"}, "decimals": {"mean": 2},
        "same_units_confirmed": True, "sample_unit_confirmed": True}]})
    discrepancies = output["summary_consistency"]["common_findings"]
    assert len(discrepancies) == 2
    stored = reviews.get_review(101, output["integrity_review"]["review_id"])
    common = stored["report"]["common_findings"]
    mismatch = next(f for f in common if f["rule"] == "reported_mean_differs_from_raw")
    assert mismatch["evidence"]["recomputed"] == "2.5"
    assert len(mismatch["locations"]) == 2
    assert mismatch["locations"][1]["cells"] == [["A2"]]


def test_api_owner_scope_preview_and_explicit_same_origin(workspace, monkeypatch):
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("external network forbidden")
        return _LOCAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient
    from src.api.integrity_router import router
    _pair(workspace)
    _, output = workspace.tools.audit_uploaded_data.func()
    rid = output["integrity_review"]["review_id"]
    app = FastAPI()
    @app.middleware("http")
    async def identity(request: Request, call_next):
        # Isolated engineering auth fixture; live cookie middleware is main's gate.
        request.state.user = {"user_id": int(request.headers.get("x-test-owner", "101"))}
        return await call_next(request)
    app.include_router(router)
    with TestClient(app) as client:
        route = f"/api/v2/integrity/reviews/{rid}"
        assert client.get(route, headers={"x-test-owner": "202"}).status_code == 404
        data = client.get(route).json()
        item = next(f for f in data["report"]["common_findings"] if f["rule"] == "exact_column")
        path = route + "/findings/" + item["finding_id"]
        assert client.get(path + "/sources/0", headers={"x-test-owner": "202"}).status_code == 404
        preview = client.get(path + "/sources/0")
        assert preview.status_code == 200, preview.text
        assert preview.json()["rows"][0]["row"] == 2
        body = {"state": "needs_context", "note": "Check experimental unit", "expected_revision": 0}
        assert client.put(path, json=body).status_code == 403
        assert client.put(path, json=body, headers={"origin": "https://foreign.invalid"}).status_code == 403
        assert client.put(path, json=body, headers={"origin": "http://testserver"}).status_code == 200
        assert client.put(path, json=body, headers={"origin": "http://testserver"}).status_code == 409
        assert client.put(path, json={**body, "owner": 202}, headers={"origin": "http://testserver"}).status_code == 422


def test_real_cookie_identity_owner_and_same_origin_enforcement(workspace, monkeypatch):
    import hashlib
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src import auth
    from src.api.integrity_router import router
    def loopback_only(sock, address):
        if not isinstance(address, tuple) or address[0] not in {"127.0.0.1", "::1", "localhost"}:
            raise AssertionError("external network forbidden")
        return _LOCAL_CONNECT(sock, address)
    monkeypatch.setattr(socket.socket, "connect", loopback_only)
    # Real password verification and real cookie-token lookup on temporary users.
    with auth._connect() as conn:
        salt = b"synthetic-login-salt"
        for owner in (101, 202):
            conn.execute("UPDATE users SET salt=?, password_hash=? WHERE user_id=?",
                         (salt, auth._password_hash("temporary-fixture-password", salt), owner))
    _, token_a = auth.login("SYNTHETIC_integrity_101", "temporary-fixture-password")
    _, token_b = auth.login("SYNTHETIC_integrity_202", "temporary-fixture-password")
    assert token_a and token_b
    _pair(workspace)
    _, output = workspace.tools.audit_uploaded_data.func()
    rid = output["integrity_review"]["review_id"]
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        route = f"/api/v2/integrity/reviews/{rid}"
        assert client.get(route).status_code == 401
        client.cookies.set("citrus_auth", token_a)
        data = client.get(route).json()
        item = data["report"]["common_findings"][0]
        path = route + "/findings/" + item["finding_id"]
        body = {"state": "needs_context", "note": "Cookie-authenticated manual review", "expected_revision": 0}
        assert client.put(path, json=body).status_code == 403
        assert client.put(path, json=body, headers={"origin": "http://testserver"}).status_code == 200
        client.cookies.set("citrus_auth", token_b)
        assert client.get(route).status_code == 404
        assert client.get(path + "/sources/0").status_code == 404
        assert client.put(path, json={**body, "expected_revision": 1}, headers={"origin": "http://testserver"}).status_code == 404
        auth.logout(token_a)
        client.cookies.set("citrus_auth", token_a)
        assert client.get(route).status_code == 401


def patterned_image(seed=17):
    import random
    rng = random.Random(seed)
    image = Image.new("RGB", (420, 360), "white")
    draw = ImageDraw.Draw(image)
    for i in range(140):
        x, y = rng.randrange(400), rng.randrange(340)
        color = tuple(rng.randrange(20, 220) for _ in range(3))
        draw.rectangle((x, y, x+rng.randrange(5, 25), y+rng.randrange(5, 20)), fill=color)
    return image


def encoded(image, fmt="PNG", **kwargs):
    stream = io.BytesIO()
    image.save(stream, format=fmt, **kwargs)
    return stream.getvalue()


@pytest.mark.parametrize("transform", ["jpeg", "scale", "rotate", "mirror", "crop"])
def test_mature_opencv_near_match_supported_transformations(transform):
    pytest.importorskip("cv2")
    from src.research.image_integrity import analyze_image_reuse, ImageSource
    original = patterned_image()
    changed = original
    if transform == "scale":
        changed = original.resize((504, 432), Image.Resampling.LANCZOS)
    if transform == "rotate":
        changed = original.rotate(17, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
    if transform == "mirror":
        changed = original.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if transform == "crop":
        changed = original.crop((60, 40, 350, 300)).resize((348, 312), Image.Resampling.LANCZOS)
    data = encoded(changed, "JPEG", quality=85)
    result = analyze_image_reuse([ImageSource("original", encoded(original)), ImageSource("changed", data)], near_duplicates=True)
    candidates = [f for f in result["findings"] if f["kind"] == "near_duplicate_geometric_candidate"]
    assert candidates, result["near_duplicate_coverage"]
    candidate = candidates[0]
    assert candidate["verification"]["inliers"] >= 12
    assert candidate["verification"]["grayscale_correlation"] >= .8
    assert result["common_findings"][0]["human_review_required"]
    assert result["unsupported_checks"]["splice"] == "not_checked"


def test_near_matching_negative_unrelated_and_constant_controls():
    pytest.importorskip("cv2")
    from src.research.image_integrity import analyze_image_reuse, ImageSource
    for other in (patterned_image(44), Image.new("RGB", (420, 360), "white")):
        report = analyze_image_reuse([ImageSource("a", encoded(patterned_image())), ImageSource("b", encoded(other))], near_duplicates=True)
        assert not report["findings"]
        assert report["near_duplicate_coverage"]["status"] == "completed_for_declared_method"


def test_missing_mature_dependency_discloses_unavailable_coverage(monkeypatch):
    import sys
    from src.research.image_integrity import analyze_image_reuse, ImageSource
    monkeypatch.setitem(sys.modules, "cv2", None)
    import random
    rng = random.Random(19)
    textured = Image.frombytes("RGB", (80, 80), bytes(rng.randrange(256) for _ in range(80 * 80 * 3)))
    data = encoded(textured)
    report = analyze_image_reuse([ImageSource("a", data), ImageSource("b", data)], near_duplicates=True)
    assert report["near_duplicate_coverage"]["status"] == "unavailable"
    assert report["status"] == "partial"
    assert report["findings"][0]["kind"] == "exact_full_image"


def test_image_tool_emits_bound_review_facts_and_real_source_crop(workspace):
    from src.research.image_integrity_tools import audit_research_images
    from src.research import integrity_review as reviews
    from src.api.integrity_router import _preview
    image = patterned_image()
    workspace.upload([("SYNTHETIC_image.png", encoded(image)),
                      ("SYNTHETIC_recompressed.jpg", encoded(image, "JPEG", quality=85))])
    text, payload = audit_research_images.func(file_ids=[u["file_id"] for u in workspace.uploads])
    assert payload["facts"]["findings_count"] >= 1
    receipt = payload["integrity_review"]
    assert receipt["status"] == "complete", receipt
    assert payload["facts"]["review_url"] == receipt["review_url"]
    assert payload["result_refs"][0]["review_id"] == receipt["review_id"]
    review = reviews.get_review(101, receipt["review_id"])
    candidate = review["report"]["common_findings"][0]
    content, mime = _preview(101, receipt["review_id"], candidate["finding_id"], 0)
    assert mime == "image/png"
    with Image.open(io.BytesIO(content)) as preview:
        assert 0 < preview.width <= 960 and 0 < preview.height <= 720
    assert reviews.source_entry(202, receipt["review_id"], candidate["finding_id"], 0) is None


def test_truncated_and_unbound_summary_checks_are_not_marked_complete():
    from src.research.integrity import table_from_rows
    from src.research.summary_consistency import compare_reported_summaries
    raw = table_from_rows([[1], [2]], ["value"], source_id="attachment:0:raw", source_name="raw.csv",
                          source_bytes=b"fixture raw", truncation_reasons=["row_limit"])
    report = table_from_rows([[2]], ["mean"], source_id="attachment:1:report", source_name="report.csv", source_bytes=b"fixture report")
    result = compare_reported_summaries([raw, report], [{"raw_file_index": 0, "raw_column": "value",
        "reported_file_index": 1, "reported_row": 2, "columns": {"mean": "mean"}}])
    assert result["coverage"]["completed_bindings"] == 0
    assert result["checks"][0]["status"] == "not_checked"
    assert not result["common_findings"]
