"""Offline P7 contracts using synthetic citations and temporary databases only.

No provider, model, real account or scientific data is used. The implementation
phase writes these tests but leaves execution to the unified regression phase.
"""
from contextlib import contextmanager
from copy import deepcopy
import io
import json
import socket
import time

import httpx
import pytest

from src.research import reference_audit as audit
from src.research import reference_extraction as extraction
from src.research import literature as lit
from src.core import account_leases, tracing

OWNER = 991881
SESSION = 'reference_audit_session'
TITLE = 'Synthetic bibliography contract example'
CLAIMS = {'doi': '10.1000/synthetic-p7', 'title': TITLE, 'authors': 'Alice Smith', 'year': '2020',
          'journal': 'Synthetic Journal', 'volume': '2', 'issue': '3', 'pages': '10-19', 'article_number': 'e12'}


def candidate(**changes):
    value = lit._rec('Crossref', title=TITLE, doi=CLAIMS['doi'], authors='Alice Smith', year='2020',
                     venue='Synthetic Journal', volume='2', issue='3', pages='10-19', article_number='e12')
    value.update(changes)
    return value


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from src import auth, config
    from src.session import manager
    monkeypatch.setattr(config, 'PROJECT_ROOT', tmp_path)
    monkeypatch.setattr(auth, 'user_by_id', lambda owner: {'id': owner, 'active': True})
    monkeypatch.setattr(account_leases, '_states', {})
    monkeypatch.setattr(manager.session_manager, 'get_session_owner',
                        lambda sid: OWNER if sid in {SESSION, 'another_session'} else OWNER + 1)
    monkeypatch.setattr(audit, '_CACHE', {})
    monkeypatch.setattr(lit, '_last_call', {})
    monkeypatch.setattr(tracing, 'get_job_id', lambda: '')
    old_network = tracing.web_search_enabled()
    tracing.set_web_search_enabled(True)
    lease_token = account_leases.set_current_account_lease(None)

    def forbidden(*args, **kwargs):
        raise AssertionError('No live network in reference audit tests')

    monkeypatch.setattr(httpx, 'get', forbidden)
    monkeypatch.setattr(httpx, 'stream', forbidden)
    monkeypatch.setattr(socket.socket, 'connect', forbidden)
    # Every provider defaults to an explicit empty checked result.
    for name in ('_crossref_work', '_openalex_work', '_datacite_work', '_s2_doi',
                 '_crossref_search', '_openalex_search', '_epmc_search', '_pubmed_search',
                 '_arxiv_by_id', '_datacite_arxiv'):
        monkeypatch.setattr(lit, name, lambda *args, **kwargs: (200, [], ''))
    yield
    account_leases.reset_current_account_lease(lease_token)
    tracing.set_web_search_enabled(old_network)


def fake_verified(claims, *, check=None, debit=None, **kwargs):
    if check:
        check()
    if debit:
        debit()
    return {'status': 'metadata_matched', 'existence': 'record_found', 'identity': 'identifier_match',
            'fields': audit.compare_fields(claims, candidate()), 'rule_version': audit.RULE_VERSION,
            'claims_sha256': extraction.digest(claims), 'retryable': False, 'candidates': [candidate()],
            'evidence': [{'provider': 'synthetic', 'status': 'returned'}]}


