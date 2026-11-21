"""Registry response mappings extracted from QAagent 494b7dd; explicit transport only."""
from __future__ import annotations
import html, re, unicodedata, xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import quote
from contextvars import ContextVar
from contextlib import contextmanager
_DOI_PATTERN = re.compile(r'(?i)\b10\.\d{4,9}/[^\s<>\[\]{}\"\'，。；：、]+')
_ARXIV_ID_PATTERN = re.compile(r'(?i)(?:arxiv[:/\s]*|abs/)(\d{4}\.\d{4,5}|[a-z-]+(?:\.[a-z]{2})?/\d{7})(?:v\d+)?')
_TRANSLITERATE = str.maketrans({'ı':'i','ğ':'g','ş':'s','ç':'c','ö':'o','ü':'u','ø':'o','å':'a','æ':'ae','ß':'ss','ł':'l','đ':'d'})
_DASHES = {code:'-' for code in (0x2010,0x2011,0x2012,0x2013,0x2014,0x2015,0x2212,0xFF0D)}
_TRANSPORT = ContextVar("citrus_registry_transport", default=None)

@contextmanager
def using_transport(transport):
    token = _TRANSPORT.set(transport)
    try:
        yield
    finally:
        _TRANSPORT.reset(token)

def _get(url, params=None, timeout=8, source=""):
    transport = _TRANSPORT.get()
    if transport is None:
        raise RuntimeError("registry_transport_required")
    return transport.get(url, params=params, source=source)

def normalize_doi(value: Any) -> str:
    doi = str(value or "").strip().lower()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    doi = re.sub(r"^doi\s*:\s*", "", doi, flags=re.I)
    return doi.rstrip(".,;:，。；）)]}")


def norm_title(value: Any) -> str:
    text = re.sub(r"<[^>]+>", " ", str(value or ""))
    text = unicodedata.normalize('NFKC', html.unescape(text)).casefold()
    text = ''.join(character if character.isalnum() else ' ' for character in text)
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


def _rec(source: str, *, title: str, authors: str = "", year: str = "", venue: str = "",
         doi: str = "", arxiv_id: str = "", kind: str = "journal-article",
         url: str = "", extra: str = "", volume: str = "", issue: str = "",
         pages: str = "", article_number: str = "", years: list | None = None) -> dict[str, Any]:
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
        "volume": str(volume or ""), "issue": str(issue or ""),
        "pages": str(pages or ""), "article_number": str(article_number or ""),
        "years": years or [],
    }


def _crossref_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.crossref.org/works/{doi}", source="crossref")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    item = payload.get("message") if isinstance(payload.get("message"), dict) else payload
    if not item.get('DOI') or not item.get('title'):
        return 0, [], 'invalid_provider_payload'
    return 200, [_crossref_record(item, "Crossref")], ""


def _crossref_record(item: dict, source: str = "Crossref") -> dict:
    issued = (item.get("issued") or {}).get("date-parts") or [[""]]
    authors = "; ".join(
        " ".join(filter(None, [a.get("given"), a.get("family")])) for a in (item.get("author") or [])
    )
    record = _rec(source,
                title=first_text(item.get("title")),
                authors=authors,
                year=str((issued[0] or [""])[0] or "") if issued else "",
                venue="; ".join(item.get("container-title") or []),
                doi=item.get("DOI", ""),
                kind=str(item.get("type") or "journal-article"),
                url=str(item.get("URL") or ""),
                volume=item.get('volume', ''), issue=item.get('issue', ''),
                pages=item.get('page', ''), article_number=item.get('article-number', ''),
                years=[{'kind': key, 'year': str(parts[0][0])}
                       for key in ('published-print', 'published-online', 'issued')
                       if (parts := (item.get(key) or {}).get('date-parts')) and parts[0]])
    aliases = item.get('short-container-title')
    if isinstance(aliases, list):
        record['journal_aliases'] = [value for value in aliases if isinstance(value, str) and value.strip()][:8]
    relations = item.get('relation')
    if isinstance(relations, dict):
        record['related_dois'] = [normalize_doi(ref.get('id'))
            for kind, refs in relations.items() if kind in {'is-preprint-of', 'has-preprint', 'is-version-of', 'has-version'}
            and isinstance(refs, list) for ref in refs[:12]
            if isinstance(ref, dict) and ref.get('id-type') == 'doi' and ref.get('id')]
    return record


def _crossref_search(query: str, rows: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://api.crossref.org/works",
                                  {"query.bibliographic": query[:300], "rows": rows},
                                  source="crossref")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    items = (payload.get("message") or {}).get("items")
    if not isinstance(items, list):
        return 0, [], 'invalid_provider_payload'
    return 200, [_crossref_record(item) for item in items[:rows] if isinstance(item, dict)], ""


