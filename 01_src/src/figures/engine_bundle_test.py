"""Worker packaging checks; no Docker, provider or upstream code execution."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from src.figures.engine_bundle import bundle_files, stage_engine

ROOT = Path(__file__).resolve().parents[2]


def test_bundle_contains_all_dockerfile_runtime_sources_and_verified_vendor(tmp_path):
    stage_engine(tmp_path)
    lines = (ROOT / 'docker/Dockerfile.sandbox').read_text(encoding='utf-8').splitlines()
    for line in lines:
        if not line.startswith('COPY 01_src/src/'):
            continue
        _, source, destination = line.split()
        expected = ROOT.parent / source
        actual = tmp_path / destination.removeprefix('/opt/citrusfig/')
        if expected.is_file():
            assert actual.read_bytes() == expected.read_bytes()
    vendor = tmp_path / 'src/prompts/skills/vendor/nature-skills'
    manifest = json.loads((vendor / 'provenance.json').read_bytes())
    for item in manifest['files']:
        assert hashlib.sha256((vendor / item['path']).read_bytes()).hexdigest() == item['sha256']
    assert not (tmp_path / 'src/config.py').exists()
    assert not (tmp_path / 'agent').exists()


@pytest.mark.parametrize('install', [False, True])
def test_local_refresh_stages_complete_runtime_and_checks_dependency_versions(tmp_path, install):
    spec = importlib.util.spec_from_file_location('worker_refresh', ROOT / 'scripts/update_local_sandbox.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.prepare_context(tmp_path, 'citrusqa/sandbox:checked-base', install)
    dockerfile = (tmp_path / 'Dockerfile').read_text(encoding='utf-8')
    assert 'COPY engine/ /opt/citrusfig/' in dockerfile
    assert ('pip install --no-cache-dir -r' in dockerfile) is install
    assert 'version(name)==wanted' in dockerfile
    assert (tmp_path / 'requirements.txt').read_bytes() == (ROOT / 'docker/requirements.sandbox.txt').read_bytes()
    for name, data in bundle_files().items():
        assert (tmp_path / 'engine' / name).read_bytes() == data
