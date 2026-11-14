"""Run the actual web application against disposable synthetic acceptance data.

The production lifespan is disabled to avoid model warmup; authentication,
CSRF/owner checks, figure routes, publisher and frontend are the real code.
Only loopback connections are permitted. Stop the preview to discard its DBs.
Synthetic local login: qa_preview_a / Synthetic-preview-2026!
Second empty account: qa_preview_b / Synthetic-preview-2026!
"""
from pathlib import Path
import asyncio
import os
import socket
import sys
import tempfile
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '01_src'))
os.environ['PYTHONDONTWRITEBYTECODE'] = '1'


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8131)
    parser.add_argument('--research-fixtures', action='store_true')
    parser.add_argument('--check-only', type=Path, help='Run actual ASGI routes, save evidence and exit without a server')
    args = parser.parse_args()
    original_connect = socket.socket.connect

    def loopback_only(sock, address):
        if sock.family == getattr(socket, 'AF_UNIX', None) or (
            isinstance(address, tuple) and address[0] in {'127.0.0.1', '::1', 'localhost'}
        ):
            return original_connect(sock, address)
        raise RuntimeError('external network disabled in acceptance preview')

    with tempfile.TemporaryDirectory(prefix='qaagent-browser-', ignore_cleanup_errors=True) as state:
        import src.config as config
        config.PROJECT_ROOT = Path(state)
        from src import auth
        from src.admin_audit import init_store as init_audit
        auth.init_store()
        init_audit()
        users = []
        user_tokens = []
        for name in ('qa_preview_a', 'qa_preview_b'):
            user, _token, error = auth.register(name, 'Synthetic-preview-2026!')
            if error:
                raise RuntimeError(error)
            auth.complete_onboarding(user['user_id'])
            users.append(user)
            user_tokens.append(_token)
        from src.session.manager import session_manager
        sid = asyncio.run(session_manager.get_or_create_session(None, users[0]['user_id']))
        from src.core import tracing
        tracing.set_user_id(users[0]['user_id'])
        tracing.set_session_id(sid)
        tracing.set_client_request_id('synthetic-browser-fixture')
        from acceptance_figures import publish_fixtures
        publish_fixtures(users[0]['user_id'], sid)
        if args.research_fixtures:
            from acceptance_research import publish_research_fixtures
            receipts = publish_research_fixtures(users[0]['user_id'], sid)
            import json
            (Path(state) / 'fixture_receipts.json').write_text(json.dumps(receipts, ensure_ascii=False), encoding='utf-8')
            print('Synthetic research tool/publisher chain ready.', flush=True)
        tracing.set_user_id(None)
        tracing.set_session_id('')
        print('Synthetic figure fixture ready; no production state used.', flush=True)
        with patch('socket.socket.connect', loopback_only), \
             patch('src.core.llm_pool.get_llm', side_effect=RuntimeError('model calls disabled in acceptance preview')), \
             patch('src.retrieval.runtime.get_rag', side_effect=RuntimeError('retrieval disabled in acceptance preview')):
            from src.api.main import app
            if args.check_only:
                if not args.research_fixtures:
                    raise ValueError('--check-only requires --research-fixtures')
                from acceptance_research_http import verify
                verify(app, receipts, user_tokens[0], user_tokens[1], args.check_only)
                return
            import uvicorn
            uvicorn.run(app, host='127.0.0.1', port=args.port, lifespan='off', log_level='warning')


if __name__ == '__main__':
    main()
