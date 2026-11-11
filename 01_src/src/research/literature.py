"""Generic scholarly identity search for the General Agent.

One capability, many registries. Callers pass whatever they have (natural
language query, title, DOI, arXiv id, authors, year) and receive structured
candidates with provenance, identifier match flags and per-source errors.

The module *reports* evidence. It never decides that a citation is "verified":
that judgement stays with the agent, which can search again, fetch a publisher
page, or ask the user. The only hard rule enforced here is honesty - a record
is only reported when a registry actually returned it. Network failures are
returned as errors, never as "not found".
"""
from __future__ import annotations

import html
import os
import re
import unicodedata
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from difflib import SequenceMatcher
from typing import Any, Iterable

import httpx

TIMEOUT = 8.0
RETRIES = 1
POOL = 6
# Hard wall-clock budget for one call: return the evidence that arrived,
# report stragglers as "deadline" instead of making the caller wait.
DEADLINE = 6.0
# Only controls whether a candidate is worth *showing*; it never marks a
# citation as verified.
CANDIDATE_FLOOR = 0.55
SOURCE_PRIORITY = (
    "Crossref", "Europe PMC", "PubMed", "OpenAlex", "arXiv", "DataCite", "Semantic Scholar",
)
_DOI_PATTERN = re.compile(r"(?i)\b10\.\d{4,9}/[^\s<>\[\]{}\"'，。；：、]+")
_ARXIV_ID_PATTERN = re.compile(r"(?i)(?:arxiv[:/\s]*|abs/)(\d{4}\.\d{4,5})(?:v\d+)?")
_CJK = re.compile(r"[\u4e00-\u9fff]")

_MIN_INTERVAL = {"pubmed": 0.34, "arxiv": 3.0}
_last_call: dict[str, float] = {}
_rate_lock = threading.Lock()


