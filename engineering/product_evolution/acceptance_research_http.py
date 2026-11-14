"""Actual app ASGI/cookie/publisher integration, without launching a server.

This does not claim natural-language model orchestration or browser acceptance.
"""
from __future__ import annotations
import hashlib
import io
import json
from pathlib import Path
from zipfile import ZipFile


def verify(app, receipts: dict, owner_token: str, other_token: str, output: Path):
    from fastapi.testclient import TestClient
    from src.research.analysis_delivery import result_table
    from src.core.tracing import get_user_id
    checks = []
    def check(name, condition):
        checks.append({'name': name, 'passed': bool(condition), 'source': 'actual_app_asgi'})
        if not condition:
            raise AssertionError(name)
    output.mkdir(parents=True, exist_ok=False)
    try:
        from contextlib import asynccontextmanager
        from unittest.mock import patch
        @asynccontextmanager
        async def without_model_warmup(_app):
            yield
        with patch.object(app.router, 'lifespan_context', without_model_warmup), TestClient(app, base_url='http://testserver') as client:
            client.cookies.set('citrus_auth', owner_token)
            headers = {'origin': 'http://testserver'}
            paths = client.get('/openapi.json').json()['paths']
            check('New task events and updates routes are mounted', '/jobs/{job_id}/events' in paths and '/api/v2/chat/jobs/{job_id}/updates' in paths)
            check('Model-unwarmed instance does not claim ready', client.get('/ready').status_code == 503)
            rid = receipts['integrity_review']['review_id']
            response = client.get('/api/v2/integrity/reviews/' + rid)
            check('Real audit review can be fetched with actual account cookie', response.status_code == 200)
            review = response.json()
            findings = review['report']['common_findings']
            check('Actual duplicate fixture produces located evidence', any(f['rule'] == 'exact_column' for f in findings))
            selected = next(f for f in findings if f['rule'] == 'exact_column')
            endpoint = '/api/v2/integrity/reviews/' + rid + '/findings/' + selected['finding_id']
            decision = {'state': 'explained', 'note': 'Deliberate synthetic duplicate column; engineering positive control.', 'expected_revision': 0}
            check('Full middleware refuses originless review write', client.put(endpoint, json=decision).status_code == 403)
            check('Same-origin real review decision commits', client.put(endpoint, json=decision, headers=headers).status_code == 200)
            check('Stale review revision conflicts', client.put(endpoint, json=decision, headers=headers).status_code == 409)
            check('Review source region is authorized and readable', client.get(endpoint + '/sources/0').status_code == 200)
            word = client.get(receipts['word']['artifact_url'])
            check('Real Word publication downloads', word.status_code == 200 and word.content.startswith(b'PK'))
            check('Downloaded Word equals publisher receipt', hashlib.sha256(word.content).hexdigest() == receipts['word']['sha256'])
            with ZipFile(io.BytesIO(word.content)) as package:
                document_xml = package.read('word/document.xml').decode()
            check('Word contains verified result identity and precision', receipts['analysis_result_ref']['sha256'] in document_xml and '3.8333333333333335' in document_xml)
            (output / 'SYNTHETIC_results.docx').write_bytes(word.content)
            fid = receipts['figure_id']
            project_path = '/api/v2/figures/projects/' + fid
            detail = client.get(project_path)
            check('Real multi-panel project is owner readable', detail.status_code == 200)
            objects = detail.json()['manifest']['objects']
            check('Panel identities survive API projection', any(o['gid'] == 'panel_growth' for o in objects) and any(o['gid'] == 'panel_yield' for o in objects))
            svg = client.get(project_path + '/preview.svg')
            export = client.get(project_path + '/export/svg')
            check('SVG preview and download are identical bytes', svg.status_code == 200 and export.status_code == 200 and svg.content == export.content)
            for fmt, signature in [('png', b'\x89PNG'), ('pdf', b'%PDF')]:
                result = client.get(project_path + '/export/' + fmt)
                check('Same project actual ' + fmt.upper() + ' downloads', result.status_code == 200 and result.content.startswith(signature))
            (output / 'SYNTHETIC_panels.svg').write_bytes(svg.content)
            client.cookies.set('citrus_auth', other_token)
            check('Other account cannot fetch review', client.get('/api/v2/integrity/reviews/' + rid).status_code == 404)
            check('Other account cannot download Word', client.get(receipts['word']['artifact_url']).status_code in {403, 404})
            check('Other account cannot fetch figure', client.get(project_path).status_code == 404)
    finally:
        (output / 'checks.json').write_text(json.dumps({'checks': checks,
            'limits': ['Synthetic engineering input only', 'Actual tools, stores, publisher, cookie authentication and complete ASGI middleware',
                       'No model calls; not a natural-language orchestration test', 'No browser or live TCP server', 'No deployment or GPU acceptance']}, ensure_ascii=False, indent=2), encoding='utf-8')
    print(str(output / 'checks.json'), flush=True)
