"""Startup-only execution evidence for the local WSL figure worker.

Request-time readiness reads this evidence; it never starts a container.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import threading

from .sandbox import SandboxJob, SandboxUnavailable

_LOCK = threading.RLock()
_EVIDENCE: dict = {}
PROBE_VERSION = 4

PROBE_SCRIPT = '''import os
from pathlib import Path
import matplotlib.pyplot as plt
assert os.getuid() == 65534
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
assert status['NoNewPrivs'].strip() == '1'
assert int(status['CapEff'].strip(), 16) == 0
assert set(os.listdir('/sys/class/net')) == {'lo'}
assert any(row.split()[4] == '/' and 'ro' in row.split()[5].split(',') for row in Path('/proc/self/mountinfo').read_text().splitlines())
assert int(Path('/sys/fs/cgroup/memory.max').read_text()) <= 1024 * 1024 * 1024
assert int(Path('/sys/fs/cgroup/pids.max').read_text()) <= 128
quota, period = Path('/sys/fs/cgroup/cpu.max').read_text().split()
assert quota != 'max' and int(quota) / int(period) <= 1
try:
    Path('/work/in/.must-not-write').write_text('blocked')
except OSError:
    pass
else:
    raise RuntimeError('input mount is writable')
fig, ax = plt.subplots(figsize=(4, 3), constrained_layout=True)
ax.plot([1, 10, 100], [2, 4, 3], marker='o', label='Synthetic startup fixture')
ax.set_title('SYNTHETIC startup fixture')
ax.set_xlabel('Synthetic x')
ax.set_ylabel('Synthetic y')
'''


def backend_arguments(settings) -> dict:
    return dict(backend=str(settings.FIGURES_SANDBOX_BACKEND),
                image=str(settings.FIGURES_SANDBOX_IMAGE),
                image_sha256=str(settings.FIGURES_SANDBOX_IMAGE_SHA256),
                runtime=str(settings.FIGURES_SANDBOX_RUNTIME),
                wsl_distribution=str(getattr(settings, 'FIGURES_SANDBOX_WSL_DISTRO', 'Ubuntu-24.04')),
                allow_local=False)


def configuration_key(settings) -> str:
    value = backend_arguments(settings) | {'staging': os.environ.get('CITRUSQA_FIGURE_JOB_DIR', ''),
                                           'probe_version': PROBE_VERSION}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def worker_snapshot(settings) -> dict | None:
    with _LOCK:
        if _EVIDENCE.get('configuration_key') == configuration_key(settings):
            return copy.deepcopy(_EVIDENCE)
    return None


def invalidate_worker(reason: str) -> None:
    with _LOCK:
        if _EVIDENCE:
            _EVIDENCE.update(verified=False, status='unavailable', error=reason[-1000:])


def check_image_identity(identity: str) -> None:
    with _LOCK:
        expected = _EVIDENCE.get('image_id')
        verified = _EVIDENCE.get('verified')
    if expected and (expected != identity or not verified):
        invalidate_worker('Drawing worker evidence is invalid or its image changed; restart to reverify.')
        raise SandboxUnavailable('Drawing worker requires startup verification before further execution.')


def _verify_render(manifest: dict, blobs: dict[str, bytes], mode: str) -> None:
    """Require the current mode and byte receipts, retaining initial Nature QA."""
    from .nature_receipts import validate_nature_receipt
    from .render_policy import RENDER_PROTOCOL
    receipt = manifest.get('render_receipt') or {}
    checks = receipt.get('checks') or {}
    if receipt.get('version') != RENDER_PROTOCOL or receipt.get('mode') != mode:
        raise ValueError('Drawing image has an incompatible render mode protocol.')
    expected_audit = 'performed' if mode == 'initial' else 'not_repeated_manual_edit'
    if receipt.get('nature_audit') != expected_audit:
        raise ValueError('Drawing image did not follow the render mode audit policy.')
    signatures = {'svg': b'<?xml', 'png': b'\x89PNG\r\n\x1a\n', 'pdf': b'%PDF-'}
    for fmt, data in blobs.items():
        check = checks.get(fmt) or {}
        if (not data.startswith(signatures[fmt]) or check.get('format') != 'pass'
                or check.get('size_bytes') != len(data)
                or check.get('sha256') != hashlib.sha256(data).hexdigest()):
            raise ValueError('Drawing export does not match its render integrity receipt.')
    if mode == 'initial':
        validate_nature_receipt(manifest, pdf=blobs.get('pdf'))
    elif manifest.get('nature_skill_receipt') is not None:
        raise ValueError('Manual rendering incorrectly claims a repeated Nature audit.')


def verify_worker(settings, runner) -> dict:
    previous = worker_snapshot(settings)
    if previous and previous.get('verified'):
        return previous
    from .wsl_docker import validate_staging
    root = validate_staging()
    baseline = {path.name for path in root.iterdir()}
    formats = {'svg': 'figure.svg', 'png': 'figure.png', 'pdf': 'figure.pdf',
               'manifest': 'objects.json'}
    exports = {}
    for label, mode in [('create', 'initial'), ('edit_replay', 'edit'), ('preview', 'preview')]:
        edits = [] if mode == 'initial' else [
            {'gid': 'axes_0.title', 'prop': 'text', 'value': 'SYNTHETIC startup edited'},
            {'gid': 'axes_0.title', 'prop': 'fontsize', 'value': 16}]
        outputs = formats if mode == 'initial' else formats | {'overrides': 'overrides.json'}
        result = runner.run(SandboxJob(script=PROBE_SCRIPT, outputs=outputs,
                                       replay_overrides=mode != 'initial', overrides=edits,
                                       render_mode=mode, dpi=100, timeout_sec=45))
        if not result.ok:
            raise SandboxUnavailable(f'Drawing startup {label} failed: {result.describe()}; '
                                     f'{result.stderr[-1500:]} {result.stdout[-500:]}')
        try:
            manifest = json.loads(result.outputs['manifest'])
            _verify_render(manifest, {fmt: result.outputs[fmt] for fmt in ('svg', 'png', 'pdf')}, mode)
            title = next((item for item in manifest['objects'] if item['gid'] == 'axes_0.title'), {})
            if mode != 'initial':
                if title.get('text') != 'SYNTHETIC startup edited' or title.get('fontsize') != 16:
                    raise ValueError('Drawing worker did not apply the edit/replay protocol.')
                receipt = json.loads(result.stdout.strip().splitlines()[-1])
                if receipt.get('override_protocol') != 1 or receipt.get('applied_count') != len(edits):
                    raise ValueError('Drawing image has an incompatible worker override protocol.')
        except (ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
            raise SandboxUnavailable(f'Drawing startup {label} verification failed: {exc}') from exc
        exports[label] = {name: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                          for name, data in result.outputs.items()}
    from .script_outputs import normalize_output_contract, output_slots, validate_receipt
    from .dataset_codec import decode_frame
    contract = normalize_output_contract({'version': 2, 'tables': [{'name': 'values'}],
        'figures': [{'name': 'first', 'formats': ['png']}, {'name': 'second', 'formats': ['png']}]})
    script = PROBE_SCRIPT + '''
import pandas as pd
emit.table('values', pd.DataFrame({'id': pd.Series([2**53+1], dtype='Int64')}))
emit.figure('first', fig)
second, ax2 = plt.subplots(figsize=(4, 3), constrained_layout=True)
ax2.plot([1, 2], [3, 4])
ax2.set_title('SYNTHETIC second')
ax2.set_xlabel('Synthetic x'); ax2.set_ylabel('Synthetic y')
emit.figure('second', second)
'''
    for mode, target in [('initial', ''), ('edit', 'second'), ('preview', 'second')]:
        edits = [{'gid': 'axes_0.title', 'prop': 'text', 'value': 'SYNTHETIC protocol edited'}] if target else []
        job = SandboxJob(script=script, output_contract=contract, output_key=target,
                         outputs=output_slots(contract, output_key=target), overrides=edits,
                         replay_overrides=bool(target), render_mode=mode, dpi=100, timeout_sec=45)
        result = runner.run(job)
        try:
            if not result.ok:
                raise ValueError(f'{result.describe()}; {result.stderr[-1500:]} {result.stdout[-500:]}')
            receipt = validate_receipt(result.outputs['result'], contract, job_id=job.job_id, output_key=target)
            if receipt['status'] != 'ok' or receipt['applied_count'] != len(edits):
                errors = {key: state for key, state in receipt['slots'].items() if state['status'] == 'error'}
                raise ValueError(f'incomplete v2 output/edit receipt: {json.dumps(errors, ensure_ascii=False)[:2000]}')
            if target:
                title = next(item for item in json.loads(result.outputs['figure:second:manifest'])['objects']
                             if item['gid'] == 'axes_0.title')
                if title.get('text') != 'SYNTHETIC protocol edited' or 'figure:first:png' in result.outputs:
                    raise ValueError('targeted replay did not select the requested figure')
            else:
                frame = decode_frame(json.loads(result.outputs['table:values']))
                if int(frame.iloc[0, 0]) != 2**53+1:
                    raise ValueError('typed intermediate table lost precision')
            for entry in contract['figures']:
                if target and entry['name'] != target:
                    continue
                prefix = f"figure:{entry['name']}:"
                _verify_render(json.loads(result.outputs[prefix + 'manifest']),
                               {fmt: result.outputs[prefix + fmt] for fmt in entry['formats']}, mode)
            label = 'outputs_v2' if mode == 'initial' else 'outputs_v2_' + mode
            exports[label] = {
                key: {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
                for key, data in result.outputs.items()}
        except (ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
            raise SandboxUnavailable(f'Drawing worker output protocol v2 verification failed: {exc}') from exc
    if {path.name for path in root.iterdir()} != baseline:
        raise SandboxUnavailable('Drawing startup left job staging directories behind.')
    evidence = dict(backend=runner.name, status='ready', verified=True,
                    configuration_key=configuration_key(settings), probe_version=PROBE_VERSION,
                    distribution=runner.distribution, image_id=runner.image_id,
                    isolation_verified=True, edit_replay_verified=True, preview_verified=True,
                    render_protocol=2, cleanup_verified=True,
                    exports=exports)
    with _LOCK:
        _EVIDENCE.clear()
        _EVIDENCE.update(evidence)
    return copy.deepcopy(evidence)