# ---------------------------------------------------------------- helpers
def normalize_doi(value: Any) -> str:
    doi = str(value or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi\s*:\s*", "", doi, flags=re.I)
    return doi.rstrip(".,;:，。；）)]}")


def norm_title(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = html.unescape(text).casefold()
    text = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def title_similarity(left: Any, right: Any) -> float:
    a, b = norm_title(left), norm_title(right)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ratio = SequenceMatcher(None, a, b).ratio()
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 20 and shorter in longer and len(shorter) / len(longer) >= 0.6:
        return max(ratio, 0.9)
    return ratio


def first_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return str(value[0]) if value else ""
    return str(value or "")


def _headers() -> dict[str, str]:
    mailto = str(os.environ.get("CITRUSQA_CROSSREF_MAILTO") or "").strip()
    agent = "CitrusQA/1.0 (scholarly identity search"
    if mailto:
        agent += f"; mailto:{mailto}"
    return {"User-Agent": agent + ")", "Accept": "application/json, application/atom+xml;q=0.9, */*;q=0.8"}


def _throttle(source: str) -> None:
    gap = _MIN_INTERVAL.get(source)
    if not gap:
        return
    with _rate_lock:
        wait = _last_call.get(source, 0.0) + gap - time.time()
        if wait > 0:
            time.sleep(wait)
        _last_call[source] = time.time()


def _get(url: str, params: dict | None = None, timeout: float = TIMEOUT,
         source: str = "") -> tuple[int | str, Any, str]:
    """Return (status, payload, error). ``status`` is 200 or an error token."""
    _throttle(source)
    last_error = ""
    for attempt in range(RETRIES + 1):
        try:
            response = httpx.get(url, params=params, timeout=timeout,
                                 follow_redirects=True, headers=_headers())
            if response.status_code == 200:
                if "atom" in response.headers.get("content-type", "") or response.text.lstrip().startswith("<?xml"):
                    return 200, response.text, ""
                try:
                    return 200, response.json(), ""
                except ValueError:
                    return 200, response.text, ""
            if response.status_code in (400, 404):
                return response.status_code, None, ""
            if response.status_code == 429:
                last_error = "http_429"
            elif response.status_code in (401, 403):
                return response.status_code, None, "http_blocked"
            else:
                last_error = f"http_{response.status_code}"
        except Exception as exc:  # noqa: BLE001 - network must not raise into tools
            last_error = "timeout" if "imeout" in type(exc).__name__ else type(exc).__name__
        if attempt < RETRIES:
            time.sleep(0.5 * (attempt + 1))
    return 0, None, last_error or "network_error"


def _rec(source: str, *, title: str, authors: str = "", year: str = "", venue: str = "",
         doi: str = "", arxiv_id: str = "", kind: str = "journal-article",
         url: str = "", extra: str = "") -> dict[str, Any]:
    doi = normalize_doi(doi)
    return {
        "title": re.sub(r"\s+", " ", str(title or "")).strip(),
        "authors": str(authors or "").strip(),
        "year": str(year or "")[:4],
        "venue": str(venue or "").strip(),
        "doi": doi,
        "arxiv_id": str(arxiv_id or "").strip(),
        "type": kind,
        "source": source,
        "url": url or (f"https://doi.org/{doi}" if doi else ""),
        "extra": extra,
    }


# ---------------------------------------------------------------- registries
def _crossref_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.crossref.org/works/{doi}", source="crossref")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    item = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    return 200, [_crossref_record(item, "Crossref")], ""


def _crossref_record(item: dict, source: str = "Crossref") -> dict:
    issued = (item.get("issued") or {}).get("date-parts") or [[""]]
    authors = "; ".join(
        " ".join(filter(None, [a.get("given"), a.get("family")])) for a in (item.get("author") or [])[:10]
    )
    return _rec(source,
                title=first_text(item.get("title")),
                authors=authors,
                year=str((issued[0] or [""])[0] or "") if issued else "",
                venue="; ".join(item.get("container-title") or []),
                doi=item.get("DOI", ""),
                kind=str(item.get("type") or "journal-article"),
                url=str(item.get("URL") or ""))


def _crossref_search(query: str, rows: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://api.crossref.org/works",
                                  {"query.bibliographic": query[:300], "rows": rows},
                                  source="crossref")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    items = (payload.get("message") or {}).get("items") or []
    return 200, [_crossref_record(item) for item in items if isinstance(item, dict)], ""


def _datacite_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.datacite.org/dois/{doi}", source="datacite")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    attributes = (payload.get("data") or {}).get("attributes") or {}
    titles = attributes.get("titles") or [{}]
    container = attributes.get("container") or {}
    types = attributes.get("types") or {}
    return 200, [_rec("DataCite",
                      title=(titles[0] or {}).get("title", ""),
                      authors="; ".join(str(c.get("name") or "") for c in (attributes.get("creators") or [])[:10]),
                      year=attributes.get("publicationYear", ""),
                      venue=str(container.get("title") or attributes.get("publisher") or ""),
                      doi=attributes.get("doi", ""),
                      kind=str(types.get("resourceTypeGeneral") or "dataset"),
                      url=str(attributes.get("url") or ""))], ""


def _datacite_arxiv(arxiv_id: str) -> tuple[int | str, list[dict], str]:
    """arXiv preprints are registered in DataCite under ``10.48550/arXiv.<id>``.

    The arXiv API serialises requests and regularly needs >6 s per DOI batch,
    so a deadline kill is common; DataCite answers in ~1.5 s and carries the
    same identifier. The record is backfilled with ``arxiv_id`` so that callers
    still see an identifier match.
    """
    status, records, error = _datacite_work(f"10.48550/arXiv.{arxiv_id}")
    for record in records:
        record["arxiv_id"] = arxiv_id
        record["source"] = "DataCite (arXiv DOI)"
        if not record.get("venue"):
            record["venue"] = "arXiv"
    return status, records, error


def _openalex_record(item: dict) -> dict:
    authors = "; ".join(str((a.get("author") or {}).get("display_name") or "")
                        for a in (item.get("authorships") or [])[:10])
    location = item.get("primary_location") or {}
    venue = str((location.get("source") or {}).get("display_name") or "")
    kind = str(item.get("type") or "journal-article")
    return _rec("OpenAlex",
                title=item.get("title") or item.get("display_name") or "",
                authors=authors,
                year=item.get("publication_year", ""),
                venue=venue,
                doi=str(item.get("doi") or ""),
                kind=kind,
                url=str(item.get("id") or ""))


def _openalex_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.openalex.org/works/https://doi.org/{doi}",
                                  source="openalex")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    return 200, [_openalex_record(payload)], ""


def _openalex_search(query: str, per_page: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://api.openalex.org/works",
                                  {"search": query[:300], "per-page": per_page},
                                  source="openalex")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    return 200, [_openalex_record(item) for item in (payload.get("results") or []) if isinstance(item, dict)], ""


def _epmc_records(payload: dict) -> list[dict]:
    records = []
    for item in ((payload.get("resultList") or {}).get("result") or []):
        journal = ((item.get("journalInfo") or {}).get("journal") or {}).get("title") or item.get("journalTitle") or ""
        pmid = str(item.get("pmid") or "")
        url = f"https://europepmc.org/article/MED/{pmid}" if pmid else (f"https://doi.org/{item.get('doi')}" if item.get("doi") else "")
        kind = "preprint" if str(item.get("source") or "").upper() in {"PPR", "PREPRINT"} else "journal-article"
        records.append(_rec("Europe PMC",
                            title=item.get("title", ""),
                            authors=item.get("authorString", ""),
                            year=item.get("pubYear", ""),
                            venue=str(journal),
                            doi=item.get("doi", ""),
                            kind=kind,
                            url=url,
                            extra=f"pmid:{pmid}" if pmid else ""))
    return records


def _epmc_search(query: str, page_size: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                  {"query": query, "format": "json", "resultType": "core",
                                   "pageSize": page_size},
                                  source="epmc")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    return 200, _epmc_records(payload), ""


def _epmc_doi(doi: str) -> tuple[int | str, list[dict], str]:
    return _epmc_search(f'DOI:"{doi}"', page_size=3)


def _pubmed_search(query: str, retmax: int = 6) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                                  {"db": "pubmed", "term": query, "retmode": "json", "retmax": retmax},
                                  source="pubmed")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    ids = ((payload.get("esearchresult") or {}).get("idlist") or [])[:retmax]
    if not ids:
        return 200, [], ""
    status, summary, error = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi",
                                  {"db": "pubmed", "id": ",".join(ids), "retmode": "json"},
                                  source="pubmed")
    if status != 200 or not isinstance(summary, dict):
        return status, [], error
    records = []
    for uid in ids:
        item = ((summary.get("result") or {}).get(uid) or {})
        if not isinstance(item, dict) or not item.get("title"):
            continue
        doi = ""
        for value in item.get("articleids") or []:
            if str(value.get("idtype")) == "doi":
                doi = str(value.get("value") or "")
        records.append(_rec("PubMed",
                            title=item.get("title", ""),
                            authors="; ".join(str(a.get("name") or "") for a in (item.get("authors") or [])[:10]),
                            year=str(item.get("pubdate") or "")[:4],
                            venue=item.get("fulljournalname", ""),
                            doi=doi,
                            url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                            extra=f"pmid:{uid}"))
    return 200, records, ""


