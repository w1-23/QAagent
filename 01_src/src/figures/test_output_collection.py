"""The worker output directory is untrusted, including file identities."""
import os
from pathlib import Path

import pytest

from src.figures import sandbox


@pytest.fixture
def output_dir(tmp_path):
    (tmp_path / 'out').mkdir()
    return tmp_path


def job(name='figure.svg'):
    return sandbox.SandboxJob(outputs={'svg': name})


def test_collects_regular_file(output_dir):
    (output_dir / 'out/figure.svg').write_bytes(b'<svg/>')
    assert sandbox._read_outputs(output_dir, job()) == {'svg': b'<svg/>'}


@pytest.mark.parametrize('name', ['../figure.svg', '/figure.svg', 'a\\figure.svg', 'C:figure.svg'])
def test_rejects_paths_instead_of_silently_using_basename(output_dir, name):
    (output_dir / 'out/figure.svg').write_bytes(b'<svg/>')
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, job(name))


def test_missing_output_is_not_success(output_dir):
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, job())


def test_rejects_size_before_open(output_dir, monkeypatch):
    (output_dir / 'out/figure.svg').write_bytes(b'x' * 33)
    monkeypatch.setattr(sandbox, 'MAX_OUTPUT_BYTES', 32)
    with pytest.raises(sandbox.SandboxError, match='体积'):
        sandbox._read_outputs(output_dir, job())


def test_enforces_total_budget(output_dir, monkeypatch):
    for name in ('a', 'b'):
        (output_dir / 'out' / name).write_bytes(b'x' * 20)
    monkeypatch.setattr(sandbox, 'MAX_TOTAL_OUTPUT_BYTES', 30)
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, sandbox.SandboxJob(outputs={'a': 'a', 'b': 'b'}))


def test_rejects_hardlink(output_dir):
    source = output_dir / 'private'
    source.write_bytes(b'private contents')
    os.link(source, output_dir / 'out/figure.svg')
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, job())


def test_rejects_symlink(output_dir):
    source = output_dir / 'private'
    source.write_bytes(b'private contents')
    try:
        (output_dir / 'out/figure.svg').symlink_to(source)
    except OSError:
        pytest.skip('Platform does not grant symlink creation')
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, job())


def test_rejects_replaced_file_before_read(output_dir, monkeypatch):
    path = output_dir / 'out/figure.svg'
    path.write_bytes(b'initial')
    replacement = output_dir / 'replacement'
    replacement.write_bytes(b'private replacement')
    real_open = os.open
    replaced = False

    def racing_open(target, flags, *args, **kwargs):
        nonlocal replaced
        if Path(target).name == 'figure.svg' and not replaced:
            replaced = True
            os.replace(replacement, path)
        return real_open(target, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', racing_open)
    with pytest.raises(sandbox.SandboxError, match='替换'):
        sandbox._read_outputs(output_dir, job())


def test_rejects_directory(output_dir):
    (output_dir / 'out/figure.svg').mkdir()
    with pytest.raises(sandbox.SandboxError):
        sandbox._read_outputs(output_dir, job())
