"""Offline correctness checks; each run has a new output directory.

Run from the repository root with the development Python environment:
    python engineering/product_evolution/run_checks.py
Use --group NAME for a targeted rerun. This runner does not discover arbitrary
pytest files, run scientific benchmarks, start a service, or alter old reports.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "01_src/src"
GROUPS = {
    "figures": [
        "core/test_figure_request_scope.py", "figures/test_figure_numeric_correctness.py", "figures/test_figure_engine.py",
        "figures/manifest_geometry_test.py", "figures/semantic_editing_test.py", "figures/svg_semantic_export_test.py",
        "figures/test_override_coordinates.py", "figures/test_render_lifetime.py",
        "figures/test_output_collection.py", "figures/test_raster_export_safety.py",
        "figures/test_sandbox.py", "figures/test_apptainer.py", "figures/test_worker_overrides.py", "research/test_figure_tools.py",
        "prompts/test_figure_stat_skill_loading.py", "figures/s4_capabilities_test.py",
        "research/test_figure_lifecycle.py", "session/test_resource_references.py", "figures/capacity_quota_test.py",
        "figures/trusted_render_test.py",
    ],
    "figure_api": [
        "api/test_figure_router.py", "api/test_figure_export_consistency.py",
        "api/test_figure_isolation.py", "api/test_figure_security_negative.py",
        "api/figure_scatter_test.py", "api/test_figure_preview.py", "api/s4_figure_api_test.py",
    ],
    "research": [
        "research/test_statistics_tools.py", "research/test_analysis_results.py", "research/test_statistics_designs.py",
        "research/test_analysis_delivery.py", "tools/test_outcome.py",
        "research/reference_verification_policy_test.py", "research/test_reference_batch.py",
        "research/summary_consistency_test.py", "research/test_audit_tools.py",
        "research/test_header_and_optional.py", "research/integrity_test.py",
        "research/integrity_integration_test.py", "research/integrity_review_test.py", "research/integrity_observations_test.py", "research/image_integrity_test.py",
        "research/image_integrity_integration_test.py",
        "research/test_data_audit_xlsx.py", "research/test_annotation_tools.py",
    ],
    "contracts": [
        "core/test_execution_ledger.py", "core/test_turn_status.py", "core/jobs_idempotency_test.py",
        "core/test_turn_envelope.py", "core/test_cancel_lifecycle.py",
        "graph/test_reliability_contracts.py", "graph/test_candidate_prefetch.py",
        "graph/test_figure_delivery_contract.py", "graph/test_tool_bindings.py",
        "graph/test_tool_visibility.py", "test_account_deletion.py",
        "core/test_account_leases.py", "core/test_chat_capacity.py", "api/chat_idempotency_test.py",
        "api/test_admin_dashboard.py", "core/task_protocol_test.py", "api/task_protocol_http_test.py",
        "session/test_storage_quota.py", "core/test_publication_reconcile.py",
        "retrieval/test_translation_gateway.py", "core/test_context_budget.py", "tools/test_runtime_contracts.py",
    ],
    "engine": ["engine/test_gpu_readiness.py", "engine/test_hardware_probe.py",
               "engine/test_embedder_prefix.py"],
    "word": ["core/test_word_documents.py", "research/test_word_tools.py",
             "research/test_word_resource_dependencies.py", "api/test_word_delivery_download.py",
             "prompts/test_word_skill_loading.py"],
}


def child(group: str, xml: Path) -> int:
    # Set the root before modules that capture database paths are imported.
    sys.path.insert(0, str(ROOT / "01_src"))
    with tempfile.TemporaryDirectory(prefix="qaagent-check-", ignore_cleanup_errors=True) as state:
        import src.config as config
        config.PROJECT_ROOT = Path(state)
        import pytest
        import socket
        original_connect = socket.socket.connect
        original_create_connection = socket.create_connection

        def local_address(address):
            return isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1", "localhost"}

        def local_connect(sock, address):
            # Windows asyncio constructs its wakeup socketpair over loopback.
            if sock.family == getattr(socket, "AF_UNIX", None) or local_address(address):
                return original_connect(sock, address)
            raise AssertionError("external network forbidden")

        def local_create_connection(address, *args, **kwargs):
            if not local_address(address):
                raise AssertionError("external network forbidden")
            return original_create_connection(address, *args, **kwargs)

        with patch("src.retrieval.runtime.get_rag", side_effect=AssertionError("live retrieval forbidden")), \
             patch("src.core.llm_pool.get_llm", side_effect=AssertionError("live model forbidden")), \
             patch("socket.create_connection", local_create_connection), \
             patch("socket.socket.connect", local_connect):
            return int(pytest.main([
                "-q", "-p", "no:cacheprovider", "--tb=short", "--show-capture=no",
                f"--junitxml={xml}", *[str(BASE / name) for name in GROUPS[group]],
            ]))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--group", choices=[*GROUPS, "frontend", "release"], action="append")
    parser.add_argument("--child", choices=GROUPS, help=argparse.SUPPRESS)
    parser.add_argument("--xml", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child:
        return child(args.child, args.xml)
    groups = args.group or [*GROUPS, "frontend", "release"]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    out = Path(__file__).resolve().parent / "results" / stamp
    out.mkdir(parents=True, exist_ok=False)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONIOENCODING": "utf-8",
           "PYTHONPATH": str(ROOT / "01_src")}
    results = []
    for group in groups:
        if group == "release":
            command = [sys.executable, "-I", str(ROOT / "engineering/product_evolution/release/test_release.py")]
        elif group == "frontend":
            tests = sorted((ROOT / "01_src/web/tests").glob("*.test.cjs"))
            if not tests:
                raise RuntimeError("No frontend test files found")
            command = ["node", "--test", *map(str, tests)]
        else:
            missing = [name for name in GROUPS[group] if not (BASE / name).is_file()]
            if missing:
                raise RuntimeError(f"Missing {group} test files: {missing}")
            command = [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                       "--child", group, "--xml", str(out / f"{group}.xml")]
        with (out / f"{group}.log").open("w", encoding="utf-8") as log:
            try:
                run = subprocess.run(command, cwd=ROOT, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=600)
                code = run.returncode
            except subprocess.TimeoutExpired:
                log.write("\nRunner timed out after 600 seconds. This group did not pass.\n")
                code = 124
        results.append({"group": group, "exit_code": code})
        print(f"{group}: exit={code}; log={out / (group + '.log')}", flush=True)
    (out / "summary.json").write_text(json.dumps({
        "created_at": stamp, "python": sys.version, "groups": results,
        "limits": ["offline fixtures only", "no browser or Docker runtime acceptance",
                   "no scientific benchmark or live model calls"],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    return int(any(item["exit_code"] != 0 for item in results))


if __name__ == "__main__":
    raise SystemExit(main())