def test_151_entries_across_batches_cancel_resume_and_all_exports(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    references = [dict(CLAIMS, citation=f'Synthetic original entry {i}') for i in range(151)]
    created = audit.create_audit(OWNER, SESSION, references=references)
    aid = created['audit_id']
    assert created['coverage']['total'] == 151
    assert len({row['record_id'] for row in audit.verification_rows(OWNER, SESSION, aid)}) == 151
    first = audit.run_audit(OWNER, SESSION, aid, max_rows=100)
    assert first['coverage']['processed'] == 100
    audit.cancel_audit(OWNER, SESSION, aid)
    assert audit.run_audit(OWNER, SESSION, aid)['run_reason'] == 'cancelled_use_resume'
    resumed = audit.run_audit(OWNER, SESSION, aid, max_rows=100, resume=True)
    assert resumed['coverage']['processed'] == 151
    assert resumed['coverage']['pending'] == 0
    assert resumed['budget']['requests_used'] == 151
    payload, _, _ = audit.export_audit(OWNER, SESSION, aid)
    rows = json.loads(payload)['rows']
    assert len(rows) == 151
    assert all(row['attempts'] == 1 for row in rows)
    from openpyxl import load_workbook
    workbook, _, _ = audit.export_audit(OWNER, SESSION, aid, format='xlsx')
    wb = load_workbook(io.BytesIO(workbook))
    assert wb['References'].max_row == 152
    assert wb['Evidence'].max_row > 151
    for format, marker in (('ris', b'TY  - GEN'), ('bibtex', b'@misc{')):
        exported, _, _ = audit.export_audit(OWNER, SESSION, aid, format=format)
        assert exported.count(marker) == 151
        assert b'full evidence in JSON/XLSX' in exported


def test_progress_is_durable_and_cancel_preserves_completed_row(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS, CLAIMS])['audit_id']
    seen = []

    def progress(event):
        seen.append(event)
        assert audit.get_audit(OWNER, SESSION, aid)['coverage']['processed'] == 1
        audit.cancel_audit(OWNER, SESSION, aid)

    result = audit.run_audit(OWNER, SESSION, aid, progress=progress)
    assert len(seen) == 1
    assert result['status'] == 'cancelled'
    assert result['coverage']['pending'] == 1
    assert result['budget']['requests_used'] == 1
    result = audit.run_audit(OWNER, SESSION, aid, resume=True)
    assert result['budget']['requests_used'] == 2
    assert result['coverage']['processed'] == 2


def test_expired_runner_reclaims_without_resetting_budget(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS, CLAIMS], request_budget=2)['audit_id']
    audit.run_audit(OWNER, SESSION, aid, max_rows=1)
    with audit._connect() as conn:
        conn.execute("UPDATE reference_audits SET lease_token='dead-process',lease_until=?,status='running' WHERE id=?",
                     (time.time() - 1, aid))
    result = audit.run_audit(OWNER, SESSION, aid, resume=True)
    assert result['budget']['requests_used'] == 2
    assert result['coverage']['processed'] == 2


def test_budget_cannot_be_refilled_by_resume(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS, CLAIMS], request_budget=1)['audit_id']
    result = audit.run_audit(OWNER, SESSION, aid)
    assert result['run_reason'] == 'budget_exhausted'
    assert result['coverage']['processed'] == 1
    result = audit.run_audit(OWNER, SESSION, aid, resume=True)
    assert result['budget']['requests_used'] == 1
    assert result['coverage']['pending'] == 1


def test_owner_session_and_explicit_export_identity():
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS])['audit_id']
    with pytest.raises(PermissionError):
        audit.get_audit(OWNER + 1, SESSION, aid)
    with pytest.raises(LookupError):
        audit.get_audit(OWNER, 'another_session', aid)
    with pytest.raises(ValueError):
        audit.export_audit(OWNER, SESSION, '')
    other = audit.create_audit(OWNER, SESSION, references=[CLAIMS, CLAIMS])
    assert other['audit_id'] != aid
    assert len(json.loads(audit.export_audit(OWNER, SESSION, aid)[0])['rows']) == 1


