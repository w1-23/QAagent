"""CPU shape bookkeeping must never hide model tensor computation in readiness."""
import json
from types import SimpleNamespace

import pytest

from src.engine import hardware

GPU = 'CUDAExecutionProvider'
CPU = 'CPUExecutionProvider'


def node(op, inputs, outputs):
    return {'op_name': op, 'input_type_shape': inputs, 'output_type_shape': outputs}


@pytest.mark.parametrize('args', [
    node('Gather', [{'int64':[4]}, {'int64':[]}], [{'int64':[]}]),
    node('Where', [{'bool':[4]}, {'int64':[4]}, {'int64':[4]}], [{'int64':[4]}]),
    node('Cast', [{'int64':[1]}], [{'float':[1]}]),
    node('Sqrt', [{'float':[1]}], [{'float':[1]}]),
    node('Div', [{'float':[1]}, {'float':[1]}], [{'float':[1]}]),
    node('Shape', [{'float':[1,512,1024]}], [{'int64':[3]}]),
])
def test_observed_bounded_control_profiles_are_explicitly_recognized(args):
    assert hardware._cpu_control_node(args)


@pytest.mark.parametrize('args', [
    node('MatMul', [{'float':[1]}, {'float':[1]}], [{'float':[1]}]),
    node('Gemm', [{'float':[1]}, {'float':[1]}], [{'float':[1]}]),
    node('Gather', [{'float':[4]}, {'int64':[]}], [{'float':[]}]),
    node('Gather', [{'int64':[10000]}, {'int64':[4]}], [{'int64':[4]}]),
    node('Sqrt', [{'float':[1024]}], [{'float':[1024]}]),
    node('Cast', [{'int64':[20]}], [{'float':[20]}]),
    node('Shape', [{'float':[1,2]}], [{'int64':[100]}]),
    node('Gather', [{'int64':[-1]}], [{'int64':[1]}]),
    node('Gather', [], [{'int64':[1]}]),
    node('Gather', [{'int64':[1]}], None),
    {'op_name':'Gather'},
])
def test_model_compute_large_or_unknown_nodes_are_never_hidden(args):
    assert not hardware._cpu_control_node(args)


def test_profile_separates_recorded_control_but_keeps_cpu_matmul_failure(tmp_path):
    path = tmp_path/'profile.json'
    control = node('Gather', [{'int64':[4]}, {'int64':[]}], [{'int64':[]}])
    events = [
        {'cat':'Node','args':control | {'provider':CPU}},
        {'cat':'Node','args':{'op_name':'MatMul','provider':GPU}},
        {'cat':'Node','args':{'op_name':'MemcpyFromHost','provider':GPU}},
        {'cat':'Node','args':{'op_name':'MatMul','provider':CPU}},
    ]
    path.write_text(json.dumps(events),encoding='utf-8')
    evidence = {}
    assert hardware.profile_compute_providers(SimpleNamespace(end_profiling=lambda: str(path)),
        allow_cpu_control=True,evidence=evidence) == [CPU,GPU]
    assert evidence == {'cpu_control_ops':{'Gather':1}}
    assert not path.exists()


def test_control_only_profile_cannot_establish_gpu_compute(tmp_path):
    path = tmp_path/'profile.json'
    args = node('Gather', [{'int64':[4]}, {'int64':[]}], [{'int64':[]}])
    path.write_text(json.dumps([{'cat':'Node','args':args | {'provider':CPU}}]),encoding='utf-8')
    assert hardware.profile_compute_providers(SimpleNamespace(end_profiling=lambda: str(path)),
        allow_cpu_control=True) == []
