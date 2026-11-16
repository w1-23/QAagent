"""One conservative bibliography inventory for uploads and pasted citations.

PyMuPDF/python-docx handle containers. No GROBID installation or OCR is implied.
The existing line parser is reused; its guesses never become certain claims.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import re
import zipfile

from src.research import literature

PARSER_VERSION = 'bibliography-fallback/3'
MAX_BYTES = 20 * 1024 * 1024
MAX_TEXT = 2_000_000
MAX_RECORDS = 5000
_HEADING = re.compile(r'^\s*(?:#{1,6}\s*)?(references|bibliography|literature cited|参考文献)\s*$', re.I)
_END = re.compile(r'^\s*(?:#{1,6}\s*)?(appendix(?:\s+[A-Z0-9]+)?|appendices|supplementary (?:material|information)|acknowledg(?:e)?ments?|附录|致谢)\s*$', re.I)
_ENTRY = re.compile(r'^\s*(?:\[\d{1,5}\]|\d{1,5}[.)、]\s+|[A-Z][^\n]{1,90}\(?(?:19|20)\d{2}[a-z]?\)?)')


def digest(value) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    return hashlib.sha256(raw).hexdigest()


def _parse_claims(text: str) -> tuple[dict, list, dict]:
    """Recognize explicit delimiters, retaining guesses separately from field checks.

    This small grammar covers author (year). title. venue citations. It says
    nothing about whether segmentation covered the source's whole bibliography.
    Other layouts still use the legacy fallback with uncertain fields.
    """
    from src.research.tools import _parse_reference_line
    flat = re.sub(r'\s+', ' ', text).strip()
    doi = next(iter(literature._DOI_PATTERN.findall(flat)), '')
    claims = _parse_reference_line(flat, literature.normalize_doi(doi))
    claims['doi'] = literature.normalize_doi(doi)
    certainty = {key: 'heuristic' for key, value in claims.items() if value}
    if doi:
        certainty['doi'] = 'explicit'
    arxiv = literature._ARXIV_ID_PATTERN.search(flat)
    if arxiv:
        claims['arxiv_id'] = arxiv.group(1)
        certainty['arxiv_id'] = 'explicit'
    clean = re.sub(r'^\s*(?:\[\d{1,5}\]|\d{1,5}[.)、])\s*', '', flat)
    clean = literature._DOI_PATTERN.sub('', clean)
    clean = re.sub(r'(?i)\s*(?:https?://(?:dx\.)?doi\.org/|doi\s*[:：])\s*$', '', clean).strip()
    explicit = re.fullmatch(r'(?P<authors>[^()\d]{2,300})\s*\((?P<year>(?:19|20)\d{2})\)\s*\.\s+(?P<tail>.+)', clean)
    if explicit:
        authors = explicit['authors'].strip(' .;,')
        # Require name-like initials or an explicit multi-author delimiter;
        # ordinary prose before a year is not an author assertion.
        name_like = (re.search(r'\b[A-Z]{1,4}\b', authors)
                     or re.search(r'[,;]|\bet al\b', authors, re.I))
        if name_like:
            claims.update(authors=authors, year=explicit['year'])
            certainty.update(authors='explicit', year='explicit')
            parts = re.split(r'(?<=[.!?])\s+', explicit['tail'].strip())
            if len(parts) == 1:
                claims['title'] = parts[0].rstrip(' .')
                certainty['title'] = 'heuristic'  # May still contain an undelimited venue.
            elif len(parts) >= 2:
                claims['title'] = parts[0].rstrip(' .!?')
                venue = ' '.join(parts[1:]).strip(' .')
                numeric = re.fullmatch(r'(?P<journal>.+?),\s*(?P<volume>\d+)\s*(?:\((?P<issue>[^()]+)\))?\s*,\s*(?P<pages>\d+\s*[-–—]\s*\d+|[eE]?\d+)\.?', venue)
                if numeric:
                    claims.update({key: value for key, value in numeric.groupdict().items() if value})
                    certainty.update({key: 'explicit' for key, value in numeric.groupdict().items() if value})
                    # A single numeric locator can be a page or an article number.
                    if not re.search(r'[-–—]', numeric['pages']):
                        certainty['pages'] = 'heuristic'
                elif venue and not re.search(r'https?://', venue):
                    claims['journal'] = venue
                    # Ordinal conference names (e.g. 9th conference) are venue
                    # text; bare numbers may instead be undelimited locators.
                    certainty['journal'] = ('explicit' if len(parts) == 2 and not re.search(r'\b\d+\b', venue)
                                            else 'heuristic')
                # Extra sentence boundaries can be title/venue abbreviations;
                # retain the strings but do not certify these boundaries.
                certainty['title'] = 'explicit' if len(parts) == 2 else 'heuristic'
    uncertain = [key for key, value in certainty.items() if value == 'heuristic']
    return claims, uncertain, certainty


def parse_reference_claims(text: str) -> dict:
    return _parse_claims(text)[0]


def extract_inventory(*, name='references.txt', content=None, text='', references=None, source_ref=None,
                      _pdf_pages=None, _pdf_page_count=None) -> dict:
    """Retain every entry and its exact input; source_ref is opaque provenance."""
    if references is not None:
        if not isinstance(references, list) or any(not isinstance(item, dict) for item in references):
            raise ValueError('references must be a list of objects')
        if len(references) > MAX_RECORDS:
            raise ValueError('Reference inventory exceeds 5000 entries; split sources explicitly')
        entries = []
        for index, item in enumerate(references):
            original = dict(item)
            claims = dict(item.get('claims') or item)
            for alias, field in (('journal', 'venue'), ('article#', 'article_number'), ('page', 'pages')):
                if claims.get(alias) and not claims.get(field):
                    claims[field] = claims[alias]
            citation = str(item.get('citation') or item.get('text') or json.dumps(original, ensure_ascii=False))
            entries.append({'citation': citation, 'claims': claims, 'original': original,
                            'positions': item.get('positions') or [{'record_index': index}],
                            'parse_status': 'supplied', 'uncertain_fields': []})
        source_hash = digest(references)
        coverage = {'status': 'complete', 'scope': 'supplied_records', 'limitations': [], 'records': len(entries)}
    else:
        content = content if content is not None else str(text).encode('utf-8')
        if not isinstance(content, bytes) or len(content) > MAX_BYTES:
            raise ValueError('Source must be bytes, at most 20 MB')
        source_hash = digest(content)
        suffix = Path(name).suffix.lower()
        units, limitations, blank_pages = [], [], []
        if suffix == '.pdf' or content.startswith(b'%PDF'):
            if _pdf_pages is None:
                import fitz
                with fitz.open(stream=content, filetype='pdf') as doc:
                    page_count = len(doc)
                    pages = [doc.load_page(i).get_text('text') for i in range(min(page_count, 300))]
            else:
                pages, page_count = _pdf_pages, _pdf_page_count
            for page, value in enumerate(pages):
                if not value.strip():
                    blank_pages.append(page + 1)
                units.append((value, {'page': page + 1}))
            if page_count > len(pages):
                limitations.append('pdf_page_limit:300')
            if blank_pages:
                limitations.append('pages_without_text:no_OCR_performed')
        elif suffix == '.docx':
            from docx import Document
            # Bound inflated XML before python-docx opens the package.
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                if sum(item.file_size for item in archive.infolist()) > 80 * 1024 * 1024:
                    raise ValueError('DOCX expanded content exceeds limit')
            document = Document(io.BytesIO(content))
            units = [(p.text, {'paragraph': i + 1}) for i, p in enumerate(document.paragraphs)]
            if document.tables:
                limitations.append('docx_tables_not_in_paragraph_inventory')
        elif suffix in {'.txt', '.md', '.csv', '.tsv', ''}:
            try:
                value = content.decode('utf-8-sig')
            except UnicodeDecodeError:
                value = content.decode('utf-8', errors='replace')
                limitations.append('invalid_utf8_replaced')
            units = [(value, {})]
        else:
            raise ValueError('Supported reference sources: DOCX/PDF/TXT/MD/pasted text')
        lines, offset = [], 0
        for value, location in units:
            for line_no, line in enumerate(value.splitlines(keepends=True)):
                if offset + len(line) > MAX_TEXT:
                    limitations.append('text_limit:2000000')
                    break
                lines.append((line, {**location, 'line': line_no + 1, 'text_start': offset, 'text_end': offset + len(line)}))
                offset += len(line)
            if offset >= MAX_TEXT or 'text_limit:2000000' in limitations:
                break
        heading = next((i for i, (line, _) in enumerate(lines) if _HEADING.match(line)), None)
        selected = lines[heading + 1:] if heading is not None else lines
        if heading is None:
            limitations.append('bibliography_heading_not_found:input_may_include_body_text')
        entries, pending = [], []

        def flush():
            if not pending:
                return
            raw = ''.join(line for line, _ in pending)
            if raw.strip():
                claims, uncertain, certainty = _parse_claims(raw)
                entries.append({'citation': raw, 'claims': claims,
                                'positions': [pos for _, pos in pending],
                                'parse_status': 'heuristic' if uncertain else 'parsed',
                                'field_certainty': certainty, 'uncertain_fields': uncertain})
            pending.clear()

        for line, location in selected:
            if heading is not None and _END.match(line):
                break
            if _ENTRY.match(line) and pending:
                flush()
            # Blank lines separate unnumbered pasted entries; numbered wraps
            # otherwise remain together. Every nonempty fragment is retained.
            if not line.strip():
                flush()
            else:
                pending.append((line, location))
            if len(entries) >= MAX_RECORDS:
                limitations.append('record_limit:5000')
                pending.clear()
                break
        if len(entries) < MAX_RECORDS:
            flush()
        limitations.append('heuristic_bibliography_segmentation_and_fields:manual_review_required')
        coverage = {'status': 'partial', 'scope': 'bibliography_section' if heading is not None else 'unscoped_text',
                    'heading_found': heading is not None, 'records': len(entries),
                    'text_characters': offset, 'pages_without_text': blank_pages,
                    'limitations': list(dict.fromkeys(limitations)), 'ocr_performed': False,
                    'grobid': 'not_configured'}
    for index, entry in enumerate(entries):
        entry.update(record_index=index, record_id=digest([source_hash, index, entry['citation']]),
                     claims_sha256=digest(entry['claims']), source_sha256=source_hash,
                     parser_version=PARSER_VERSION)
    return {'source': {'name': Path(name).name, 'sha256': source_hash,
                       'version': source_hash, 'ref': source_ref or {}},
            'parser_version': PARSER_VERSION, 'extraction': coverage, 'entries': entries}