def test_wrong_doi_independently_searches_title_and_keeps_provider_evidence(monkeypatch):
    calls = []
    wrong = candidate(title='An unrelated synthetic article', doi=CLAIMS['doi'])
    correct = candidate(doi='10.1000/correct-synthetic')
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (200, [wrong], ''))

    def search(title):
        calls.append(title)
        return 200, [correct], ''

    monkeypatch.setattr(lit, '_crossref_search', search)
    result = audit.verify_record(CLAIMS)
    assert calls == [TITLE]
    assert result['fields']['doi']['status'] == 'matched'
    assert result['fields']['title']['status'] == 'conflicting'
    assert result['identity'] == 'identifier_match'
    assert result['selection_basis'] == 'exact_identifier'
    assert result['selected_record']['title'] == wrong['title']
    assert result['title_candidates'][0]['doi'] == correct['doi']
    assert len(result['identifier_records']) == 1
    assert len(result['candidates']) == 2
    assert result['status'] == 'metadata_mismatch'


def test_cached_provider_metadata_recompares_claims_and_network_gate(monkeypatch):
    calls = []

    def lookup(doi):
        calls.append(doi)
        return 200, [candidate()], ''

    monkeypatch.setattr(lit, '_crossref_work', lookup)
    assert audit.verify_record(CLAIMS)['fields']['volume']['status'] == 'matched'
    changed = audit.verify_record(dict(CLAIMS, volume='999'))
    assert changed['fields']['volume']['status'] == 'conflicting'
    assert len(calls) == 1
    tracing.set_web_search_enabled(False)
    blocked = audit.verify_record(CLAIMS)
    assert blocked['status'] == 'not_checked'
    assert blocked['evidence'] == []
    assert len(calls) == 1


def test_no_doi_title_only_remains_candidate(monkeypatch):
    monkeypatch.setattr(lit, '_crossref_search', lambda title: (200, [candidate()], ''))
    result = audit.verify_record({'title': TITLE})
    assert result['identity'] == 'candidate'
    assert result['existence'] == 'candidate_records_found'
    assert result['fields']['doi']['status'] == 'not_checked'
    assert result['status'] == 'title_candidate'


def test_all_field_states_and_publication_year_variants():
    record = candidate(years=[{'kind': 'published-online', 'year': '2019'}], issue='')
    fields = audit.compare_fields(dict(CLAIMS, year='2019', volume='9', journal='Synth. J.'), record)
    assert fields['year']['status'] == 'matched'
    assert fields['volume']['status'] == 'conflicting'
    assert fields['issue']['status'] == 'missing'
    assert fields['journal']['status'] == 'uncertain'
    assert fields['article_number']['status'] == 'matched'
    assert audit.compare_fields({'year': '2019'}, candidate())['year']['status'] == 'uncertain'
    assert audit.compare_fields(CLAIMS, record, uncertain_fields=['title'])['title']['status'] == 'uncertain'


def test_provider_conflicts_are_not_hidden_by_priority(monkeypatch):
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (200, [candidate()], ''))
    monkeypatch.setattr(lit, '_openalex_work', lambda doi: (200, [candidate(source='OpenAlex', volume='9')], ''))
    result = audit.verify_record(CLAIMS)
    assert result['status'] == 'metadata_uncertain'
    assert result['fields']['volume']['status'] == 'uncertain'
    assert result['provider_conflicts'][0]['fields'] == ['volume']


def test_not_found_and_network_error_never_mean_fabricated(monkeypatch):
    missing = audit.verify_record(CLAIMS)
    assert missing['status'] == 'not_found_in_checked_registries'
    assert missing['existence'] == 'not_established'
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (429, [], 'http_429'))
    failed = audit.verify_record(CLAIMS)
    assert failed['status'] == 'network_error'
    assert failed['retryable'] is True
    assert 'http_429' in failed['errors'].values()
    assert 'fabricated' not in json.dumps(failed)