def _datacite_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.datacite.org/dois/{doi}", source="datacite")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    attributes = (payload.get("data") or {}).get("attributes") or {}
    if not attributes.get('doi') or not attributes.get('titles'):
        return 0, [], 'invalid_provider_payload'
    titles = attributes.get("titles") or [{}]
    container = attributes.get("container") or {}
    types = attributes.get("types") or {}
    return 200, [_rec("DataCite",
                      title=(titles[0] or {}).get("title", ""),
                      authors="; ".join(str(c.get("name") or "") for c in (attributes.get("creators") or [])),
                      year=attributes.get("publicationYear", ""),
                      venue=str(container.get("title") or ""),
                      doi=attributes.get("doi", ""),
                      kind=str(types.get("resourceTypeGeneral") or "dataset"),
                      url=str(attributes.get("url") or ""),
                      volume=container.get('volume', ''), issue=container.get('issue', ''),
                      pages='-'.join(str(container[k]) for k in ('firstPage', 'lastPage') if container.get(k)))], ""


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
                        for a in (item.get("authorships") or []))
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
                url=str(item.get("id") or ""),
                volume=(item.get('biblio') or {}).get('volume', ''),
                issue=(item.get('biblio') or {}).get('issue', ''),
                pages='-'.join(str((item.get('biblio') or {})[k]) for k in ('first_page', 'last_page')
                               if (item.get('biblio') or {}).get(k)))


def _openalex_work(doi: str) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get(f"https://api.openalex.org/works/https://doi.org/{doi}",
                                  source="openalex")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    if not (payload.get('title') or payload.get('display_name')):
        return 0, [], 'invalid_provider_payload'
    return 200, [_openalex_record(payload)], ""


def _openalex_search(query: str, per_page: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://api.openalex.org/works",
                                  {"search": query[:300], "per-page": per_page},
                                  source="openalex")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    if not isinstance(payload.get('results'), list):
        return 0, [], 'invalid_provider_payload'
    return 200, [_openalex_record(item) for item in payload['results'][:per_page] if isinstance(item, dict)], ""


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
                            extra=f"pmid:{pmid}" if pmid else "",
                            volume=(item.get('journalInfo') or {}).get('volume', ''),
                            issue=(item.get('journalInfo') or {}).get('issue', ''),
                            pages=item.get('pageInfo', '')))
    return records


def _epmc_search(query: str, page_size: int = 8) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                                  {"query": query, "format": "json", "resultType": "core",
                                   "pageSize": page_size},
                                  source="epmc")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    if not isinstance(payload.get('resultList'), dict) or not isinstance(payload['resultList'].get('result'), list):
        return 0, [], 'invalid_provider_payload'
    return 200, _epmc_records(payload)[:page_size], ""


def _epmc_doi(doi: str) -> tuple[int | str, list[dict], str]:
    return _epmc_search(f'DOI:"{doi}"', page_size=3)


def _pubmed_search(query: str, retmax: int = 6) -> tuple[int | str, list[dict], str]:
    status, payload, error = _get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi",
                                  {"db": "pubmed", "term": query, "retmode": "json", "retmax": retmax},
                                  source="pubmed")
    if status != 200 or not isinstance(payload, dict):
        return status, [], error
    if not isinstance((payload.get('esearchresult') or {}).get('idlist'), list):
        return 0, [], 'invalid_provider_payload'
    ids = payload['esearchresult']['idlist'][:retmax]
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
                            authors="; ".join(str(a.get("name") or "") for a in (item.get("authors") or [])),
                            year=str(item.get("pubdate") or "")[:4],
                            venue=item.get("fulljournalname", ""),
                            doi=doi,
                            url=f"https://pubmed.ncbi.nlm.nih.gov/{uid}/",
                            extra=f"pmid:{uid}", volume=item.get('volume', ''),
                            issue=item.get('issue', ''), pages=item.get('pages', '')))
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
        match = _ARXIV_ID_PATTERN.search(raw_id)
        journal_ref = first_text(entry.findtext("arxiv:journal_ref", default="", namespaces=ns))
        doi = first_text(entry.findtext("arxiv:doi", default="", namespaces=ns))
        records.append(_rec("arXiv",
                            title=first_text(entry.findtext("a:title", default="", namespaces=ns)),
                            authors="; ".join(first_text(a.findtext("a:name", default="", namespaces=ns))
                                              for a in entry.findall("a:author", ns)),
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
                      authors="; ".join(str(a.get("name") or "") for a in (payload.get("authors") or [])),
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
                            authors="; ".join(str(a.get("name") or "") for a in (item.get("authors") or [])),
                            year=item.get("year", ""),
                            venue=item.get("venue", ""),
                            doi=ids.get("DOI", ""),
                            arxiv_id=ids.get("ArXiv", "")))
    return 200, records, ""


def _fold(text: str) -> str:
    """Lowercase and strip diacritics so Öztürk and Ozturk compare equal."""
    value = str(text or "").translate(_DASHES).translate(_TRANSLITERATE)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return value.casefold()


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

