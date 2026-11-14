"""Bounded E.2 detector tests. All data/images are generated engineering fixtures."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import random

import numpy as np
from PIL import Image, PngImagePlugin
import pytest

from src.research.image_integrity import ImageSource, Limits, analyze_image_reuse
from src.research.integrity import IntegrityConfig, audit_numeric_reuse, table_from_rows
from src.research.integrity_integration_test import workspace


def encoded(image, **kwargs):
    stream = io.BytesIO()
    image.save(stream, format="PNG", **kwargs)
    return stream.getvalue()


def local_report(image, *, data=None, limits=None):
    return analyze_image_reuse([ImageSource("synthetic", data or encoded(image))],
                               local_checks=True, limits=limits)


def rules(report):
    return {item["rule"]: item for item in report["local_findings"]}


def texture(seed, size=(160, 160)):
    rng = random.Random(seed)
    return Image.frombytes("RGB", size, bytes(rng.randrange(256) for _ in range(size[0]*size[1]*3)))


def duplicated_regions(*, duplicate=True, background=(110, 110, 110)):
    image = Image.new("RGB", (512, 384), background)
    image.paste(texture(17), (32, 32))
    image.paste(texture(17 if duplicate else 91), (304, 208))
    return image


def test_selfmatch_copy_move_finds_separated_regions_with_geometric_evidence():
    pytest.importorskip("cv2")
    report = local_report(duplicated_regions())
    candidate = rules(report).get("within_image_geometric_repetition")
    assert candidate, report["local_analysis_coverage"]
    evidence = candidate["evidence"]
    assert evidence["inliers"] >= 12 and evidence["correlation"] >= .85
    left, right = [loc["bbox"] for loc in candidate["locations"]]
    assert left[2] <= right[0] or right[2] <= left[0]
    assert candidate["counterexamples"] and candidate["human_review_required"]
    assert candidate["coverage"]["max_features"] == 800
    # Saved registration maps original pixel centers, not thumbnail coordinates.
    matrix = np.asarray(evidence["source_to_target_matrix"])
    mapped = matrix @ [100, 100, 1]
    assert mapped[:2] == pytest.approx([372, 276], abs=3)


def test_selfmatch_does_not_flag_independent_regions_or_constant_background():
    pytest.importorskip("cv2")
    for image in (duplicated_regions(duplicate=False), Image.new("RGB", (512, 384), "white")):
        report = local_report(image)
        assert "within_image_geometric_repetition" not in rules(report)
        assert "dynamic_range_endpoint_accumulation" not in rules(report)


def test_panel_background_step_is_located_and_normal_panels_are_counterexample():
    gray = np.full((256, 256), 60, dtype=np.uint8)
    gray[:, 128:] = 155
    report = local_report(Image.fromarray(gray))
    observed = rules(report)["panel_boundary_background_step"]
    assert observed["evidence"]["axis"] == "vertical"
    assert observed["evidence"]["original_boundary_position"] == 128
    assert observed["evidence"]["background_gray_means"] == [60., 155.]
    assert any("panel" in text.lower() for text in observed["counterexamples"])
    assert observed["coverage"]["arbitrary_seams"] == "not_assessed"


def test_smooth_gradient_and_uniform_image_do_not_create_seam_or_dynamic_range_signal():
    for array in (np.tile(np.arange(256, dtype=np.uint8), (256, 1)), np.full((256, 256), 150, dtype=np.uint8)):
        report = local_report(Image.fromarray(array))
        assert "panel_boundary_background_step" not in rules(report)
        assert "dynamic_range_endpoint_accumulation" not in rules(report)
        assert len(report["provenance_findings"]) == 1


def test_dynamic_range_endpoint_inventory_is_not_a_clipping_or_misconduct_verdict():
    ramp = np.clip(np.linspace(-100, 355, 256), 0, 255).astype(np.uint8)
    report = local_report(Image.fromarray(np.tile(ramp, (256, 1))))
    observed = rules(report)["dynamic_range_endpoint_accumulation"]
    assert observed["evidence"]["black_endpoint_fraction"] >= .2
    assert observed["evidence"]["white_endpoint_fraction"] >= .2
    assert sum(observed["evidence"]["histogram"]) <= 512 * 512
    assert observed["coverage"]["original_sensor_range"] == "unknown"
    assert any("background" in text.lower() for text in observed["counterexamples"])


def test_present_and_missing_metadata_are_non_authenticating_source_inventory():
    image = Image.new("RGB", (64, 64), "gray")
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "SYNTHETIC export-v1")
    data = encoded(image, pnginfo=info)
    report = local_report(image, data=data)
    inventory = report["provenance_findings"][0]
    assert inventory["severity"] == "information"
    assert inventory["evidence"]["declared_fields"]["image_software"] == "SYNTHETIC export-v1"
    assert inventory["locations"][0]["sha256"] == hashlib.sha256(data).hexdigest()
    assert inventory["evidence"]["metadata_authenticity"] == "not_assessed"
    blank = local_report(image)["provenance_findings"][0]
    assert blank["evidence"]["missing_metadata_is_suspicious"] is False
    assert blank["evidence"]["declared_fields"] == {}
    assert not report["local_findings"]


def test_local_candidate_budget_returns_partial_and_preserves_evidence():
    pytest.importorskip("cv2")
    report = local_report(duplicated_regions(background=(255, 255, 255)),
                          limits=Limits(max_local_findings_per_image=1))
    assert len(report["local_findings"]) == 1
    assert report["local_analysis_coverage"]["findings_omitted"] >= 1
    assert report["status"] == "partial"
    assert not report["coverage"]["complete_within_declared_search"]
    assert report["local_analysis_coverage"]["sources"][0]["limits"]["max_findings"] == 1


def test_copy_move_missing_opencv_keeps_boundary_provenance_and_discloses_coverage(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, "cv2", None)
    gray = np.full((128, 128), 50, dtype=np.uint8)
    gray[:, 64:] = 150
    report = local_report(Image.fromarray(gray))
    assert "panel_boundary_background_step" in rules(report)
    assert report["provenance_findings"]
    assert report["local_analysis_coverage"]["sources"][0]["checks"]["copy_move"] == "unavailable"
    assert report["status"] == "partial"


def numeric_table(source, values):
    return table_from_rows([[value] for value in values], ["value"], source_id=source,
                           source_name=source+".csv", source_bytes=("SYNTHETIC "+source+repr(values)).encode())


TAIL_A = ["1.123457", "2.234568", "3.345679", "5.456781", "8.567892", "13.678913"]
TAIL_B = ["19.993457", "4.884568", "33.775679", "61.666781", "10.557892", "3.448913"]


def test_shared_decimal_tail_locates_exact_suffixes_and_discloses_counterexamples():
    report = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", TAIL_B)])
    tail, = [item for item in report["findings"] if item["method"] == "shared_decimal_tail"]
    assert tail["relationship"]["tail_digits"] == 3
    assert tail["relationship"]["tail_integers"] == [457, 568, 679, 781, 892, 913]
    assert tail["left"]["cells"] == [[f"A{row}"] for row in range(2, 8)]
    common = next(item for item in report["common_findings"] if item["rule"] == "shared_decimal_tail")
    assert any("单位换算" in text for text in common["counterexamples"])
    assert tail["relationship"]["significance_claimed"] is False


@pytest.mark.parametrize("values", [
    ["19.993458", "4.884569", "33.775671", "61.666782", "10.557893", "3.448914"],
    ["1.1", "2.2", "3.3", "4.4", "5.5", "6.6"],
    [1, 2, 3, 5, 8, 13],
    ["1.111111", "2.111111", "3.111111", "5.111111", "8.111111", "13.111111"],
])
def test_nonmatching_coarse_integer_or_constant_tails_are_not_evidence(values):
    report = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", values)])
    assert not [item for item in report["findings"] if item["method"] == "shared_decimal_tail"]


def test_decimal_tail_uses_existing_column_pair_budget_and_bounded_precision():
    report = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", TAIL_B)],
                                 config=IntegrityConfig(max_column_pairs=1))
    assert report["coverage"]["column_pairs_examined"] == 1
    assert report["coverage"]["methods"]["shared_decimal_tail"]["tested"] <= 1
    high_precision = [f"{i}." + "0"*80 + str(i+1) for i in range(1, 7)]
    limited = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", high_precision)])
    assert not [item for item in limited["findings"] if item["method"] == "shared_decimal_tail"]
    skipped = limited["coverage"]["methods"]["shared_decimal_tail"]["skipped"]
    assert skipped.get("nonterminating_or_more_than_twelve_decimals", 0) >= 1


def test_single_image_tool_publishes_real_inventory_and_persistent_review(workspace):
    from src.research.image_integrity_tools import audit_research_images
    from src.research import integrity_review
    image = Image.new("RGB", (96, 96), "gray")
    workspace.upload([("SYNTHETIC_single.png", encoded(image))])
    _, payload = audit_research_images.func(file_ids=[workspace.uploads[0]["file_id"]])
    assert payload["_tool_outcome"]["status"] == "complete", payload
    assert payload["finding_count"] == 0 and payload["provenance_inventory_count"] == 1
    receipt = payload["integrity_review"]
    stored = integrity_review.get_review(101, receipt["review_id"])
    inventory, = stored["report"]["provenance_findings"]
    assert inventory["locations"][0]["file_id"] == workspace.uploads[0]["file_id"]
    assert inventory["evidence"]["missing_metadata_is_suspicious"] is False
    assert payload["report"]["sha256"] and payload["facts"]["review_url"]


def test_transparent_hidden_rgb_does_not_create_copy_or_dynamic_range_claim():
    image = duplicated_regions().convert("RGBA")
    image.putalpha(0)
    report = local_report(image)
    assert not report["local_findings"]
    assert report["provenance_findings"]


def test_boundary_location_survives_bounded_thumbnail_scaling():
    gray = np.full((256, 2048), 70, dtype=np.uint8)
    gray[:, 1024:] = 170
    observed = rules(local_report(Image.fromarray(gray)))["panel_boundary_background_step"]
    assert observed["evidence"]["original_boundary_position"] == pytest.approx(1024, abs=4)
    assert max(observed["evidence"]["analysis_shape"]) <= 512
    assert all(0 <= loc["bbox"][0] < loc["bbox"][2] <= 2048 for loc in observed["locations"])


def test_tail_missing_row_exclusions_preserve_original_coordinates():
    other = [*TAIL_B[:5], None]
    report = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", other)])
    tail, = [item for item in report["findings"] if item["method"] == "shared_decimal_tail"]
    assert tail["matched_rows"] == 5
    assert tail["left"]["rows"] == [2, 3, 4, 5, 6]
    assert tail["coverage"]["left_excluded_row_ordinals"] == [5]


def test_insufficient_suffix_matches_and_invalid_digit_budget_are_refused():
    b = TAIL_B[:3] + ["61.666782", "10.557893", "3.448914"]
    report = audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", b)])
    assert not [item for item in report["findings"] if item["method"] == "shared_decimal_tail"]
    for changes in ({"decimal_tail_digits": 1}, {"decimal_tail_digits": 100},
                    {"decimal_tail_min_places": 100}, {"decimal_tail_min_places": True}):
        with pytest.raises(ValueError):
            audit_numeric_reuse([numeric_table("a", TAIL_A), numeric_table("b", TAIL_B)], config=IntegrityConfig(**changes))


def test_metadata_inventory_bounds_declared_text_without_authenticating_it():
    image = Image.new("RGB", (64, 64), "gray")
    info = PngImagePlugin.PngInfo()
    info.add_text("Software", "SYNTHETIC" + "x"*2000)
    info.add_text("GPS", "PRIVATE omitted fixture field")
    provenance = local_report(image, data=encoded(image, pnginfo=info))["provenance_findings"][0]["evidence"]
    assert len(provenance["declared_fields"]["image_software"]) == 256
    assert "PRIVATE" not in str(provenance)
    assert provenance["private_gps_and_serial_fields"] == "not_collected"
    assert provenance["metadata_authenticity"] == "not_assessed"