def test_duplicate_multiline_no_doi_inventory_has_positions_and_partial_coverage():
    text = ('Body DOI 10.1000/body\nReferences\n'
            '[1] Smith A. (2020). A synthetic title\ncontinued title. doi:10.1000/one\n'
            '[2] Smith A. (2021). Different title. doi:10.1000/one\n'
            '[3] Li X. (2019). A reference without identifier.\nAppendix\nNot a reference\n')
    result = extraction.extract_inventory(text=text)
    assert len(result['entries']) == 3
    assert len({item['record_id'] for item in result['entries']}) == 3
    assert result['entries'][0]['claims']['doi'] == result['entries'][1]['claims']['doi']
    assert result['entries'][2]['claims']['doi'] == ''
    assert 'continued title' in result['entries'][0]['citation']
    assert result['entries'][0]['positions'][0]['line'] == 3
    assert result['extraction']['status'] == 'partial'
    assert result['extraction']['heading_found'] is True


def test_missing_heading_unicode_and_unparseable_input_stay_visible():
    result = extraction.extract_inventory(text='An ambiguous fragment without year or DOI\n')
    assert len(result['entries']) == 1
    assert result['extraction']['status'] == 'partial'
    assert result['extraction']['heading_found'] is False
    assert result['entries'][0]['parse_status'] == 'heuristic'
    assert lit.norm_title('Исследование примера')
    assert lit.title_similarity('Исследование примера', 'Исследование примера') == 1


def test_docx_and_pdf_use_same_inventory_with_source_coordinates():
    from docx import Document
    doc = Document()
    doc.add_paragraph('References')
    doc.add_paragraph('[1] Smith A. (2020). Synthetic title without DOI.')
    out = io.BytesIO()
    doc.save(out)
    inventory = extraction.extract_inventory(name='sample.docx', content=out.getvalue())
    assert inventory['entries'][0]['positions'][0]['paragraph'] == 2
    assert inventory['extraction']['status'] == 'partial'
    import fitz
    with fitz.open() as pdf:
        page = pdf.new_page()
        page.insert_text((50, 50), 'References\n[1] Smith A. (2020). Synthetic title without DOI.')
        pdf.new_page()  # no OCR is claimed for the empty page
        content = pdf.tobytes()
    inventory = extraction.extract_inventory(name='sample.pdf', content=content)
    assert inventory['entries'][0]['positions'][0]['page'] == 1
    assert inventory['extraction']['pages_without_text'] == [2]
    assert inventory['extraction']['ocr_performed'] is False


def test_http_evidence_error_status_hash_and_budget_charge(monkeypatch):
    charges = []

    @contextmanager
    def stream(*args, **kwargs):
        assert kwargs['follow_redirects'] is False
        assert kwargs['timeout'] <= 3
        yield httpx.Response(200, json={'message': {'items': []}}, request=httpx.Request('GET', args[1]))

    monkeypatch.setattr(httpx, 'stream', stream)
    with lit.bounded_lookup(deadline=time.monotonic() + 2, debit=lambda: charges.append(1)) as context:
        status, data, error = lit._get('https://api.crossref.org/works', source='crossref')
    assert status == 200 and not error
    assert charges == [1]
    assert len(context.evidence[0]['response_sha256']) == 64
    assert context.evidence[0]['status'] == 'returned'

    @contextmanager
    def bad(*args, **kwargs):
        yield httpx.Response(400, request=httpx.Request('GET', args[1]))

    monkeypatch.setattr(httpx, 'stream', bad)
    with lit.bounded_lookup(deadline=time.monotonic() + 2) as context:
        status, _, error = lit._get('https://api.crossref.org/works', source='crossref')
    assert status == 400 and error == 'http_400'
    assert context.evidence[0]['status'] == 'error'


def test_http_deadline_does_not_dispatch(monkeypatch):
    with lit.bounded_lookup(deadline=time.monotonic() - 1) as context:
        status, _, error = lit._get('https://api.crossref.org/works')
    assert status == 0 and error == 'deadline'
    assert context.evidence[0]['status'] == 'deadline'


