"""Run only offline engineering checks, with API import boundaries in separate processes.

From repository root: python engineering/word_delivery_20261112/run_checks.py
No model provider, live service, or scientific benchmark is invoked.
"""
from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MAIN = [
    "core/test_word_documents.py", "core/test_merged_audit_marks.py", "core/test_deliverable_check.py",
    "core/test_turn_status.py", "core/test_public_progress.py",
    "research/test_word_tools.py", "research/test_single_report.py", "research/test_artifact_append.py",
    "research/test_truncated_json.py", "research/test_export_table.py",
    "graph/test_document_contracts.py", "graph/test_provider_readiness.py", "graph/test_reliability_contracts.py",
    "graph/test_export_attribution.py", "graph/test_tool_bindings.py", "graph/test_tool_visibility.py",
    "graph/test_general_agent_routing.py", "prompts/test_word_skill_loading.py", "tools/test_outcome.py",
    "tools/test_runtime_contracts.py",
    "research/test_data_audit_xlsx.py", "research/test_deliverable_publish_guard.py",
    "research/test_annotation_tools.py", "research/test_audit_tools.py",
    "research/test_literature_handoff.py", "research/test_research_notes_and_citation_exports.py",
    "research/test_artifact_lifecycle.py", "research/test_reference_batch.py", "research/test_header_and_optional.py",
    "test_account_deletion.py", "session/test_file_turn_refs.py",
    # General-agent ecosystem contracts (typed terminal state, one execution
    # ledger, canonical history turns, SSE seq, frontend reducer).
    "core/test_execution_ledger.py", "core/test_context_budget.py", "core/test_turn_envelope.py",
    "core/test_cancel_lifecycle.py", "api/test_terminal_state_frontend.py",
    "api/test_stop_turn_contract.py", "api/test_trace_turn_merge.py",
    "api/test_history_pagination.py", "api/test_frontend_round7.py",
    "api/test_web_script_scope.py", "session/test_turn_index.py",
    # Request-scoped candidate pool (synchronous local prefetch + bounded
    # candidate tools) and the dead-code removal guard.
    "retrieval/test_candidate_pool.py", "graph/test_candidate_prefetch.py",
    "tools/test_candidate_tools.py", "graph/test_candidate_graph.py",
    "core/test_dead_code_removed.py",
]
GROUPS = {
    "main": MAIN,
    "word_download": ["api/test_word_delivery_download.py"],
    "attachment_boundaries": ["api/test_file_boundary_matrix.py"],
}


def main():
    env = {**os.environ, "PYTHONPATH": str(ROOT / "01_src"), "PYTHONIOENCODING": "utf-8",
           "PYTHONDONTWRITEBYTECODE": "1"}
    results = []
    for name, tests in GROUPS.items():
        command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
                   f"--junitxml={OUT / (name + '.xml')}"]
        command += [str(ROOT / "01_src/src" / test) for test in tests]
        result = subprocess.run(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, encoding="utf-8", errors="replace")
        (OUT / f"{name}.log").write_text(result.stdout, encoding="utf-8")
        print(f"{name}: exit={result.returncode}", flush=True)
        print(result.stdout, flush=True)
        results.append(result.returncode)
    return 1 if any(results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