def _arxiv_entries(xml_text: str) -> list[dict]:
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    records = []
    for entry in root.findall("a:entry", ns):
        raw_id = first_text(entry.findtext("a:id", default="", namespaces=ns))
        match = re.search(r"abs/([0-9]+\.[0-9]+)", raw_id)
        journal_ref = first_text(entry.findtext("arxiv:journal_ref", default="", namespaces=ns))
        doi = first_text(entry.findtext("arxiv:doi", default="", namespaces=ns))
        records.append(_rec("arXiv",
                            title=first_text(entry.findtext("a:title", default="", namespaces=ns)),
                            authors="; ".join(first_text(a.findtext("a:name", default="", namespaces=ns))
                                              for a in entry.findall("a:author", ns)[:10]),
                            year=first_text(entry.findtext("a:published", default="", namespaces=ns))[:4],
                            venue=journal_ref,
                            doi=doi,
                            arxiv_id=match.group(1) if match else "",
                            kind="preprint",
                            url=raw_id,
                            extra="journal_ref:" + journal_ref if journal_ref else ""))
    return records


def _arxiv_by_id(arxiv_id: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://export.arxiv.org/api/query",
                                  {"id_list": arxiv_id}, source="arxiv")
    if status != 200 or not isinstance(payload, str):
        return status, [], error
    return 200, _arxiv_entries(payload), ""


def _arxiv_by_title(title: str, max_results: int = 5) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://export.arxiv.org/api/query",
                                  {"search_query": f'ti:"{title[:200]}"', "max_results": max_results},
                                  source="arxiv")
    if status != 200 or not isinstance(payload, str):
        return status, [], error
    return 200, _arxiv_entries(payload), ""