def test_export_xlsx_guards_formula_and_keeps_long_original():
    original = '=DANGEROUS()' + 'x' * 33000
    aid = audit.create_audit(OWNER, SESSION, references=[dict(CLAIMS, citation=original)])['audit_id']
    from openpyxl import load_workbook
    content = audit.export_audit(OWNER, SESSION, aid, format='xlsx')[0]
    wb = load_workbook(io.BytesIO(content))
    assert wb['References']['C2'].data_type != 'f'
    chunks = [row for row in wb['Evidence'].iter_rows(min_row=2, values_only=True) if row[1] == 'original_entry']
    recovered = json.loads(''.join(row[4] for row in sorted(chunks, key=lambda row: row[3])))
    assert recovered['citation'] == original


def test_retry_keeps_previous_error_evidence_and_rule_change_rechecks(monkeypatch):
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS])['audit_id']

    def failure(claims, **kwargs):
        result = fake_verified(claims, **kwargs)
        result.update(status='network_error', retryable=True,
                      errors={'Crossref': 'http_429'}, evidence=[{'provider': 'Crossref', 'error': 'http_429'}])
        return result

    monkeypatch.setattr(audit, 'verify_record', failure)
    first = audit.run_audit(OWNER, SESSION, aid)
    assert first['coverage']['retryable'] == 1
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    second = audit.run_audit(OWNER, SESSION, aid, resume=True)
    row = second['rows'][0]
    assert row['status'] == 'metadata_matched'
    assert len(row['attempt_history']) == 2
    assert row['attempt_history'][0]['result']['errors'] == {'Crossref': 'http_429'}
    assert row['attempts'] == 2
    monkeypatch.setattr(audit, 'RULE_VERSION', 'synthetic-next-rule')
    third = audit.run_audit(OWNER, SESSION, aid)
    assert third['rows'][0]['attempts'] == 3
    assert third['budget']['requests_used'] == 3


def test_ambiguous_no_doi_candidates_do_not_claim_unique_identity(monkeypatch):
    monkeypatch.setattr(lit, '_crossref_search',
                        lambda title: (200, [candidate(), candidate(doi='10.1000/second-record')], ''))
    result = audit.verify_record({key: value for key, value in CLAIMS.items() if key != 'doi'})
    assert result['identity'] == 'ambiguous_candidates'
    assert result['status'] == 'metadata_uncertain'


def test_invalid_json_is_a_provider_failure_not_not_found(monkeypatch):
    @contextmanager
    def stream(*args, **kwargs):
        yield httpx.Response(200, content=b'not json', request=httpx.Request('GET', args[1]))

    monkeypatch.setattr(httpx, 'stream', stream)
    with lit.bounded_lookup(deadline=time.monotonic() + 2) as context:
        status, _, error = lit._get('https://api.crossref.org/works')
    assert status == 0 and error == 'invalid_response'
    assert context.evidence[0]['status'] == 'error'
    assert context.evidence[0]['response_sha256']


def test_exact_book_doi_wins_over_more_similar_review(monkeypatch):
    book = candidate(title=TITLE + ': Second edition', year='2016')
    review = candidate(doi='10.1000/book-review', year='2010')
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (200, [book], ''))
    monkeypatch.setattr(lit, '_crossref_search', lambda title: (200, [review], ''))
    result = audit.verify_record(dict(CLAIMS, year='2016'))
    assert result['selected_record']['doi'] == CLAIMS['doi']
    assert result['identity'] == 'identifier_match'
    assert result['fields']['doi']['status'] == result['fields']['year']['status'] == 'matched'
    assert result['title_candidates'][0]['doi'] == review['doi']


