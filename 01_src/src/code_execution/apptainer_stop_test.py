"""The stop contract depends on the observed invocation and cgroup."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.code_execution import apptainer
from src.code_execution.apptainer import ApptainerStatus, OfflineApptainerBackend


@pytest.mark.parametrize('returncode, uncertain, truncated', [
    (0, False, False),
    (1, False, False),
    (None, True, False),
    (0, False, True),
])
def test_stop_waits_for_original_cgroup_after_signal(monkeypatch, returncode, uncertain, truncated):
    identity = {'invocation': 'original', 'cgroup': '/original'}
    replacement = {'invocation': 'replacement', 'cgroup': '/replacement'}
    stopped = ApptainerStatus('stopped', stopped=True, identity=identity)
    backend = SimpleNamespace(
        config=SimpleNamespace(control_timeout_sec=1),
        inspect=Mock(side_effect=[ApptainerStatus('running', identity=identity),
                                  ApptainerStatus('unknown'),
                                  ApptainerStatus('stopped', stopped=True, identity=replacement),
                                  stopped]),
        _unit=Mock(return_value={'InvocationID': 'original'}),
        _run=Mock(return_value=SimpleNamespace(returncode=returncode, uncertain=uncertain,
                                             truncated=truncated)),
    )
    clock = iter((0, 0.1, 0.2, 0.3))
    monkeypatch.setattr(apptainer.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(apptainer.time, 'sleep', lambda seconds: None)

    assert OfflineApptainerBackend.kill(backend, SimpleNamespace(name='fixture.service')) is stopped
    backend._run.assert_called_once()
    assert backend.inspect.call_count == 4


def test_stop_without_cgroup_proof_remains_unknown(monkeypatch):
    identity = {'invocation': 'original', 'cgroup': '/original'}
    backend = SimpleNamespace(
        config=SimpleNamespace(control_timeout_sec=1),
        inspect=Mock(side_effect=[ApptainerStatus('running', identity=identity), ApptainerStatus('unknown')]),
        _unit=Mock(return_value={'InvocationID': 'original'}),
        _run=Mock(return_value=SimpleNamespace(returncode=1, uncertain=False, truncated=False)),
    )
    clock = iter((0, 0.4, 1.1))
    monkeypatch.setattr(apptainer.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(apptainer.time, 'sleep', lambda seconds: None)

    observed = OfflineApptainerBackend.kill(backend, SimpleNamespace(name='fixture.service'))

    assert observed.state == 'unknown'
    assert observed.identity == identity
    assert not observed.stopped
    backend._run.assert_called_once()
