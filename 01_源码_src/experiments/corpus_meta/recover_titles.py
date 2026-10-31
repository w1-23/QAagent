"""Recover missing titles for corpus records by reverse lookup.

Why this exists
---------------
The literature corpus was cleaned from Markdown files whose H1 title was
dropped, so for 1137 of 3171 papers ``section_name`` is ``Abstract`` and the
body starts mid-abstract.  ``title`` is then only an internal file key
(``047_paper_3fff7da9_...``).  ``build_metadata_index.py`` can only recover a
title when the text itself still carries one (Markdown heading / first line).

This script reconstructs the *real* title from external registries, in three
progressively weaker phases, and never guesses:

1. ``doi``        – a DOI appears verbatim somewhere in the paper's chunks, so
                    ``api.crossref.org/works/{doi}`` returns the exact record.
2. ``europepmc``  – a distinctive phrase from the abstract is searched as a
                    quoted phrase; Europe PMC indexes abstracts, so a hit means
                    the phrase really occurs in that record's abstract.
3. ``crossref``   – last resort: bibliographic query with journal + year +
                    leading distinctive words.  Accepted only when the title
                    overlaps the paper's own text.

Every accepted record stores its provenance (``title_source``,
``title_confidence``, ``title_evidence``) so a reviewer can audit it.  Raw
registry responses are cached under ``recovery_cache/`` so a rerun is offline
reproducible and byte-comparable.

Usage
-----
    python experiments/corpus_meta/recover_titles.py --data-dir agent/data \
        --limit 20 --dry-run          # pilot, prints candidates only
    python experiments/corpus_meta/recover_titles.py --data-dir agent/data
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
STOPWORDS = set("""
a an the of in on for and or with by from to as at is are was were be been
this that these those we our its their it they them here however thus also
using used use based study results result present presents show shows shown
between among during after before under over into than then there which while
""".split())
USER_AGENT = "CitrusScholar/1.0 (title recovery; mailto:research@example.org)"
CACHE_DIRNAME = "recovery_cache"
REQUEST_TIMEOUT = 25


# --------------------------------------------------------------------------- #
# small utilities
# --------------------------------------------------------------------------- #
def slug_tokens(slug: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", (slug or "").lower()) if t]


def norm_journal(text: str) -> str:
    return " ".join(slug_tokens(text))


def distinctive_phrase(text: str, n_words: int = 12) -> str:
    """Take the first run of long, non-boilerplate words from the abstract."""
    words = re.findall(r"[A-Za-z][A-Za-z\-']+", text or "")
    out: list[str] = []
    for w in words:
        lw = w.lower()
        if lw in STOPWORDS or len(lw) < 4:
            if out:
                break
            continue
        out.append(w)
        if len(out) >= n_words:
            break
    return " ".join(out)


def rare_words(text: str, n: int = 8) -> list[str]:
    """Pick distinctive query terms: long, rare (usually once-occurring) words.

    A quoted-phrase search looks attractive but fails as soon as the cleaning
    step altered punctuation or dropped a stop word.  Individual rare words
    joined with AND are far more forgiving and still selective: a real abstract
    shares most of them, an unrelated paper rarely does.
    """
    counts: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z][A-Za-z\-']{4,}", text or ""):
        lw = w.lower()
        if lw in STOPWORDS:
            continue
        counts[lw] = counts.get(lw, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (kv[1], -len(kv[0])))
    return [w for w, _ in ranked[:n]]


def ngram_overlap(source: str, candidate: str, n: int = 8) -> str:
    """Longest-run evidence: an n-gram of the paper also present in candidate."""
    src = re.findall(r"[A-Za-z']+", (source or "").lower())
    cand = " " + " ".join(re.findall(r"[A-Za-z']+", (candidate or "").lower())) + " "
    for i in range(0, max(0, len(src) - n + 1)):
        gram = " ".join(src[i:i + n])
        if gram in cand:
            return gram
    return ""


def title_overlap(title: str, body: str) -> float:
    """Fraction of the title's content tokens that occur in the paper body."""
    toks = {t for t in slug_tokens(title) if len(t) > 3 and t not in STOPWORDS}
    if not toks:
        return 0.0
    body_l = (body or "").lower()
    hit = sum(1 for t in toks if t in body_l)
    return hit / len(toks)


_TAG_RE = re.compile(r"</?[A-Za-z][^<>]{0,40}>")


def clean_title(text: str) -> str:
    """欧洲 PMC 会返回带 <i>/&lt;i&gt; 标记的题名，这里统一还原成纯文本。"""
    value = html.unescape(str(text or ""))
    value = _TAG_RE.sub("", value)
    value = value.replace("\u00a0", " ")
    return " ".join(value.split()).strip().strip(".")