def test_durable_exports_keep_identifier_record_and_separate_title_candidate(monkeypatch):
    exact = candidate(title='The record named by the DOI', year='2020')
    alternative = candidate(doi='10.1000/independent-title')
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (200, [exact], ''))
    monkeypatch.setattr(lit, '_crossref_search', lambda title: (200, [alternative], ''))
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS])['audit_id']
    saved = audit.run_audit(OWNER, SESSION, aid)
    assert saved['rows'][0]['result']['identity'] == 'identifier_match'
    assert saved['rows'][0]['result']['fields']['title']['status'] == 'conflicting'
    exported = json.loads(audit.export_audit(OWNER, SESSION, aid)[0])
    result = exported['rows'][0]['result']
    assert result['selected_record']['doi'] == CLAIMS['doi']
    assert result['title_candidates'][0]['doi'] == alternative['doi']
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(audit.export_audit(OWNER, SESSION, aid, format='xlsx')[0]))
    chunks = [row for row in wb['Evidence'].iter_rows(min_row=2, values_only=True) if row[1] == 'full_result']
    recovered = json.loads(''.join(row[4] for row in sorted(chunks, key=lambda row: row[3])))
    assert recovered == result
    assert wb['References']['F2'].value == 'identifier_match'


def test_unresolved_wrong_doi_keeps_independent_title_candidate(monkeypatch):
    alternative = candidate(doi='10.1000/alternative')
    monkeypatch.setattr(lit, '_crossref_search', lambda title: (200, [alternative], ''))
    result = audit.verify_record(CLAIMS)
    assert result['identifier_records'] == []
    assert result['identity'] == 'identifier_conflict_candidate'
    assert result['fields']['doi']['status'] == 'conflicting'
    assert result['title_candidates'][0]['doi'] == alternative['doi']


def test_clear_numbered_citations_keep_explicit_fields_without_claiming_source_completeness(monkeypatch):
    text = ('[1] Smith JD. (2018). Tiny title. Nature. DOI: 10.1000/synthetic-p7\n\n'
            '[2] Smith JD. (2018). Tiny title. Nature. DOI: 10.1000/synthetic-p7\n\n'
            '[3] Smith JD. (2018). Tiny title. Synthetic Journal, 12(3), 2825–2830.\n')
    inventory = extraction.extract_inventory(text=text)
    entries = inventory['entries']
    assert len(entries) == len({row['record_id'] for row in entries}) == 3
    assert entries[0]['citation'] == text.split('\n\n')[0] + '\n'
    assert entries[0]['positions'][0]['line'] == 1
    assert entries[2]['positions'][0]['line'] == 5
    claims = entries[0]['claims']
    assert claims['authors'] == 'Smith JD' and claims['year'] == '2018'
    assert claims['title'] == 'Tiny title' and claims['journal'] == 'Nature'
    assert entries[0]['uncertain_fields'] == []
    assert entries[0]['field_certainty']['journal'] == 'explicit'
    assert inventory['extraction']['status'] == 'partial'
    third = entries[2]['claims']
    assert (third['journal'], third['volume'], third['issue'], third['pages'], third['doi']) == (
        'Synthetic Journal', '12', '3', '2825–2830', '')
    registered = candidate(title='Tiny title', authors='John Hunter', year='2020', venue='Synthetic Journal')
    fields = audit.compare_fields(claims, registered, uncertain_fields=entries[0]['uncertain_fields'])
    assert {fields[key]['status'] for key in ('authors', 'year', 'journal')} == {'conflicting'}


def test_numbered_venue_names_survive_but_ambiguous_locators_need_review():
    conference = extraction.extract_inventory(text='[1] Smith A. (2020). Tiny title. Proceedings of the 9th Synthetic Conference.\n')['entries'][0]
    assert conference['claims']['journal'] == 'Proceedings of the 9th Synthetic Conference'
    assert conference['field_certainty']['journal'] == 'explicit'
    ambiguous = extraction.extract_inventory(text='[1] Smith A. (2020). Tiny title. Synthetic Journal, 12, 115.\n')['entries'][0]
    assert ambiguous['claims']['pages'] == '115'
    assert 'pages' in ambiguous['uncertain_fields']
    assert not ambiguous['claims'].get('article_number')


