"""Host repair identity requires unchanged inputs/contract, not tool-name matching."""
from copy import deepcopy
from src.research.script_runtime_test import temporary_runtime, execute
import src.tools.outcome  # Load registry before the isolated runtime settings fixture.
from src.core.op_receipt import make_receipt
from src.core.task_result import effective_tool_records


def test_source_revision_repairs_same_operation_but_changed_bytes_do_not():
    def executor(status, digest):
        def run(**kwargs):
            kwargs['script_record']['input_snapshots'] = [{
                'variable': 'data', 'sha256': digest, 'source_file_id': 'source'}]
            return '{}', {'_tool_outcome': {'status': status, 'reason_code': 'fixture', 'artifacts': []}}
        return run
    _, failed = execute(executor('failed', 'a' * 64), script='print(1)')
    _, fixed = execute(executor('complete', 'a' * 64), script='print(2)')
    _, different = execute(executor('complete', 'b' * 64), script='print(2)')
    assert failed['repair_identity'] == fixed['repair_identity']
    assert failed['repair_identity'] != different['repair_identity']
    name = 'run_research_figure_script'
    def record(payload, cid):
        receipt = make_receipt(name, cid, '{}', payload)
        return {'name': name, 'tool_call_id': cid, 'logical_key': cid, 'attempt': 1,
                'status': receipt['status'], 'reason_code': receipt['reason_code'], 'op_receipt': receipt}
    old, repaired, changed = record(failed, 'a'), record(fixed, 'b'), record(different, 'c')
    assert effective_tool_records([old, repaired]) == [repaired]
    assert len(effective_tool_records([old, changed])) == 2
    forged = deepcopy(repaired)
    forged['op_receipt']['tool_call_id'] = 'not-the-call'
    assert len(effective_tool_records([old, forged])) == 2
    assert old['status'] == 'failed'
