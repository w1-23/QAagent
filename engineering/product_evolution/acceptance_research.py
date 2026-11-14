"""Real tool/publisher fixtures for an isolated app; no model or scientific claim."""
from __future__ import annotations
import json


def publish_research_fixtures(owner: int, session_id: str) -> dict:
    from src.session import files
    from src.research.context import set_request_tables, reset_request_tables
    from src.research.statistics_tools import analyze_research_data
    from src.research.tools import audit_uploaded_data, export_table
    from src.research.figure_tools import create_research_figure
    from src.research.word_tools import word_document

    data = b'group,value,duplicate\na,1,1\na,2,2\na,4,4\nb,3,3\nb,5,5\nb,8,8\n'
    info = files.register_file(session_id=session_id, owner_user_id=owner,
                              filename='SYNTHETIC_engineering_measurements.csv', content=data, mime='text/csv')
    other = files.register_file(session_id=session_id, owner_user_id=owner,
                               filename='SYNTHETIC_reused_control.csv', content=data, mime='text/csv')
    token = set_request_tables([{'name': info['filename'], 'data': data, 'file_id': info['file_id']},
                                {'name': other['filename'], 'data': data, 'file_id': other['file_id']}])
    try:
        stats = json.loads(analyze_research_data.func(group_by_json=['group'], metrics_json=['value'],
                                                    test='ttest_ind', output_format='xlsx'))
        if stats.get('snapshot_status') != 'complete':
            raise RuntimeError('Synthetic analysis snapshot was not persisted')
        ref = stats['analysis_result_ref']
        # A deliberate duplicate column is an engineering positive control,
        # not material from a scientific paper or an accusation.
        _, audit = audit_uploaded_data.func()
        if audit.get('integrity_review', {}).get('status') != 'complete':
            raise RuntimeError('Synthetic audit review was not persisted')
        export = export_table.func(title='SYNTHETIC full precision results', output_format='xlsx',
                                   analysis_result_ref=ref, result_section='descriptive')
        if '/api/v2/session/' not in export:
            raise RuntimeError('Synthetic result export lacks a real artifact URL')
        text, created = word_document.func(operation='create', title='SYNTHETIC engineering result',
            outline=[{'section_id': 'results', 'title': 'Engineering result'}], batch_id='synthetic-create')
        document_id = created.get('document_id')
        if not document_id:
            raise RuntimeError(text)
        blocks = [
            {'block_id': 'heading', 'type': 'heading', 'section_id': 'results', 'level': 1, 'text': 'Synthetic engineering result'},
            {'block_id': 'context', 'type': 'paragraph', 'section_id': 'results', 'text': 'Synthetic correctness fixture. These are not experimental observations.'},
            {'block_id': 'numbers', 'type': 'analysis_table', 'section_id': 'results', 'analysis_result_ref': ref, 'result_section': 'descriptive'},
            {'block_id': 'section_done', 'type': 'section_end', 'section_id': 'results'},
            {'block_id': 'document_done', 'type': 'document_end'},
        ]
        text, changed = word_document.func(operation='append_blocks', document_id=document_id,
            batch_id='synthetic-append', expected_version=created['version'], blocks=blocks)
        if 'version' not in changed:
            raise RuntimeError(text)
        text, word = word_document.func(operation='finalize', document_id=document_id,
            expected_version=changed['version'], expected_last_block_id='document_done', filename='SYNTHETIC_results.docx')
        if not word.get('artifact_url'):
            raise RuntimeError(text)
        spec = {'title': 'SYNTHETIC two-panel engineering fixture', 'panels': [
            {'panel_id': 'growth', 'chart': 'line', 'title': 'Engineering growth',
             'x': {'kind': 'inline', 'values': [1, 10, 100]}, 'y': {'kind': 'inline', 'values': [1, 2, 4]}},
            {'panel_id': 'yield', 'chart': 'scatter', 'title': 'Engineering yield',
             'x': {'kind': 'inline', 'values': [1, 10, 100]}, 'y': {'kind': 'inline', 'values': [3, 5, 8]}}],
            'layout': {'rows': 1, 'cols': 2}, 'shared_legend': False,
            'qa_profile': {'name': 'scientific', 'caption': 'Synthetic engineering fixture; no error bars.'}}
        text, figure = create_research_figure.func(spec_json=json.dumps(spec))
        if not figure.get('editable'):
            raise RuntimeError(text)
        return {'kind': 'synthetic_engineering_fixture', 'analysis_result_ref': ref,
                'dataset_ref': stats['dataset_ref'], 'integrity_review': audit['integrity_review'],
                'word': word, 'figure_id': figure['figure_id']}
    finally:
        reset_request_tables(token)