def test_ambiguous_field_parse_keeps_comparison_and_input_uncertainty_separate():
    entry = extraction.extract_inventory(text='[1] Smith A. (2020). Dr. Example title. Synth. J.\n')['entries'][0]
    assert entry['parse_status'] == 'heuristic'
    assert 'title' in entry['uncertain_fields']
    fields = audit.compare_fields(entry['claims'], candidate(), uncertain_fields=entry['uncertain_fields'])
    assert fields['title']['status'] == 'uncertain'
    assert fields['title']['input_certainty'] == 'uncertain'
    assert fields['title']['comparison_status'] == 'conflicting'


@pytest.mark.parametrize('left,right,state', [
    ('Smith A et al.', 'Alice Smith; Bob Jones; Carol Brown', 'uncertain'),
    ('Smith A et al.', 'Alice Smith; Alan Smith', 'uncertain'),
    ('Smith A et al.', 'Robert Jones; Alice Smith', 'conflicting'),
    ('Smith JD', 'John David Smith', 'uncertain'),
    ('Smith X', 'John Smith', 'conflicting'),
    ('Li X', 'Li Y', 'conflicting'),
    ('Smith JD', 'Hunter JD', 'conflicting'),
    ('Alice Smith', 'Alice Smith', 'matched'),
])
def test_author_abbreviations_and_obvious_conflicts(left, right, state):
    assert audit.compare_fields({'authors': left}, {'authors': right})['authors']['status'] == state


@pytest.mark.parametrize('left,right,state', [
    ('Nucleic Acids Res.', 'Nucleic Acids Research', 'uncertain'),
    ('J. Mach. Learn. Res.', 'Journal of Machine Learning Research', 'uncertain'),
    ('JMLR', 'Journal of Machine Learning Research', 'uncertain'),
    ('Nature', 'Journal of the Royal Statistical Society Series B', 'conflicting'),
    ('Nature Methods', 'Nature Protocols', 'conflicting'),
    ('Clinical Chemistry', 'Clinical Chemistry', 'matched'),
    ('Cell', 'Nature', 'conflicting'),
])
def test_journal_abbreviations_and_obvious_conflicts(left, right, state):
    assert audit.compare_fields({'journal': left}, {'journal': right})['journal']['status'] == state


def test_only_publication_years_document_online_print_difference():
    fields = audit.compare_fields({'year': '2018'}, candidate(year='2020', years=[{'kind': 'created', 'year': '2018'}]))
    assert fields['year']['status'] == 'conflicting'
    assert audit.compare_fields({'year': '2019'}, candidate())['year']['status'] == 'uncertain'
    assert audit.compare_fields({'year': '2019'}, candidate(years=[{'kind': 'published-online', 'year': 2019}]))['year']['status'] == 'matched'


def test_provider_format_variants_do_not_create_false_conflicts(monkeypatch):
    monkeypatch.setattr(lit, '_crossref_work', lambda doi: (200, [candidate()], ''))
    monkeypatch.setattr(lit, '_openalex_work', lambda doi: (200, [candidate(
        authors='Smith A', venue='Synth. J.', year='2019',
        years=[{'kind': 'published-print', 'year': '2020'}])], ''))
    result = audit.verify_record(CLAIMS)
    assert result['provider_conflicts'] == []
    assert result['fields']['title']['status'] == 'matched'