def cache_path(cache_dir: str, key: str) -> str:
    h = hashlib.sha256(key.encode("utf-8")).hexdigest()[:24]
    return os.path.join(cache_dir, h + ".json")


def http_json(url: str, cache_file: str | None = None) -> Any:
    if cache_file and os.path.exists(cache_file):
        try:
            with open(cache_file, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        payload = json.loads(resp.read().decode("utf-8", "replace"))
    if cache_file:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        tmp = cache_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, cache_file)
    return payload


# --------------------------------------------------------------------------- #
# registry lookups
# --------------------------------------------------------------------------- #
def crossref_by_doi(doi: str, cache_dir: str) -> dict | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")
    try:
        data = http_json(url, cache_path(cache_dir, "doi:" + doi))
    except Exception:
        return None
    msg = (data or {}).get("message") or {}
    title = " ".join(msg.get("title") or []).strip()
    if not title:
        return None
    year = ""
    for field in ("published-print", "published-online", "issued", "created"):
        parts = ((msg.get(field) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = str(parts[0])
            break
    container = " ".join(msg.get("container-title") or [])
    return {
        "title": clean_title(title),
        "year": year,
        "journal": container,
        "doi": doi,
        "source": "crossref_doi",
        "confidence": 0.99,
        "evidence": "doi_in_text",
    }


def europepmc_abstract(words: list[str], cache_dir: str) -> list[dict]:
    """Search Europe PMC by rare abstract words joined with AND."""
    if not words:
        return []
    q = "ABSTRACT:(" + " AND ".join(words) + ")"
    url = ("https://www.ebi.ac.uk/europepmc/webservices/rest/search?query=%s"
           "&format=json&pageSize=5&resultType=core"
           % urllib.parse.quote(q))
    try:
        data = http_json(url, cache_path(cache_dir, "epmc-abs:" + " ".join(words)))
    except Exception:
        return []
    out = []
    for item in ((data or {}).get("resultList") or {}).get("result") or []:
        out.append({
            "title": clean_title(item.get("title") or ""),
            "year": str(item.get("pubYear") or ""),
            "journal": item.get("journalTitle") or "",
            "doi": item.get("doi") or "",
            "pmid": item.get("pmid") or "",
            "abstract": item.get("abstractText") or "",
        })
    return out


def crossref_bibliographic(query: str, year: str, cache_dir: str) -> list[dict]:
    params = {"rows": "5", "query.bibliographic": query,
              "mailto": "research@example.org"}
    if year and year.isdigit():
        params["filter"] = "from-pub-date:%s-01-01,until-pub-date:%s-12-31" % (year, year)
    url = "https://api.crossref.org/works?" + urllib.parse.urlencode(params)
    try:
        data = http_json(url, cache_path(cache_dir, "cr:" + query + ":" + year))
    except Exception:
        return []
    out = []
    for item in ((data or {}).get("message") or {}).get("items") or []:
        year_v = ""
        for field in ("published-print", "published-online", "issued"):
            parts = ((item.get(field) or {}).get("date-parts") or [[None]])[0]
            if parts and parts[0]:
                year_v = str(parts[0])
                break
        out.append({
            "title": clean_title(" ".join(item.get("title") or [])),
            "year": year_v,
            "journal": " ".join(item.get("container-title") or []),
            "doi": item.get("DOI") or "",
        })
    return out


# --------------------------------------------------------------------------- #
# per-record recovery
# --------------------------------------------------------------------------- #
def recover_one(key: str, info: dict, text0: str, all_text: str,
                cache_dir: str) -> dict | None:
    year_hint = str(info.get("year") or "")
    journal_hint = norm_journal(info.get("journal") or "")

    # ---- phase 1: DOI mentioned in the paper body -------------------------
    doi_match = DOI_IN_TEXT.search(all_text or "")
    if doi_match:
        doi = doi_match.group(0).rstrip(".,;)]")
        hit = crossref_by_doi(doi, cache_dir)
        if hit:
            return hit

    # ---- phase 2: rare abstract words in Europe PMC ------------------------
    words = rare_words(text0)
    if len(words) >= 4:
        for cand in europepmc_abstract(words, cache_dir):
            title = cand.get("title") or ""
            if not title or len(title) < 15:
                continue
            gram = ngram_overlap(text0, cand.get("abstract") or "")
            overlap = title_overlap(title, all_text)
            if gram:
                return {
                    "title": clean_title(title),
                    "year": cand.get("year") or year_hint,
                    "journal": cand.get("journal") or info.get("journal") or "",
                    "doi": cand.get("doi") or "",
                    "pmid": cand.get("pmid") or "",
                    "source": "europepmc",
                    "confidence": 0.95,
                    "evidence": "abstract_ngram:%s" % gram[:60],
                }
            if overlap >= 0.6:
                return {
                    "title": clean_title(title),
                    "year": cand.get("year") or year_hint,
                    "journal": cand.get("journal") or info.get("journal") or "",
                    "doi": cand.get("doi") or "",
                    "pmid": cand.get("pmid") or "",
                    "source": "europepmc",
                    "confidence": 0.8,
                    "evidence": "title_overlap=%.2f" % overlap,
                }
    phrase = " ".join(words[:10])

    # ---- phase 3: Crossref bibliographic ----------------------------------
    if len(phrase.split()) >= 5:
        query = " ".join(phrase.split()[:10])
        for cand in crossref_bibliographic(query, year_hint, cache_dir):
            title = cand.get("title") or ""
            if not title or len(title) < 15:
                continue
            overlap = title_overlap(title, all_text)
            journal_ok = (not journal_hint
                          or journal_hint in norm_journal(cand.get("journal") or "")
                          or norm_journal(cand.get("journal") or "").startswith(journal_hint[:12]))
            if overlap >= 0.6 and (journal_ok or not journal_hint):
                return {
                    "title": clean_title(title),
                    "year": cand.get("year") or year_hint,
                    "journal": cand.get("journal") or info.get("journal") or "",
                    "doi": cand.get("doi") or "",
                    "source": "crossref",
                    "confidence": 0.6,
                    "evidence": "bibliographic+overlap=%.2f" % overlap,
                }
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="agent/data")
    ap.add_argument("--corpus", default="cqa_literature")
    ap.add_argument("--limit", type=int, default=0, help="0 = all")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    data_dir = os.path.abspath(args.data_dir)
    chunks = os.path.join(data_dir, args.corpus, "chunks.jsonl")
    meta_path = os.path.join(data_dir, "metadata_index.json")
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), CACHE_DIRNAME)

    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    index = meta.get("index") or {}
    untitled = [k for k, v in index.items() if not (v or {}).get("title")]
    print("untitled records: %d" % len(untitled), flush=True)
    if args.limit:
        untitled = untitled[:args.limit]

    want = set(untitled)
    first: dict[str, str] = {}
    texts: dict[str, list[str]] = {}
    with open(chunks, encoding="utf-8") as f:
        for line in f:
            if '"paper_id"' not in line:
                continue
            row = json.loads(line)
            pid = row.get("paper_id")
            if pid not in want:
                continue
            if str(row.get("chunk_index")) == "0":
                first[pid] = row.get("text") or ""
            texts.setdefault(pid, []).append(row.get("text") or "")
    print("loaded texts for %d records" % len(texts), flush=True)

    def work(key: str):
        info = index.get(key) or {}
        t0 = first.get(key, "")
        all_text = "\n".join(texts.get(key, []))[:60000]
        try:
            hit = recover_one(key, info, t0, all_text, cache_dir)
        except Exception as exc:  # network hiccup must not kill the run
            return (key, None, "%s: %s" % (type(exc).__name__, exc))
        if not args.dry_run:
            time.sleep(0.15)
        return (key, hit, "")

    results: dict[str, dict] = {}
    errors = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for i, (key, hit, err) in enumerate(pool.map(work, untitled), 1):
            if err:
                errors += 1
            elif hit:
                results[key] = hit
            if i % 25 == 0 or i == len(untitled):
                print("  %d/%d  recovered=%d errors=%d"
                      % (i, len(untitled), len(results), errors), flush=True)

    print("\nrecovered %d/%d (errors=%d)" % (len(results), len(untitled), errors))
    by_source: dict[str, int] = {}
    for hit in results.values():
        by_source[hit["source"]] = by_source.get(hit["source"], 0) + 1
    print("by source:", by_source)

    if args.dry_run:
        for key, hit in list(results.items())[:10]:
            print("\n%s\n  -> %s\n     %s | %s | %s | conf=%.2f"
                  % (key, hit["title"], hit["journal"], hit["year"],
                     hit["doi"], hit["confidence"]))
        return 0

    # merge into the sidecar
    for key, hit in results.items():
        entry = dict(index.get(key) or {})
        entry.update({
            "title": hit["title"],
            "year": hit.get("year") or entry.get("year") or "",
            "journal": hit.get("journal") or entry.get("journal") or "",
            "doi": hit.get("doi") or entry.get("doi") or "",
            "title_source": hit["source"],
            "title_confidence": hit["confidence"],
            "title_evidence": hit.get("evidence") or "",
        })
        index[key] = entry
    meta["index"] = index
    meta["title_recovery"] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "attempted": len(untitled),
        "recovered": len(results),
        "errors": errors,
        "by_source": by_source,
    }
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, meta_path)
    titled = sum(1 for v in index.values() if (v or {}).get("title"))
    print("metadata_index.json updated: %d docs, %d titled (%.1f%%)"
          % (len(index), titled, 100.0 * titled / max(1, len(index))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
