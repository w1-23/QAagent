"""Pure finding construction, without storage."""
import hashlib
import json
import re
SCHEMA_VERSION="integrity-finding/1"
_SHA=re.compile(r"[a-f0-9]{64}")
def finding(*, rule: str, version: str, locations: list[dict], evidence: dict,
            applicability: list[str], counterexamples: list[str], coverage: dict,
            summary: str = "") -> dict:
    if not rule or not version or not locations or any(
            not _SHA.fullmatch(str(loc.get("sha256") or loc.get("source_sha256") or ""))
            for loc in locations):
        raise ValueError("finding requires a versioned rule and hashed source locations")
    payload = {"schema_version": SCHEMA_VERSION, "rule": rule, "rule_version": version,
               "locations": locations, "evidence": evidence, "applicability": applicability,
               "counterexamples": counterexamples, "coverage": coverage, "summary": summary,
               "origin": "automated", "interpretation": "observation_requires_human_context"}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False,
                         separators=(",", ":")).encode("utf-8")
    return {**payload, "finding_id": hashlib.sha256(encoded).hexdigest(),
            "review_status": "pending", "human_review_required": True}
