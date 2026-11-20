"""Run current source with an existing model configuration and an isolated worker.

Use an existing Conda interpreter. Corpus/model assets and credential source are
read from explicit locations; account/session/checkpoint writes stay in the new
runtime. No credentials are copied to the repository or passed on command lines.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import uuid

ROOT = Path(__file__).resolve().parents[2]
MODEL_FIELDS = frozenset({'main_model', 'fast_model', 'main_base_url', 'fast_base_url',
                         'context_window_tokens', 'provider_id'})


def existing_directory(value: str) -> Path:
    path = Path(value)
    if not path.is_absolute() or not path.is_dir():
        raise argparse.ArgumentTypeError('An existing absolute directory is required')
    return path.resolve()


def configure(args):
    runtime = args.runtime_root
    if not runtime.is_absolute():
        raise ValueError('runtime-root must be absolute')
    runtime = runtime.resolve()
    source = args.model_config_root
    backend = getattr(args, 'backend', 'wsl-docker')
    existing = bool(getattr(args, 'existing_runtime', False))
    if backend == 'apptainer':
        if sys.platform != 'linux' or os.getuid() == 0:
            raise ValueError('Apptainer requires an unprivileged Linux service account')
        import re
        if not re.fullmatch(r'[0-9a-f]{64}', getattr(args, 'image_sha256', '')):
            raise ValueError('Apptainer requires a pinned SIF SHA-256')
        if not Path(args.image).is_absolute() or not args.image.endswith('.sif'):
            raise ValueError('Apptainer requires an absolute local SIF path')
    elif backend != 'wsl-docker' or existing:
        raise ValueError('Existing runtime reuse is restricted to explicit Linux Apptainer deployment')
    if ((runtime == source and not existing)
            or runtime.is_relative_to(ROOT / '01_src')):
        raise ValueError('Use runtime state separate from source assets and credential source')
    if existing and (not runtime.is_dir() or not (runtime / 'state/model_config.json').is_file()):
        raise ValueError('Existing runtime must already contain its model configuration')
    if not (args.corpus_root / 'active_index.json').is_file():
        raise ValueError('Existing corpus active_index.json is required; no index is rebuilt')
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / '.configured-local-runtime'
    if not existing and not marker.exists() and any(runtime.iterdir()):
        raise ValueError('A new configured runtime must be empty')
    marker.touch(exist_ok=True)
    state = runtime / 'state'
    state.mkdir(exist_ok=True)
    config_path = state / 'model_config.json'
    if not config_path.exists():
        previous = json.loads((source / 'state/model_config.json').read_text(encoding='utf-8'))
        selected = {key: str(value).strip() for key, value in previous.items()
                    if key in MODEL_FIELDS and str(value).strip()}
        if not selected.get('provider_id') or not selected.get('main_model'):
            raise ValueError('The existing instance must have an explicit provider and model')
        with config_path.open('x', encoding='utf-8') as stream:
            json.dump(selected, stream, ensure_ascii=False)
    os.environ.update({
        'CITRUSQA_PROJECT_ROOT': str(runtime), 'DATA_DIR': str(args.corpus_root),
        'CITRUSQA_MODEL_CACHE_ROOT': str(args.model_cache_root),
        'HF_HOME': str(args.model_cache_root / 'hf'),
        'FASTEMBED_CACHE_PATH': str(args.model_cache_root / 'fastembed'),
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'CITRUS_ONNX_DEVICE': 'gpu',
        'FIGURES_SANDBOX_BACKEND': backend,
        'FIGURES_SANDBOX_WSL_DISTRO': args.distribution,
        'FIGURES_SANDBOX_IMAGE': args.image,
        'FIGURES_SANDBOX_IMAGE_SHA256': getattr(args, 'image_sha256', ''),
        'FIGURES_SANDBOX_RUNTIME': getattr(args, 'apptainer_path', '/usr/bin/apptainer') if backend == 'apptainer' else '',
        'FIGURES_SANDBOX_ALLOW_LOCAL': 'false',
        'CITRUSQA_FIGURE_JOB_DIR': str(args.worker_staging),
        'CITRUS_REQUIRE_FIGURE_WORKER': '1',
        'CITRUS_LOCAL_INSTANCE_ID': uuid.uuid4().hex,
    })
    if backend == 'apptainer':
        os.environ['CITRUS_APPTAINER_HOST_PYTHON'] = args.host_python
        # hardware.py defaults to GPU and reads an explicit administrator choice
        # from this runtime. A forced env value would erase that choice on restart.
        os.environ.pop('CITRUS_ONNX_DEVICE', None)
    # Start in GPU mode, while preserving subsequent administrator CPU/auto choices.
    os.environ.pop('CITRUS_REQUIRE_GPU', None)
    if args.require_full:
        os.environ['CITRUS_LOCAL_FULL_STARTUP'] = '1'
    else:
        os.environ.pop('CITRUS_LOCAL_FULL_STARTUP', None)
    if args.direct_network:
        for name in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
                     'http_proxy', 'https_proxy', 'all_proxy'):
            os.environ.pop(name, None)
    sys.path.insert(0, str(ROOT / '01_src'))
    if getattr(args, 'offline_code', False):
        from src.code_execution.presence import worker_available
        if not worker_available(state / 'code_executions.sqlite3'):
            raise ValueError('Start the independent offline Code Worker for this runtime first')
        os.environ['CITRUS_CODE_EXECUTION'] = 'offline-development'
    else:
        os.environ.pop('CITRUS_CODE_EXECUTION', None)
    from src.config import settings
    # Existing keys saved by this runtime take precedence. The authorized old
    # instance supplies the selected key in memory only; no session DB is read.
    provider = settings.RESOLVED_PROVIDER_ID
    if not settings._runtime_provider_api_keys.get(provider):
        key_path = source / 'state/provider_api_keys.json'
        keys = json.loads(key_path.read_text(encoding='utf-8'))
        key = keys.get(provider)
        if not isinstance(key, str) or not key.strip():
            raise ValueError('Selected provider has no configured key in the authorized source')
        settings._runtime_provider_api_keys[provider] = key.strip()
        settings._runtime_configured_model_providers.add(provider)
    # Reuse the authorized deployment's network configuration in memory only.
    # Runtime-local settings win; no credentials or account DBs are copied.
    if not getattr(args, "without_source_web_search", False):
        search_config = source / 'state/web_search_config.json'
        if not settings._runtime_web_search_cfg and search_config.is_file():
            search_provider = json.loads(search_config.read_text(encoding='utf-8')).get('provider_id')
            if search_provider in {'deepseek_search_fetch', 'zhipu_web_search'}:
                settings._runtime_web_search_cfg = {'provider_id': search_provider}
        search_key = source / 'state/deepseek_web_api_key'
        if not settings.RESOLVED_DEEPSEEK_WEB_API_KEY and search_key.is_file():
            settings._runtime_deepseek_web_api_key = search_key.read_text(encoding='utf-8').strip()
        if settings.RESOLVED_WEB_SEARCH_PROVIDER == 'zhipu_web_search' and not settings.RESOLVED_ZHIPU_API_KEY:
            source_keys = json.loads((source / 'state/provider_api_keys.json').read_text(encoding='utf-8'))
            zhipu_key = source_keys.get('zhipu')
            if isinstance(zhipu_key, str) and zhipu_key.strip():
                settings._runtime_provider_api_keys['zhipu'] = zhipu_key.strip()
    if backend == 'apptainer':
        from src.figures.readiness import validate_apptainer_staging
        validate_apptainer_staging()
    else:
        from src.figures.wsl_docker import validate_staging
        validate_staging()
    print(f'[CitrusScholar] existing environment: {sys.prefix}', flush=True)
    print(f'[CitrusScholar] model provider: {provider}; model: {settings.RESOLVED_MAIN_MODEL}', flush=True)
    print(f'[CitrusScholar] {backend} worker workspace: {args.worker_staging}', flush=True)
    return runtime


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime-root', type=Path, required=True)
    parser.add_argument('--model-config-root', type=existing_directory, required=True)
    parser.add_argument('--corpus-root', type=existing_directory, required=True)
    parser.add_argument('--model-cache-root', type=existing_directory, required=True)
    parser.add_argument('--worker-staging', type=existing_directory, required=True)
    parser.add_argument('--distribution', default='Ubuntu-24.04')
    parser.add_argument('--backend', choices=('wsl-docker', 'apptainer'), default='wsl-docker')
    parser.add_argument('--image', required=True)
    parser.add_argument('--image-sha256', default='')
    parser.add_argument('--apptainer-path', default='/usr/bin/apptainer')
    parser.add_argument('--host-python', default='/usr/bin/python3',
                        help='Trusted host interpreter used by the systemd sandbox guard')
    parser.add_argument('--existing-runtime', action='store_true',
                        help='Linux deployment only: reuse runtime in place, without copying account/session databases')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8167)
    parser.add_argument('--require-full', action='store_true')
    parser.add_argument('--offline-code', action='store_true',
                        help='Enable trusted local offline Code after its independent Worker starts')
    parser.add_argument('--prepare-only', action='store_true',
                        help='Initialize an independent runtime for Worker startup, without a server')
    parser.add_argument('--without-source-web-search', action='store_true',
                        help='Use runtime-local Search settings only')
    parser.add_argument('--direct-network', action='store_true',
                        help='Ignore inherited HTTP proxy variables in this process only')
    args = parser.parse_args()
    if args.existing_runtime and args.prepare_only:
        parser.error('prepare-only is only for a new independent runtime')
    if not 1024 <= args.port <= 65535:
        parser.error('port must be between 1024 and 65535')
    with socket.socket() as connection:
        if connection.connect_ex(('127.0.0.1', args.port)) == 0:
            parser.error('port is occupied; no running service will be replaced')
    configure(args)
    if args.prepare_only:
        from src.auth import init_store as init_accounts
        from src.session.files import init_store as init_files
        init_accounts()
        init_files()
        from src.session.manager import session_manager
        print('[CitrusScholar] Independent runtime prepared; no server started', flush=True)
        return
    import uvicorn
    uvicorn.run('src.api.main:app', host=args.host, port=args.port, reload=False,
                proxy_headers=False)


if __name__ == '__main__':
    main()
