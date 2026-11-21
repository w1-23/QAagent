"""Version-bound full text and reuse metadata from the official PMC Cloud bucket."""
from hashlib import md5
import json
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree

import requests

from .rights import TRUSTED_LICENSES, license_token

_BASE = "https://pmc-oa-opendata.s3.amazonaws.com"
_BUCKET_NS = "{http://s3.amazonaws.com/doc/2006-03-01/}"
_PMC_ROUTES = {
    "pmc.ncbi.nlm.nih.gov": r"/articles/(PMC[0-9]+)(?:\.(?P<version>[1-9][0-9]*))?/?",
    "www.ebi.ac.uk": r"/europepmc/webservices/rest/(PMC[0-9]+)/fullTextXML",
}
PMC_CLOUD_EVIDENCE = "ncbi_pmc_cloud"
PMC_CLOUD_METADATA_URL = re.compile(rf"{re.escape(_BASE)}/metadata/PMC[0-9]+\.[1-9][0-9]*\.json")


def _read(path: str, max_bytes: int, *, params=None) -> bytes:
    with requests.get(_BASE + path, params=params, timeout=(2, 3), stream=True,
                      allow_redirects=False) as response:
        if response.status_code != 200:
            raise ValueError("pmc_cloud_response_unavailable")
        data = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            if len(data) + len(chunk) > max_bytes:
                raise ValueError("pmc_cloud_response_too_large")
            data.extend(chunk)
        return bytes(data)


def _only_version(pmcid: str) -> str:
    prefix = f"metadata/{pmcid}."
    data = _read("/", 32768, params={"list-type": "2", "prefix": prefix, "max-keys": "2"})
    # S3 listings have no DTD or entity declarations.
    if b"<!" in data:
        return ""
    listing = ElementTree.fromstring(data.decode("utf-8"))
    keys = listing.findall(f"{_BUCKET_NS}Contents/{_BUCKET_NS}Key")
    if (listing.tag != f"{_BUCKET_NS}ListBucketResult"
            or listing.findtext(f"{_BUCKET_NS}Prefix") != prefix
            or listing.findtext(f"{_BUCKET_NS}IsTruncated") != "false"
            or len(keys) != 1):
        return ""
    match = re.fullmatch(rf"{re.escape(prefix)}([1-9][0-9]*)\.json", keys[0].text or "")
    return match[1] if match else ""


def licensed_full_text(source_url: str, *, doi: str, pmid: str, max_bytes: int) -> tuple[dict, str, str] | None:
    """Return the official body, normalized license and the exact metadata URL."""
    from citrus_refcheck.providers import normalize_doi

    try:
        parsed = urlsplit(source_url)
        route = _PMC_ROUTES.get(parsed.netloc)
        match = re.fullmatch(route, parsed.path) if route and parsed.scheme == "https" else None
        if match is None:
            return None
        pmcid = match[1]
        version = match.groupdict().get("version") or _only_version(pmcid)
        if not version:
            return None
        key = f"{pmcid}.{version}"
        metadata_path = f"/metadata/{key}.json"
        record = json.loads(_read(metadata_path, 131072).decode("utf-8"))
        if not isinstance(record, dict):
            return None
        license = license_token(record.get("license_code"))
        official_doi = normalize_doi(record.get("doi"))
        title = record.get("title")
        if (record.get("pmcid") != pmcid or type(record.get("version")) is not int
                or str(record["version"]) != version or record.get("is_retracted") is not False
                or record.get("is_pmc_openaccess") is not True or license not in TRUSTED_LICENSES
                or (doi and official_doi != doi) or (pmid and str(record.get("pmid")) != pmid)
                or not isinstance(title, str) or not title.strip()):
            return None
        text_path = f"/{key}/{key}.txt"
        text_uri = record.get("text_url")
        expected = re.fullmatch(rf"{re.escape('s3://pmc-oa-opendata' + text_path)}\?md5=([0-9a-f]{{32}})",
                                text_uri if isinstance(text_uri, str) else "")
        if expected is None:
            return None
        raw = _read(text_path, max_bytes)
        if md5(raw, usedforsecurity=False).hexdigest() != expected[1]:
            return None
        body = raw.decode("utf-8")
        if "\x00" in body:
            return None
        return ({"url": _BASE + text_path, "title": title, "format": "pmc_cloud_text",
                 "content": body, "doi": official_doi}, license, _BASE + metadata_path)
    except (requests.RequestException, OSError, ValueError, ElementTree.ParseError):
        return None