def test_all_attempted_source_failures_recommend_delivery_without_hiding_coverage(monkeypatch):
    def failed_source(claims, **kwargs):
        return dict(fake_verified(claims, **kwargs), retryable=True, errors={'registry:doi': 'http_429'})

    monkeypatch.setattr(audit, 'verify_record', failed_source)
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS, CLAIMS], request_budget=3)['audit_id']
    first = audit.run_audit(OWNER, SESSION, aid)
    assert first['coverage']['processed'] == 2
    assert first['coverage']['pending'] == 0
    assert first['coverage']['retryable'] == 2
    assert first['coverage']['complete'] is first['coverage']['processing_complete'] is False
    assert first['status'] == 'partial' and first['terminal'] is False
    assert first['next_action'] == 'export_and_review'
    assert first['export_ready'] is first['retry_optional'] is first['can_resume'] is True
    assert first['_tool_outcome']['status'] == 'partial'
    unchanged = audit.run_audit(OWNER, SESSION, aid)
    assert unchanged['processed_indices'] == []
    assert unchanged['budget'] == first['budget']
    assert unchanged['rows'] == first['rows']
    retried = audit.run_audit(OWNER, SESSION, aid, resume=True)
    assert retried['run_reason'] == 'budget_exhausted'
    assert retried['budget']['requests_used'] == 3
    assert [row['attempts'] for row in retried['rows']] == [2, 1]
    assert all(row['result']['errors'] == {'registry:doi': 'http_429'} for row in retried['rows'])
    exported = json.loads(audit.export_audit(OWNER, SESSION, aid)[0])
    assert exported['rows'] == retried['rows']
    assert exported['coverage'] == retried['coverage']


def test_stale_verification_still_requires_run_instead_of_optional_retry(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    aid = audit.create_audit(OWNER, SESSION, references=[CLAIMS])['audit_id']
    audit.run_audit(OWNER, SESSION, aid)
    monkeypatch.setattr(audit, 'RULE_VERSION', audit.RULE_VERSION + '-changed')
    stale = audit.get_audit(OWNER, SESSION, aid)
    assert stale['retry_optional'] is False
    assert stale['next_action'] == 'resume'
    updated = audit.run_audit(OWNER, SESSION, aid)
    assert updated['processed_indices'] == [0]
    assert updated['rows'][0]['attempts'] == 2
    assert updated['terminal'] is True


def test_twenty_attempted_partial_parse_is_terminal_and_full_exports_remain_available(monkeypatch):
    monkeypatch.setattr(audit, 'verify_record', fake_verified)
    text = '\n\n'.join(f'[{i}] Smith A. (2020). A clear title. Synthetic Journal. DOI: 10.1000/synthetic-p7'
                       for i in range(1, 20)) + '\n\n[20] Ambiguous fragment without an identifier.\n'
    created = audit.create_audit(OWNER, SESSION, text=text)
    aid = created['audit_id']
    result = audit.run_audit(OWNER, SESSION, aid)
    assert result['coverage']['processed'] == result['coverage']['total'] == 20
    assert result['coverage']['pending'] == result['coverage']['retryable'] == 0
    assert result['coverage']['complete'] is False
    assert result['coverage']['processing_complete'] is True
    assert result['terminal'] is True and result['can_resume'] is False
    assert result['status'] == 'completed_with_review'
    assert result['next_action'] == 'export_and_review'
    assert result['_tool_outcome']['status'] == 'complete'
    again = audit.run_audit(OWNER, SESSION, aid, resume=True)
    assert again['run_reason'] == 'already_completed'
    assert again['budget'] == result['budget']
    assert [row['attempts'] for row in again['rows']] == [1] * 20
    other = audit.create_audit(OWNER, SESSION, references=[CLAIMS])
    assert other['audit_id'] != aid
    exported = json.loads(audit.export_audit(OWNER, SESSION, aid)[0])
    assert exported['status'] == 'completed_with_review'
    assert len(exported['rows']) == 20
    assert exported['rows'][-1]['claims']['doi'] == ''
    assert exported['rows'][0]['positions'][0]['line'] == 1
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(audit.export_audit(OWNER, SESSION, aid, format='xlsx')[0]))
    assert wb['References'].max_row == 21
    metadata = [row for row in wb['Evidence'].iter_rows(min_row=2, values_only=True) if row[1] == 'audit_metadata']
    assert json.loads(''.join(row[4] for row in sorted(metadata, key=lambda row: row[3])))['terminal'] is True


def test_legacy_arxiv_ids_are_retained_without_doi():
    match = lit._ARXIV_ID_PATTERN.search('https://arxiv.org/abs/hep-th/9901001v2')
    assert match and match.group(1) == 'hep-th/9901001'
