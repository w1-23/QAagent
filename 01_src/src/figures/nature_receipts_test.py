"""Host acceptance only: does not import or execute any upstream skill code."""
import hashlib
import pytest
from src.figures.nature_receipts import COMMIT, validate_nature_receipt


def receipt():
    pdf = b"%PDF-test-host-receipt"
    return {"nature_skill_receipt": {"version": 1, "commit": COMMIT, "status": "review_required",
        "pdf_sha256": hashlib.sha256(pdf).hexdigest(), "checks": {
        "source_preflight": {"status": "review_required"},
        "panel_alignment": {"status": "not_applicable"},
        "pdf_text": {"status": "pass"}, "pdf_collisions": {"status": "warn"}}}}, pdf


def test_accepts_actual_supported_statuses_and_matching_bytes():
    manifest, pdf = receipt()
    assert validate_nature_receipt(manifest, pdf=pdf)["status"] == "review_required"


@pytest.mark.parametrize("check", ["source_preflight", "panel_alignment", "pdf_text", "pdf_collisions"])
@pytest.mark.parametrize("status", ["fail", "not_auditable", "not_run"])
def test_unexecuted_or_failed_checks_cannot_publish(check, status):
    manifest, pdf = receipt()
    manifest["nature_skill_receipt"]["checks"][check]["status"] = status
    with pytest.raises(ValueError):
        validate_nature_receipt(manifest, pdf=pdf)


def test_missing_receipt_and_changed_pdf_fail_closed():
    with pytest.raises(ValueError):
        validate_nature_receipt({})
    manifest, _ = receipt()
    with pytest.raises(ValueError):
        validate_nature_receipt(manifest, pdf=b"%PDF-another-version")
