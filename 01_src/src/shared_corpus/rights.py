"""Publication-right policy for shared-corpus proposals.

A document version may be published only when either

* an administrator independently grants it through ``Catalog.grant``, or
* the *original candidate archive* carries explicit, verified open-license
  metadata from a trusted source.

Retention approval in ``src.core.corpus_review`` is a separate concern and is
never treated as a publication right.  Upload consent is sharing consent, not
a licence.  When in doubt this module returns ``needs_review`` (fail closed).
"""
from __future__ import annotations

import re
from typing import Any

TRUSTED_LICENSES = frozenset({
    "cc0",
    "public-domain",
    "cc-by",
    "cc-by-sa",
})

TRUSTED_PERMISSION_SOURCES = frozenset({
    "crossref",
    "doaj",
    "openalex",
    "publisher",
    "repository",
    "trusted-source",
    "unpaywall",
})

TRUSTED_LICENSE_MARKERS = frozenset({
    "open-license",
    "trusted-source",
    "verified-license",
})

_LICENSE_KEYS = (
    "license",
    "license_id",
    "license_url",
    "reuse_license",
    "rights",
    "access_rights",
)

_CC_RE = re.compile(
    r"creativecommons\.org/(?:licenses|publicdomain)/([a-z0-9-]+)")
_SLUG_RE = re.compile(r"[^a-z0-9.-]+")
_VERSION_RE = re.compile(r"-(?:\d+(?:\.\d+)*)(?:-[a-z]+)?$")

_ALIASES = {
    "by": "cc-by",
    "by-sa": "cc-by-sa",
    "cc0-1-0": "cc0",
    "cc-zero": "cc0",
    "publicdomain": "public-domain",
    "public-domain-mark": "public-domain",
    "pd": "public-domain",
    "zero": "cc0",
}


def _normalize_marker(value: object) -> str:
    text = str(value or "").strip().casefold()
    text = _SLUG_RE.sub("-", text).strip("-")
    text = re.sub(r"-+", "-", text)
    return _ALIASES.get(text, text)


def license_token(value: object) -> str:
    """Normalize one licence value into a comparison token.

    Unknown values remain normalized strings so callers can record them
    without ever trusting them.
    """
    if isinstance(value, dict):
        value = (value.get("URL") or value.get("url") or value.get("id")
                 or value.get("name") or "")
    text = str(value or "").strip().casefold()
    if not text:
        return ""
    creative = _CC_RE.search(text)
    if creative:
        text = creative.group(1)
    else:
        text = text.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1]
        text = _SLUG_RE.sub("-", text).strip("-")
        text = re.sub(r"-+", "-", text)
        text = _VERSION_RE.sub("", text)
    token = _ALIASES.get(text, text)
    if token.startswith("cc-") and token.count("-") > 1:
        # Strip a trailing version that survived odd spellings.
        token = _VERSION_RE.sub("", token)
    return token


def _values(value: Any):
    if value is None:
        return
    if isinstance(value, (list, tuple, set)):
        for item in value:
            yield item
        return
    yield value


def evaluate_source_rights(meta: Any) -> dict[str, Any]:
    """Return the publication-right decision derived from archive metadata."""
    meta = meta if isinstance(meta, dict) else {}
    if meta.get("publish_permission") is True:
        source = _normalize_marker(meta.get("publish_permission_source"))
        if source in TRUSTED_PERMISSION_SOURCES:
            return {
                "status": "trusted",
                "basis": "trusted_metadata",
                "license": "",
                "reason": "trusted_publish_permission",
            }
    marker = _normalize_marker(
        meta.get("rights_basis") or meta.get("license_basis") or "")
    verified = meta.get("license_verified") is True or marker in TRUSTED_LICENSE_MARKERS
    if verified:
        for key in _LICENSE_KEYS:
            for value in _values(meta.get(key)):
                token = license_token(value)
                if token in TRUSTED_LICENSES:
                    return {
                        "status": "trusted",
                        "basis": "trusted_metadata",
                        "license": token,
                        "reason": "trusted_source_license",
                    }
    return {
        "status": "needs_review",
        "basis": "needs_review",
        "license": "",
        "reason": "license_not_trusted",
    }
