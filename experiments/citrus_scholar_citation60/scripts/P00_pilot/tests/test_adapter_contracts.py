from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
ADAPTERS = ROOT / "05_baselines" / "adapters"
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ADAPTERS))


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: object) -> bytes:
    data = (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


source_scope = load_module("pilot_source_scope_test", ADAPTERS / "source_scope.py")
adapter_common = load_module(
    "pilot_adapter_common_test",
    ADAPTERS / "adapter_common.py",
)
pilot_runner = load_module(
    "pilot_runner_contract_test",
    SCRIPTS / "P00_pilot" / "20_run_pilot.py",
)
gate_validation = load_module(
    "pilot_gate_validation_test",
    SCRIPTS / "experiment_gate_validation.py",
)
capture_schema = load_module(
    "pilot_capture_schema_test",
    SCRIPTS / "capture_schema.py",
)


def test_source_scope_policy_covers_all_six_strata() -> None:
    local = source_scope.resolve_source_scope("仅本地")
    variety = source_scope.resolve_source_scope("仅查询本地UCR品种库")
    web = source_scope.resolve_source_scope("仅联网")
    fusion = source_scope.resolve_source_scope("自动")
    attachment = source_scope.resolve_source_scope("仅附件")
    negative = source_scope.resolve_source_scope("仅本地")

    assert local.allow_local and not local.allow_web
    assert variety.allow_local and not variety.allow_web
    assert web.allow_web and not web.allow_local
    assert fusion.allow_local and fusion.allow_web and fusion.allow_attachment
    assert attachment.allow_attachment
    assert not attachment.allow_local and not attachment.allow_web
    assert negative.allow_local and not negative.allow_web
    assert source_scope.validate_tool_trajectory(
        attachment,
        [{"tool_name": "local_hybrid_search"}, {"tool_name": "deepseek_search"}],
    ) == [
        "source_scope_forbidden_local_tool:0:local_hybrid_search",
        "source_scope_forbidden_web_tool:1:deepseek_search",
    ]


def test_authenticated_context_is_required_and_shared_for_local_retrieval(
    monkeypatch,
) -> None:
    state: dict[str, object] = {}
    tracing = types.ModuleType("src.core.tracing")

    def set_value(name: str):
        def setter(value):
            state[name] = value

        return setter

    tracing.set_user_id = set_value("user_id")
    tracing.set_user_role = set_value("user_role")
    tracing.set_session_id = set_value("session_id")
    tracing.set_client_request_id = set_value("client_request_id")

    class FakeLocalSearch:
        def invoke(self, arguments):
            assert state["user_id"] == 42
            return (
                "local evidence",
                {
                    "local_status": "hit",
                    "main_results": [
                        {
                            "source_id": "SOURCE-1",
                            "chunk_id": "CHUNK-1",
                            "excerpt": "frozen text",
                        }
                    ],
                },
            )

    deepseek = types.ModuleType("src.tools.deepseek_search_fetch")
    deepseek.local_hybrid_search = FakeLocalSearch()
    src = types.ModuleType("src")
    src.__path__ = []
    core = types.ModuleType("src.core")
    core.__path__ = []
    tools = types.ModuleType("src.tools")
    tools.__path__ = []
    for name, module in (
        ("src", src),
        ("src.core", core),
        ("src.core.tracing", tracing),
        ("src.tools", tools),
        ("src.tools.deepseek_search_fetch", deepseek),
    ):
        monkeypatch.setitem(sys.modules, name, module)

    b1 = load_module("pilot_b1_contract_test", ADAPTERS / "B1.py")
    text, artifact = b1._run_authenticated_local_search(
        auth={"user_id": 42, "role": "user"},
        client_request_id="CLIENT-1",
        query="question",
        families=["corpus_a"],
    )
    assert text == "local evidence"
    assert artifact["main_results"][0]["chunk_id"] == "CHUNK-1"
    assert state["user_id"] is None

    resource_access = load_module(
        "resource_access_contract_test",
        ROOT.parents[1] / "01_src" / "src" / "core" / "resource_access.py",
    )
    denied = resource_access.authorize_resource(
        resource_access.PUBLIC_CORPUS,
        None,
    )
    allowed = resource_access.authorize_resource(
        resource_access.PUBLIC_CORPUS,
        {"user_id": 42, "role": "user"},
        access_level="public",
    )
    assert denied.allowed is False and denied.reason == "unauthenticated"
    assert allowed.allowed is True


def test_experiment_login_is_cached_and_reads_real_auth_me(
    monkeypatch,
) -> None:
    calls = {"login": 0, "me": 0}

    class AuthHandler(BaseHTTPRequestHandler):
        def do_POST(self):
            assert self.path == "/api/v2/auth/login"
            assert self.headers.get("Origin") == base_url
            calls["login"] += 1
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length))
            assert payload["username"] == "experiment-user"
            body = json.dumps(
                {
                    "status": "ok",
                    "user": {"user_id": 42, "role": "user"},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "citrus_auth=test-token; Path=/; HttpOnly")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            assert self.path == "/api/v2/auth/me"
            assert self.headers.get("Cookie") == "citrus_auth=test-token"
            calls["me"] += 1
            body = json.dumps(
                {
                    "user": {
                        "user_id": 42,
                        "role": "user",
                        "username": "experiment-user",
                    }
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), AuthHandler)
    base_url = f"http://127.0.0.1:{server.server_port}"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("TEST_APP_BASE", base_url)
        monkeypatch.setenv("TEST_APP_USERNAME", "experiment-user")
        monkeypatch.setenv("TEST_APP_PASSWORD", "not-persisted")
        adapter_common.clear_experiment_auth_cache()
        config = {
            "experiment_base_url_env": "TEST_APP_BASE",
            "experiment_username_env": "TEST_APP_USERNAME",
            "experiment_password_env": "TEST_APP_PASSWORD",
            "timeout_seconds": 5,
        }
        first = adapter_common.authenticate_experiment_account(
            config,
            user_agent="CitrusScholarExperiment/Test",
        )
        second = adapter_common.authenticate_experiment_account(
            config,
            user_agent="CitrusScholarExperiment/Test",
        )
    finally:
        adapter_common.clear_experiment_auth_cache()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert calls == {"login": 1, "me": 1}
    assert first["user_id"] == second["user_id"] == 42
    assert first["identity_sha256"] == second["identity_sha256"]
    assert first["cookie_persisted"] is False


def test_budget_unknown_and_overrun_are_fail_closed() -> None:
    config = {
        "b1_b2_shared_resource_caps": {
            "max_model_calls_per_run": 2,
            "max_total_input_tokens_per_run": 100,
            "max_total_output_tokens_per_run": 100,
            "max_retrieval_calls_per_run": 2,
            "max_context_tokens_per_run": 100,
            "max_tool_calls_per_run": 2,
            "max_elapsed_seconds_per_run": 10,
        }
    }
    missing = adapter_common.budget_result(
        model_calls=1,
        tokens_in=None,
        tokens_out=None,
        retrieval_calls=0,
        context_tokens=None,
        tool_calls=0,
        elapsed_ms=1,
        config=config,
    )
    assert missing["status"] == "RESOURCE_ACCOUNTING_UNKNOWN"
    status, failure_class, _ = adapter_common.terminal_status_after_gates(
        "completed",
        budget=missing,
    )
    assert status == "failed"
    assert failure_class == "RESOURCE_ACCOUNTING_UNKNOWN"

    overrun = adapter_common.budget_result(
        model_calls=3,
        tokens_in=101,
        tokens_out=2,
        retrieval_calls=1,
        context_tokens=101,
        tool_calls=1,
        elapsed_ms=1,
        config=config,
    )
    status, failure_class, _ = adapter_common.terminal_status_after_gates(
        "completed",
        budget=overrun,
    )
    assert overrun["status"] == "BUDGET_VIOLATION"
    assert status == "failed"
    assert failure_class == "BUDGET_VIOLATION"


def test_b1_provider_response_id_is_not_an_application_request_id() -> None:
    response = {
        "status": "completed",
        "final_answer": "answer",
        "raw_answer": "answer",
        "requested_model": "deepseek-v4.1-flash",
        "response_model": "deepseek-v4.1-flash",
        "response_id": "PROVIDER-RESPONSE-1",
        "provider_response_id": "PROVIDER-RESPONSE-1",
        "server_request_id": "PROVIDER-RESPONSE-1",
        "request_body": {"model": "deepseek-v4.1-flash"},
        "request_sha256": "a" * 64,
        "raw_response": {
            "id": "PROVIDER-RESPONSE-1",
            "model": "deepseek-v4.1-flash",
            "choices": [{"message": {"content": "answer"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
        "raw_response_sha256": "b" * 64,
        "provider_http_status": 200,
        "tool_calls": [],
        "citations": [],
        "artifact_paths": [],
        "artifact_sha256": {},
        "tokens_in": 10,
        "tokens_out": 2,
        "model_calls": 1,
        "total_input_tokens": 10,
        "total_output_tokens": 2,
        "retrieval_calls": 0,
        "context_tokens_used": 10,
        "budget_status": "WITHIN_BUDGET",
        "budget": {
            "status": "WITHIN_BUDGET",
            "violations": [],
            "unknown_metrics": [],
            "caps": {},
        },
        "source_scope_policy": {"canonical": "local"},
        "source_scope_validation": {"status": "PASS", "errors": []},
    }
    errors = capture_schema.validate_capture(response, condition="B1")
    assert "b1_server_request_id_must_be_not_applicable" in errors
    response["server_request_id"] = None
    assert capture_schema.validate_capture(response, condition="B1") == []


def test_b2_capture_index_aggregates_every_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    b2 = load_module("pilot_b2_capture_contract_test", ADAPTERS / "B2.py")
    root = tmp_path / "capture"
    root.mkdir()
    model = "deepseek-v4.1-flash"
    responses = [
        {
            "id": "PROVIDER-1",
            "model": model,
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "TOOL-1",
                                "function": {"name": "local_hybrid_search", "arguments": "{}"},
                            }
                        ],
                    }
                }
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        },
        {
            "id": "PROVIDER-2",
            "model": model,
            "choices": [{"message": {"content": "final answer"}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 30},
        },
    ]
    records = []
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": "question"}],
    }
    for index, response in enumerate(responses, start=1):
        capture_dir = root / f"cap-{index}"
        raw_bytes = write_json(capture_dir / "response.json", response)
        request_bytes = write_json(capture_dir / "request.json", request_body)
        records.append(
            {
                "capture_id": f"cap-{index}",
                "run_id": "PILOT_P01_B2_T1",
                "client_request_id": "CLIENT-1",
                "server_request_id": "SERVER-1",
                "session_id": "SESSION-1",
                "job_id": "JOB-1",
                "provider_response_id": response["id"],
                "requested_model": model,
                "response_model": model,
                "request_sha256": sha(request_bytes),
                "raw_response_sha256": sha(raw_bytes),
                "raw_response_path": f"cap-{index}/response.json",
                "request_body_path": f"cap-{index}/request.json",
                "http_status": 200,
                "status": "CAPTURED",
            }
        )
    index_path = root / "provider_capture" / "index.jsonl"
    index_path.parent.mkdir()
    index_path.write_text(
        "\n".join(json.dumps(row) for row in records) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CITRUS_EXPERIMENT_CAPTURE_INDEX", str(index_path))
    capture, errors = b2._load_capture_index(
        root,
        run_id="PILOT_P01_B2_T1",
        client_request_id="CLIENT-1",
        session_id="SESSION-1",
        job_id="JOB-1",
        expected_answer="final answer",
        expected_model=model,
    )
    assert errors is None
    assert capture is not None
    assert len(capture["run_capture_records"]) == 2
    usage = adapter_common.aggregate_provider_usage(
        [
            row["raw_response_payload"]
            for row in capture["run_capture_records"]
        ]
    )
    assert usage["model_calls"] == 2
    assert usage["tokens_in"] == 300
    assert usage["tokens_out"] == 50


def _valid_negative_evidence() -> dict:
    return {
        "corpus_scope": {"family_ids": ["corpus_a"], "generation": "gen-1"},
        "retrieval_snapshot": {
            "query": "negative query",
            "executed_utc": "2026-11-06T00:00:00Z",
            "result_count": 0,
            "snapshot_sha256": "a" * 64,
        },
        "negative_verification_process": {
            "method": "full-scope retrieval plus manual review",
            "reviewed_by": "expert-hash",
            "reviewed_utc": "2026-11-06T01:00:00Z",
        },
        "expected_refusal_rule": "refuse and state evidence is insufficient",
        "expert_confirmation": {
            "reviewer_id_hash": "b" * 64,
            "reviewed_utc": "2026-11-06T01:00:00Z",
            "decision": "CONFIRMED_UNANSWERABLE",
            "evidence_sha256": "c" * 64,
        },
    }


def test_pilot_negative_evidence_schema_does_not_require_positive_evidence() -> None:
    row = {
        "run_id": "PILOT_PN01_B0_T1",
        "stratum": "N",
        "availability_status": "VERIFIED",
        "reference_evidence_json": "[]",
        "negative_evidence_json": json.dumps(_valid_negative_evidence()),
    }
    assert pilot_runner.validate_materials([row]) == []
    row["negative_evidence_json"] = "{}"
    assert any(
        "negative_evidence_empty" in error
        for error in pilot_runner.validate_materials([row])
    )
    row["negative_evidence_json"] = json.dumps(_valid_negative_evidence())
    row["reference_evidence_json"] = '[{"excerpt":"invented"}]'
    assert any(
        "unanswerable_must_not_have_positive_evidence" in error
        for error in pilot_runner.validate_materials([row])
    )


def test_gate_report_rejects_fake_pass_and_sha_mismatch(tmp_path: Path) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    evidence = b"real tool evidence\n"
    (evidence_root / "tool.json").write_bytes(evidence)
    model_path = tmp_path / "model_config.json"
    model_path.write_text(
        json.dumps(
            {
                "protocol_version": "4.4.2",
                "candidate_model_id": "deepseek-v4.1-flash",
                "allowed_response_models": ["deepseek-v4.1-flash"],
            }
        ),
        encoding="utf-8",
    )
    model_config = json.loads(model_path.read_text(encoding="utf-8"))
    categories = {
        name: {"status": "PASS", "evidence_files": [{"path": "tool.json"}]}
        for name in gate_validation.G10_CATEGORIES
    }
    report = {
        "status": "PASS",
        "artifact": "EXPERIMENT_GATE_REPORT",
        "audit": "G10_TOOL_FACT_EVIDENCE",
        "experiment_id": "citrus_scholar_citation60",
        "protocol_version": "4.4.2",
        "reviewed_commit": "1" * 40,
        "reviewed_tree": "2" * 40,
        "deployment_id": "3" * 64,
        "model_id": "deepseek-v4.1-flash",
        "model_config_sha256": sha(model_path.read_bytes()),
        "stage1_manifest_sha256": "4" * 64,
        "evidence_root": str(evidence_root),
        "evidence_files": [{"path": "tool.json", "sha256": sha(evidence)}],
        "tool_fact_categories": categories,
    }
    report_path = tmp_path / "g10.json"
    write_json(report_path, report)
    report_sha = sha(report_path.read_bytes())
    assert gate_validation.validate_gate_report(
        report_path,
        expected_report_sha256=report_sha,
        expected_audit="G10_TOOL_FACT_EVIDENCE",
        reviewed_commit="1" * 40,
        reviewed_tree="2" * 40,
        model_config=model_config,
        model_config_path=model_path,
    ) == []

    assert "gate_report_sha_mismatch" in gate_validation.validate_gate_report(
        report_path,
        expected_report_sha256="0" * 64,
        expected_audit="G10_TOOL_FACT_EVIDENCE",
        reviewed_commit="1" * 40,
        reviewed_tree="2" * 40,
        model_config=model_config,
        model_config_path=model_path,
    )
    assert "gate_report_reviewed_tree_mismatch" in (
        gate_validation.validate_gate_report(
            report_path,
            expected_report_sha256=report_sha,
            expected_audit="G10_TOOL_FACT_EVIDENCE",
            reviewed_commit="1" * 40,
            reviewed_tree="9" * 40,
            model_config=model_config,
            model_config_path=model_path,
        )
    )
    wrong_identity = dict(report)
    wrong_identity["experiment_id"] = "wrong_dataset"
    identity_path = tmp_path / "wrong_identity.json"
    write_json(identity_path, wrong_identity)
    identity_errors = gate_validation.validate_gate_report(
        identity_path,
        expected_report_sha256=sha(identity_path.read_bytes()),
        expected_audit="G10_TOOL_FACT_EVIDENCE",
        reviewed_commit="1" * 40,
        reviewed_tree="2" * 40,
        model_config=model_config,
        model_config_path=model_path,
    )
    assert "gate_report_experiment_id_mismatch" in identity_errors
    missing_evidence = dict(report)
    missing_evidence["evidence_files"] = [
        {"path": "missing.json", "sha256": "d" * 64}
    ]
    missing_path = tmp_path / "missing_evidence.json"
    write_json(missing_path, missing_evidence)
    missing_errors = gate_validation.validate_gate_report(
        missing_path,
        expected_report_sha256=sha(missing_path.read_bytes()),
        expected_audit="G10_TOOL_FACT_EVIDENCE",
        reviewed_commit="1" * 40,
        reviewed_tree="2" * 40,
        model_config=model_config,
        model_config_path=model_path,
    )
    assert "gate_evidence_file_missing:0" in missing_errors
    fake_path = tmp_path / "fake.json"
    write_json(fake_path, {"status": "PASS"})
    fake_errors = gate_validation.validate_gate_report(
        fake_path,
        expected_report_sha256=sha(fake_path.read_bytes()),
        expected_audit="G10_TOOL_FACT_EVIDENCE",
        reviewed_commit="1" * 40,
        reviewed_tree="2" * 40,
        model_config=model_config,
        model_config_path=model_path,
    )
    assert "gate_report_artifact_invalid" in fake_errors
    assert "gate_evidence_files_missing" in fake_errors