def _s2_doi(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(
        f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
        {"fields": "title,year,venue,authors,externalIds,publicationTypes"}, source="s2")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    ids = payload.get("externalIds") or {}
    kinds = payload.get("publicationTypes") or []
    return 200, [_rec("Semantic Scholar",
                      title=payload.get("title", ""),
                      authors="; ".join(str(a.get("name") or "") for a in (payload.get("authors") or [])[:10]),
                      year=payload.get("year", ""),
                      venue=payload.get("venue", ""),
                      doi=ids.get("DOI", ""),
                      arxiv_id=ids.get("ArXiv", ""),
                      kind="preprint" if "Preprint" in kinds else "journal-article")], ""


def _s2_search(title: str, limit: int = 5) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://api.semanticscholar.org/graph/v1/paper/search",
                                  {"query": title[:300], "limit": limit,
                                   "fields": "title,year,venue,authors,externalIds,publicationTypes"},
                                  source="s2")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    records = []
    for item in payload.get("data") or []:
        ids = item.get("externalIds") or {}
        records.append(_rec("Semantic Scholar",
                            title=item.get("title", ""),
                            authors="; ".join(str(a.get("name") or "") for a in (item.get("authors") or [])[:10]),
                            year=item.get("year", ""),
                            venue=item.get("venue", ""),
                            doi=ids.get("DOI", ""),
                            arxiv_id=ids.get("ArXiv", "")))
    return 200, records, ""


# ---------------------------------------------------------------- aggregation
def _translate(query: str) -> str:
    if not _CJK.search(query or ""):
        return ""
    try:
        from src.retrieval.query_translation import cached_translation
        return str(cached_translation(query) or "").strip()
    except Exception:  # noqa: BLE001 - translation is optional
        return ""


def _dedupe(records: Iterable[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for record in records:
        if not record.get("title"):
            continue
        key = record["doi"] or (f"arxiv:{record['arxiv_id']}" if record["arxiv_id"] else "") \
            or norm_title(record["title"])
        existing = seen.get(key)
        if existing is None:
            seen[key] = record
            continue
        # keep the first (higher-priority source) but retain extra identifiers
        for field in ("arxiv_id", "doi"):
            if not existing.get(field) and record.get(field):
                existing[field] = record[field]
        if record.get("venue") and not existing.get("venue"):
            existing["venue"] = record["venue"]
        existing["also_in"] = list(dict.fromkeys((existing.get("also_in") or []) + [record["source"]]))
    return list(seen.values())


_AUTHOR_SPLIT = re.compile(r"\s*(?:;|\band\b|&|、|，|,)\s*")
_INITIALS_TOKEN = re.compile(r"^(?:[A-Z]\.?){1,5}$")


_TRANSLITERATE = str.maketrans({
    "ı": "i", "ğ": "g", "ş": "s", "ç": "c", "ö": "o", "ü": "u",
    "ø": "o", "å": "a", "æ": "ae", "ß": "ss", "ł": "l", "đ": "d",
})


_DASHES = {0x2010: "-", 0x2011: "-", 0x2012: "-", 0x2013: "-", 0x2014: "-",
           0x2015: "-", 0x2212: "-", 0xFF0D: "-"}


def _fold(text: str) -> str:
    """Lowercase and strip diacritics so Öztürk and Ozturk compare equal."""
    value = str(text or "").translate(_DASHES).translate(_TRANSLITERATE)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


def _name_key(name: str) -> tuple[str, str] | None:
    """Map one author name to (surname, initials).

    Handles "Given Middle Surname" (registry style) and "Surname AB" (transcript
    style).  A trailing all-capitals token such as "SS" or "Y" is read as initials
    and the leading token becomes the surname; anything else follows the
    registry convention where the last token is the surname.
    """
    parts = [piece for piece in re.split(r"\s+", name.strip(" .")) if piece]
    if not parts:
        return None
    tail = parts[-1].rstrip(".")
    if len(parts) >= 2 and _INITIALS_TOKEN.fullmatch(tail):
        surname = re.sub(r"[^a-z\-']", "", _fold(parts[0]))
        initials = "".join(re.findall(r"[A-Z]", " ".join(parts[1:]))).casefold()
    else:
        surname = re.sub(r"[^a-z\-']", "", _fold(parts[-1]))
        initials = "".join(piece[0] for piece in parts[:-1]
                           if piece and piece[0].isalpha()).casefold()
    if not surname:
        return None
    return surname, initials


def _author_keys(text: str) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for raw in _AUTHOR_SPLIT.split(str(text or "")):
        name = str(raw).strip()
        if len(name) < 2:
            continue
        key = _name_key(name)
        if key:
            keys.add(key)
    return keys


def _initials_compatible(supplied: str, registry: str) -> bool:
    """Compare initials when a reference list abbreviates the given names.

    Citation strings routinely keep only "de Paula Santos Martins" for
    "Cristina de Paula Santos Martins", i.e. a sub-sequence of the registry
    initials; demanding the same leading initial flags correct citations as
    author conflicts.
    """
    if not supplied or not registry:
        return True
    if supplied == registry or registry.startswith(supplied) or supplied.startswith(registry):
        return True
    if len(registry) == 1 and registry == supplied[:1]:
        return True
    it = iter(registry)
    return all(ch in it for ch in supplied)


def _author_conflict(supplied: list[str] | str, registry: list[str] | str) -> bool:
    """True only when the two author lists clearly disagree.

    Reference lists vary in name order ("Wang Min" vs "Min Wang"), in how many
    authors they list, and in initials ("Kim SS" vs "Sang Suk Kim"). Judging
    order or count as a conflict produced false mismatches, so compare the two
    as token bags: enough shared name tokens means they agree.
    """
    left = _name_tokens(supplied)
    right = _name_tokens(registry)
    if not left or not right:
        return False
    shared = left & right
    if not shared:
        return True
    if (len(shared) / len(left)) < 0.5:
        return True
    # 姓氏相同但缩写首字母不同，指的是不同的人（Wang Y vs Wang X）。
    left_init = _surname_initials(supplied)
    right_init = _surname_initials(registry)
    if left_init and right_init and not (left_init & right_init):
        return True
    return False


def _surname_initials(value: list[str] | str) -> set[tuple[str, str]]:
    """Extract (surname, initial) candidates from abbreviated author names.

    引用清单常见 "Wang Y" / "Y Wang" / "Arenas-Arenas Francisco J." 三种写法，
    姓名顺序不固定，先按词折叠重音再同时给出首词与末词两种候选，
    比较时用交集判断，避免把顺序差异误判成不同作者。
    """
    if isinstance(value, (list, tuple)):
        text = " ; ".join(str(v) for v in value)
    else:
        text = str(value or "")
    out: set[tuple[str, str]] = set()
    for unit in re.split(r"[;,]", text):
        folded = _fold(unit)
        tokens = [t for t in re.split(r"[^A-Za-z]+", folded) if t]
        if len(tokens) < 2:
            continue
        letters = [t for t in tokens if len(t) == 1]
        words = [t for t in tokens if len(t) >= 3]
        if len(letters) != 1 or not words:
            continue
        letter = letters[0].lower()
        for candidate in {words[0].lower(), words[-1].lower()}:
            if candidate != letter:
                out.add((candidate, letter))
    return out

def _name_tokens(value: list[str] | str) -> set[str]:
    if isinstance(value, (list, tuple)):
        text = " ".join(str(v) for v in value)
    else:
        text = str(value or "")
    text = _fold(text)
    tokens = {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}
    return tokens


def _annotate(record: dict, query: str, doi: str, arxiv_id: str,
              authors: str, year: str, claimed_title: str = "") -> dict:
    record = dict(record)
    # ``claimed_title`` is the title as written in the citing document. It is the
    # only signal available when the caller supplies a bare DOI, and it is a
    # stricter reference than the (possibly absent) free-text query.
    reference_title = str(claimed_title or "").strip() or str(query or "").strip()
    record["title_similarity"] = round(title_similarity(reference_title or record["title"], record["title"]), 3)
    record["identifier_match"] = bool(
        (doi and record["doi"] == normalize_doi(doi))
        or (arxiv_id and record["arxiv_id"] == arxiv_id))
    conflicts = []
    if record["identifier_match"]:
        if reference_title and norm_title(reference_title):
            if record["title_similarity"] < 0.82:
                conflicts.append(f"title:{record['title_similarity']}")
            elif norm_title(reference_title) != norm_title(record["title"]):
                # Same identifier but the title is not byte-identical after
                # normalisation (a dropped tail clause scores ~0.96): that is a
                # rewritten title, not a match.
                conflicts.append(f"title_variant:{record['title_similarity']}")
        if _author_conflict(authors, record["authors"]):
            conflicts.append("authors")
        if year and record["year"]:
            supplied_year = str(year)[:4]
            if supplied_year.isdigit() and str(record["year"])[:4].isdigit():
                # 在线优先/纸本年份在不同登记库之间常差 1 年，不算篡改。
                if abs(int(supplied_year) - int(str(record["year"])[:4])) > 1:
                    conflicts.append(f"year:{supplied_year}->{record['year']}")
            elif supplied_year != str(record["year"])[:4]:
                conflicts.append(f"year:{supplied_year}->{record['year']}")
    record["field_conflicts"] = conflicts
    return record


def _web_candidate(query: str, year: str) -> tuple[list[dict], str]:
    """Optional fallback: let the configured web search surface a DOI, then
    confirm it against a registry. Results are candidates, never confirmations."""
    try:
        from src.tools.deepseek_search_fetch import deepseek_search
    except Exception as exc:  # noqa: BLE001
        return [], f"web:{type(exc).__name__}"
    try:
        text = str(deepseek_search.invoke({"query": " ".join(p for p in (query[:160], year, "doi") if p),
                                           "count": 6}))
    except Exception as exc:  # noqa: BLE001
        return [], f"web:{type(exc).__name__}"
    if text.startswith("[WEB_"):
        return [], text.splitlines()[0][:120]
    found: list[dict] = []
    for raw in list(dict.fromkeys(normalize_doi(x) for x in _DOI_PATTERN.findall(text)))[:3]:
        if not raw:
            continue
        status, records, _ = _crossref_work(raw)
        if status == 200 and records:
            record = records[0]
            record["source"] = "Web search"
            if title_similarity(query, record["title"]) >= 0.75:
                found.append(record)
    return found, "" if found else "web:no_matching_doi"


def search_literature(query: str = "", doi: str = "", arxiv_id: str = "",
                      authors: str = "", year: str = "", limit: int = 8,
                      sources: str = "", web_fallback: bool = False,
                      claimed_title: str = "") -> dict[str, Any]:
    """Search scholarly records; returns candidates + per-source errors.

    Nothing here decides "verified": callers inspect ``identifier_match`` and
    ``field_conflicts`` and decide what to report.

    ``claimed_title`` is the title *as written in the citing document*. Pass it
    whenever the citation carries a title: an identifier that resolves to a
    record whose title differs after normalisation is a rewritten title, and
    that must show up as a conflict instead of a clean match.
    """
    started = time.time()
    doi = normalize_doi(doi or (query if _DOI_PATTERN.fullmatch(str(query or "").strip()) else ""))
    arxiv_id = arxiv_id or ""
    if not arxiv_id:
        match = _ARXIV_ID_PATTERN.search(str(query or ""))
        arxiv_id = match.group(1) if match else ""
    query = str(query or "").strip()
    if doi and normalize_doi(query) == doi:
        query = ""  # a bare DOI is an identifier, not a title

    wanted = [s.strip().lower() for s in str(sources or "").split(",") if s.strip()]
    errors: dict[str, str] = {}
    notes: list[str] = []
    translated = "" if doi or arxiv_id else _translate(query)
    tasks: list[tuple[str, Any]] = []

    def add(name: str, fn, *args) -> None:
        if wanted and name.lower() not in wanted and name.lower().replace(" ", "") not in wanted:
            return
        tasks.append((name, lambda: fn(*args)))

    if doi:
        # Fast path: the two registries that cover nearly all DOIs. The slower
        # fallbacks below only run when these two return nothing.
        add("Crossref", _crossref_work, doi)
        add("OpenAlex", _openalex_work, doi)
        add("Europe PMC", _epmc_doi, doi)
    if arxiv_id:
        add("arXiv", _arxiv_by_id, arxiv_id)
        # Cheap, fast registry fallback for the same identifier.
        add("DataCite", _datacite_arxiv, arxiv_id)
    for text in [t for t in (query, translated) if t]:
        add("Crossref", _crossref_search, text, 8)
        add("OpenAlex", _openalex_search, text, 8)
        add("Europe PMC", _epmc_search, f'TITLE:"{text[:200]}"', 8)
        add("PubMed", _pubmed_search, f"{text[:200]}[Title]", 6)
        if not arxiv_id and (wanted or "arxiv" in text.lower()):
            add("arXiv", _arxiv_by_title, text, 5)

    records: list[dict] = []
    if tasks:
        pool = ThreadPoolExecutor(max_workers=min(POOL, len(tasks)))
        futures = [(name, pool.submit(fn)) for name, fn in tasks]
        pending = {future: name for name, future in futures}
        deadline = time.time() + DEADLINE
        resolved = False
        try:
            while pending and not resolved:
                remaining = max(0.0, deadline - time.time())
                done, _ = wait(list(pending), timeout=remaining, return_when=FIRST_COMPLETED)
                if not done:
                    break
                for future in done:
                    name = pending.pop(future)
                    try:
                        status, found, error = future.result()
                    except Exception as exc:  # noqa: BLE001
                        errors[name] = type(exc).__name__
                        continue
                    if error:
                        errors[name] = error
                    records.extend(found or [])
                    if doi and found:
                        resolved = True
        finally:
            for future, name in pending.items():
                future.cancel()
                errors.setdefault(name, "deadline")
            pool.shutdown(wait=False, cancel_futures=True)
        if doi and not records:
            status, found, error = _datacite_work(doi)
            if found:
                records.extend(found)
            elif error:
                errors["DataCite"] = error
            if not records:
                status, found, error = _s2_doi(doi)
                if found:
                    records.extend(found)
                elif error:
                    errors["Semantic Scholar"] = error

    merged = _dedupe(records)
    annotated = [_annotate(record, query, doi, arxiv_id, authors, year,
                                 claimed_title=claimed_title) for record in merged]
    if not annotated and web_fallback and not doi and not arxiv_id and len(norm_title(query)) >= 20:
        web_records, web_error = _web_candidate(query, year)
        if web_records:
            notes.append("登记库无命中，网页检索补充了候选 DOI 并已回登记库核对；需人工确认。")
            annotated = [_annotate(record, query, "", "", authors, year,
                                       claimed_title=claimed_title) for record in web_records]
        elif web_error:
            errors["Web search"] = web_error
    annotated.sort(key=lambda r: (0 if r["identifier_match"] else 1,
                                  -float(r["title_similarity"] or 0),
                                  SOURCE_PRIORITY.index(r["source"]) if r["source"] in SOURCE_PRIORITY else 99))
    if query and not annotated and not errors:
        notes.append("登记库没有返回候选；未查到不等于文献不存在。")
    return {
        "query_used": query,
        "translated": translated,
        "candidates": annotated[:max(1, min(int(limit or 8), 20))],
        "errors": errors,
        "notes": notes,
        "elapsed_ms": int((time.time() - started) * 1000),
    }


def lookup_doi(doi: str) -> tuple[str, dict | None, str]:
    """Backward-compatible single-record lookup: (provider, record, error)."""
    doi = normalize_doi(doi)
    if not doi:
        return "", None, "empty_doi"
    checks = [("Crossref", _crossref_work), ("DataCite", _datacite_work),
              ("OpenAlex", _openalex_work), ("Semantic Scholar", _s2_doi)]
    errors = []
    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        futures = {name: pool.submit(fn, doi) for name, fn in checks}
        for name, _ in checks:
            status, records, error = futures[name].result()
            if error:
                errors.append(f"{name}:{error}")
            if records:
                return name, records[0], ""
    return "", None, "; ".join(errors)


def evidence_row(record: dict) -> dict[str, str]:
    """Flatten a candidate for the verification spreadsheet."""
    conflicts = record.get("field_conflicts") or []
    return {
        "title": record.get("title", ""),
        "authors": record.get("authors", ""),
        "year": record.get("year", ""),
        "venue": record.get("venue", ""),
        "doi": record.get("doi", ""),
        "arxiv_id": record.get("arxiv_id", ""),
        "type": record.get("type", ""),
        "source": record.get("source", ""),
        "url": record.get("url", ""),
        "title_similarity": record.get("title_similarity"),
        "identifier_match": record.get("identifier_match", False),
        "conflicts": "；".join(conflicts),
    }
