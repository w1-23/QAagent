from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


HERE = Path(__file__).resolve()
SCRIPT_DIR = HERE.parents[1]
SCRIPTS = HERE.parents[2]
EXPERIMENT_ROOT = SCRIPTS.parent
REPO_ROOT = EXPERIMENT_ROOT.parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


stage0 = load_module("stage0_audit", SCRIPT_DIR / "00_repository_asset_audit.py")
probe = load_module("server_api_probe", SCRIPT_DIR / "10_server_api_probe.py")
stage2 = load_module(
    "agent_execution_evidence",
    SCRIPT_DIR / "20_audit_agent_execution_evidence.py",
)
transport = load_module(
    "transport_preflight",
    SCRIPT_DIR / "11_transport_preflight.py",
)
history_audit = load_module(
    "git_history_audit",
    SCRIPT_DIR / "01_git_history_audit.py",
)


def valid_request() -> dict:
    return {
        "request_id": "REQ_001",
        "run_id": "QA_L01_B1_T1",
        "session_id": "SESSION_QA_L01_B1_T1",
        "question_id": "L01",
        "condition": "B1",
        "question": "Only use the local corpus and identify the recorded result.",
        "system_prompt": "Use the permitted tools and cite evidence.",
        "source_scope": "local",
        "attachments": [],
        "research_constraints": ["Do not invent values."],
        "tool_permissions": ["LOCAL"],
        "model_config": {
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
        "session_mode": "NEW",
        "conversation_history": [],
        "question_count": 1,
        "execution_mode": "SINGLE_QUESTION",
        "workflow_id": "",
        "started_utc": "2026-11-05T00:00:00Z",
    }


def valid_trace() -> dict:
    return {
        "request_id": "REQ_001",
        "run_id": "QA_L01_B1_T1",
        "session_id": "SESSION_QA_L01_B1_T1",
        "question_id": "L01",
        "condition": "B1",
        "request_sha256": "request-sha",
        "provider": "Alibaba Cloud Bailian",
        "requested_model": "deepseek-v4.1-flash",
        "actual_model": "deepseek-v4.1-flash",
        "response_id": "resp-001",
        "started_utc": "2026-11-05T00:00:01Z",
        "finished_utc": "2026-11-05T00:00:02Z",
        "status": "completed",
        "raw_answer": "The platform-retrieved answer.",
        "final_answer": "The platform-retrieved answer.",
        "persisted_answer": "The platform-retrieved answer.",
        "answer_source": "AGENT",
        "sse_events": [
            {"sequence": 1, "event": "answer_start"},
            {"sequence": 2, "event": "text"},
            {"sequence": 3, "event": "done"},
        ],
        "retrieval_events": [
            {
                "sequence": 1,
                "query": "recorded result",
                "results": [
                    {
                        "corpus_id": "cqa_literature",
                        "source_id": "paper-001",
                        "chunk_id": "target-is-allowed-after-normal-retrieval",
                        "rank": 1,
                    }
                ],
            }
        ],
        "tool_events": [
            {
                "tool_name": "search_local_corpus",
                "tool_group": "LOCAL",
                "arguments": {"query": "recorded result"},
                "status": "completed",
                "result": {
                    "chunk_id": "target-is-allowed-after-normal-retrieval",
                    "excerpt": "Platform-retrieved evidence",
                    "chunk_text": "Normal corpus text is allowed here.",
                },
            }
        ],
        "citations": [],
        "artifact_paths": [],
        "artifact_sha256": {},
        "backend_log_refs": [
            {"reference": "backend/api.log#L10", "sha256": "log-sha"}
        ],
        "usage": {"input_tokens": 10, "output_tokens": 4},
        "cost": None,
        "list_cost": None,
        "allowance": None,
        "first_token_ms": 100,
        "elapsed_ms": 1000,
        "errors": [],
        "retry_attempts": [],
        "previous_session_ids": [],
    }


def valid_probe_plan() -> dict:
    return {
        "schema_version": "1.0",
        "probe_version": "4.4.0",
        "status": "READY",
        "execution_authorized": True,
        "review_status": "APPROVED_FOR_EXECUTION",
        "probes": [
            {"id": "server_probe", "kind": "local_resources", "required": True}
        ],
    }


@pytest.fixture()
def json_server():
    state = {
        "authorization": None,
        "password": None,
        "path": None,
        "rate_requests": 0,
        "cookie": None,
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["path"] = self.path
            state["authorization"] = self.headers.get("Authorization")
            state["cookie"] = self.headers.get("Cookie")
            if self.path == "/rate":
                state["rate_requests"] += 1
                status = 429 if state["rate_requests"] == 1 else 200
                payload = json.dumps({"status": "throttled" if status == 429 else "ok"}).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if status == 429:
                    self.send_header("Retry-After", "1")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            payload = json.dumps({"status": "ok"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length)
            body = json.loads(raw or b"{}")
            state["path"] = self.path
            state["authorization"] = self.headers.get("Authorization")
            state["cookie"] = self.headers.get("Cookie")
            state["password"] = body.get("password")
            payload = json.dumps(
                {
                    "model": body.get("model", "test-model"),
                    "password": body.get("password"),
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            if self.path in {"/login", "/api/v2/auth/login"}:
                self.send_header(
                    "Set-Cookie",
                    "citrus_auth=test-session-token; HttpOnly; Path=/",
                )
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, format, *args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", state
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_stage0_never_reports_unchecked_l1_as_verified():
    assets, blockers = stage0.build_asset_report(
        EXPERIMENT_ROOT,
        REPO_ROOT,
        None,
    )
    assert assets["qa"]["questions"] == 60
    assert assets["core_run_plan"]["qa_rows"] == 180
    assert assets["l1_manifest"]["verification_state"] == (
        "NOT_RUN_L1_ROOT_NOT_PROVIDED"
    )
    assert assets["l1_manifest"]["execution_prerequisite"] is False
    assert assets["l1_manifest"]["verified_assets"] == 0
    assert blockers == []


def test_stage0_missing_l1_asset_fails_closed(tmp_path):
    manifest = json.loads(
        (
            EXPERIMENT_ROOT
            / "12_reproducibility/L1_CONTROLLED_ASSET_MANIFEST.json"
        ).read_text(encoding="utf-8")
    )
    rows, blockers = stage0.verify_l1_assets(tmp_path / "empty-l1", manifest)
    assert len(rows) == 16
    assert len(blockers) == 16
    assert all(row["verification"] == "MISSING" for row in rows)


def test_stage0_lineage_keeps_hidden_scoring_out_of_agent():
    lineage = stage0.build_lineage(REPO_ROOT, EXPERIMENT_ROOT)
    scoring = [
        row
        for row in lineage["entries"]
        if row["path"].startswith("07_scoring/")
    ]
    assert scoring
    assert all(row["agent_visible"] is False for row in scoring)
    assert all(
        row["role"]
        in {
            "HIDDEN_SCORING_REFERENCE_OR_TEMPLATE",
            "PROTECTED_REFERENCE_OR_LOCATOR",
        }
        for row in scoring
    )


def test_stage0_visibility_checks_fail_closed():
    assert stage0.visibility_blockers(
        False,
        require_private=False,
        require_public=True,
    ) == []
    assert stage0.visibility_blockers(
        True,
        require_private=False,
        require_public=True,
    ) == ["github_repository_not_public"]
    assert stage0.visibility_blockers(
        None,
        require_private=False,
        require_public=True,
    ) == ["github_repository_not_public"]
    assert stage0.visibility_blockers(
        False,
        require_private=True,
        require_public=False,
    ) == ["github_repository_not_private"]


def test_transport_endpoint_hash_is_stable_and_redacted():
    value = transport.endpoint_sha256("example.invalid", 28022)
    assert len(value) == 64
    assert value == transport.endpoint_sha256("example.invalid", 28022)
    assert "example.invalid" not in value
    assert "28022" not in value


def test_transport_report_blocks_failed_ssh_banner_and_health(monkeypatch):
    monkeypatch.setattr(
        transport,
        "tcp_connect",
        lambda host, port, timeout: {"status": "PASS", "elapsed_ms": 1},
    )
    monkeypatch.setattr(
        transport,
        "ssh_banner",
        lambda host, port, timeout: {
            "status": "BLOCKED",
            "blockers": ["ssh_banner_transport_error"],
        },
    )
    monkeypatch.setattr(
        transport,
        "http_health",
        lambda host, port, timeout: {
            "status": "BLOCKED",
            "blockers": ["http_health_transport_error"],
        },
    )
    report = transport.build_report(
        "example.invalid",
        ssh_port=2200,
        http_port=8000,
        timeout=0.1,
    )
    assert report["status"] == "BLOCKED"
    assert report["target_recorded"] is False
    assert report["credentials_used"] is False
    assert "example.invalid" not in json.dumps(report)
    assert report["blockers"] == [
        "experiment_http:http_health_transport_error",
        "ssh:ssh_banner_transport_error",
    ]


def test_history_audit_separates_current_tree_from_legacy_objects():
    objects = [
        ("oid-1", "experiments/protected/source.jsonl"),
        ("oid-2", "experiments/current/README.md"),
    ]
    rows = history_audit.protected_history_rows(
        objects,
        {"experiments/protected/source.jsonl"},
    )
    assert rows == [
        {
            "path": "experiments/protected/source.jsonl",
            "reachable_object_count": 1,
        }
    ]


def test_history_audit_prefixes_manifest_paths_with_experiment_root():
    manifest = {
        "assets": [
            {
                "former_public_path": "03_benchmarks/protected/source.jsonl",
                "remove_from_public_tree": True,
            },
            {
                "former_public_path": "07_scoring/public/template.csv",
                "remove_from_public_tree": False,
            },
        ]
    }
    assert history_audit.repo_protected_paths(
        manifest,
        "experiments/citrus_scholar_citation60",
    ) == {
        "experiments/citrus_scholar_citation60/03_benchmarks/protected/source.jsonl"
    }


def test_probe_plan_and_authorization_are_fail_closed():
    plan = valid_probe_plan()
    assert probe.plan_validation_errors(plan) == []
    assert probe.execution_authorization_errors(plan) == []
    plan.update(
        {
            "status": "UNCONFIGURED",
            "execution_authorized": False,
            "review_status": "AWAITING_EXTERNAL_GPT_REVIEW",
        }
    )
    assert probe.execution_authorization_errors(plan) == [
        "status_not_READY",
        "execution_authorized_not_true",
        "review_status_not_approved",
    ]


def test_checked_in_probe_plan_is_structurally_valid_but_not_authorized():
    plan = json.loads(
        (
            EXPERIMENT_ROOT / "01_preflight/SERVER_API_PROBE_PLAN.json"
        ).read_text(encoding="utf-8")
    )
    assert probe.plan_validation_errors(plan) == []
    assert probe.execution_authorization_errors(plan) == [
        "status_not_READY",
        "execution_authorized_not_true",
        "review_status_not_approved",
    ]


def test_probe_manifest_hashes_every_attempt_file(tmp_path):
    output = tmp_path / "probe-output"
    manifest = probe.execute_plan(
        valid_probe_plan(),
        output_dir=output,
        timeout_seconds=5,
        allow_rate_limit=False,
    )
    assert manifest["status"] == "PASS"
    assert set(manifest["attempt_files"]) == {"attempts/server_probe.json"}
    record = manifest["attempt_files"]["attempts/server_probe.json"]
    assert record["bytes"] > 0
    assert len(record["sha256"]) == 64


def test_http_probe_archives_no_password_or_authorization(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, state = json_server
    secret = "probe-password-not-for-evidence"
    token = "probe-token-not-for-evidence"
    monkeypatch.setenv("PROBE_BASE_URL", base_url)
    monkeypatch.setenv("PROBE_PASSWORD", secret)
    monkeypatch.setenv("PROBE_TOKEN", token)
    probe_definition = {
        "id": "registration_probe",
        "kind": "http",
        "base_url_env": "PROBE_BASE_URL",
        "method": "POST",
        "path_candidates": ["/register"],
        "body_template": {
            "username": "fixture-user",
            "password": "${ENV:PROBE_PASSWORD}",
        },
        "auth": {"scheme": "Bearer", "token_env": "PROBE_TOKEN"},
        "success_statuses": [200],
    }
    result = probe.execute_http_probe(
        probe_definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    attempt_path = tmp_path / "registration_probe_register.json"
    archived = attempt_path.read_text(encoding="utf-8")
    assert result["status"] == "PASS"
    assert state["path"] == "/register"
    assert state["password"] == secret
    assert state["authorization"] == f"Bearer {token}"
    assert secret not in archived
    assert token not in archived
    assert "Authorization" not in archived
    assert "[REDACTED]" in archived


def test_auth_flow_retains_session_cookie_only_in_memory(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, state = json_server
    monkeypatch.setenv("AUTH_BASE_URL", base_url)
    monkeypatch.setenv("AUTH_USERNAME", "fixture-user")
    monkeypatch.setenv("AUTH_PASSWORD", "fixture-password")
    definition = {
        "id": "account_auth_flow",
        "kind": "auth_flow",
        "base_url_env": "AUTH_BASE_URL",
        "register": {
            "path_candidates": ["/register"],
            "method": "POST",
            "body_template": {
                "username": "${ENV:AUTH_USERNAME}",
                "password": "${ENV:AUTH_PASSWORD}",
            },
            "success_statuses": [200, 201, 409],
        },
        "login": {
            "path_candidates": ["/login"],
            "method": "POST",
            "body_template": {
                "username": "${ENV:AUTH_USERNAME}",
                "password": "${ENV:AUTH_PASSWORD}",
            },
            "success_statuses": [200],
        },
        "verify": {
            "path_candidates": ["/me"],
            "method": "GET",
            "success_statuses": [200],
        },
    }
    result = probe.execute_auth_flow_probe(
        definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    assert result["status"] == "PASS"
    assert result["session_cookie_observed"] is True
    assert state["cookie"] == "citrus_auth=test-session-token"
    recorded = "\n".join(
        path.read_text(encoding="utf-8") for path in tmp_path.glob("*.json")
    )
    assert "test-session-token" not in recorded
    assert "fixture-password" not in recorded


def test_provider_probe_rejects_model_identity_mismatch(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("PROVIDER_BASE_URL", base_url)
    monkeypatch.setenv("PROVIDER_KEY", "provider-secret-value")
    definition = {
        "id": "go_api_probe",
        "kind": "provider_http",
        "base_url_env": "PROVIDER_BASE_URL",
        "api_key_env": "PROVIDER_KEY",
        "path_candidates": ["/chat/completions"],
        "calls": [
            {
                "role": "judge_1",
                "model_id": "glm-5.3-flash",
                "allowed_response_models": ["different-model"],
                "body_template": {
                    "messages": [{"role": "user", "content": "Return OK."}]
                },
            }
        ],
    }
    result = probe.execute_provider_probe(
        definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    assert result["status"] == "BLOCKED"
    assert result["models"][0]["model_identity"] == "MISMATCH"
    assert result["models"][0]["usage"] == {
        "input_tokens": 1,
        "output_tokens": 1,
    }


def test_provider_probe_blocks_missing_body_environment_value(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("PROVIDER_BASE_URL", base_url)
    monkeypatch.setenv("PROVIDER_KEY", "provider-secret-value")
    monkeypatch.delenv("MISSING_BODY_VALUE", raising=False)
    definition = {
        "id": "provider_missing_body_env",
        "kind": "provider_http",
        "base_url_env": "PROVIDER_BASE_URL",
        "api_key_env": "PROVIDER_KEY",
        "path_candidates": ["/chat/completions"],
        "calls": [
            {
                "role": "judge_1",
                "model_id": "glm-5.3-flash",
                "allowed_response_models": ["glm-5.3-flash"],
                "body_template": {
                    "messages": [
                        {
                            "role": "user",
                            "content": "${ENV:MISSING_BODY_VALUE}",
                        }
                    ]
                },
            }
        ],
    }
    result = probe.execute_provider_probe(
        definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    assert result["status"] == "BLOCKED"
    assert "missing_env:MISSING_BODY_VALUE" in result["models"][0]["errors"]


def test_rate_limit_probe_retains_each_attempt_and_retry_after(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("RATE_BASE_URL", base_url)
    definition = {
        "id": "rate_limit_probe",
        "kind": "rate_limit",
        "base_url_env": "RATE_BASE_URL",
        "path_candidates": ["/rate"],
        "method": "GET",
        "max_requests": 2,
        "interval_seconds": 0,
        "expect_throttle": True,
        "success_statuses": [200],
        "throttle_statuses": [429],
    }
    result = probe.execute_rate_limit_probe(
        definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    assert result["status"] == "PASS"
    assert result["throttle_observed"] is True
    assert result["observed_http_statuses"] == [429, 200]
    assert result["retry_after_headers"] == ["1"]
    assert (tmp_path / "rate_limit_probe_attempt_01.json").is_file()
    assert (tmp_path / "rate_limit_probe_attempt_02.json").is_file()


def test_env_probe_checks_permissions_without_reading_content(tmp_path):
    env_file = tmp_path / "experiment.env"
    env_file.write_text("SECRET_SENTINEL=do-not-read\n", encoding="utf-8")
    env_file.chmod(0o600)
    if os.name == "posix":
        tmp_path.chmod(0o700)
    result = probe.env_presence(
        {
            "variables": [],
            "environment_file_env": "TEST_ENV_FILE",
        }
    )
    assert "SECRET_SENTINEL" not in json.dumps(result)
    assert result["environment_file"]["content_read"] is False


def test_stage2_accepts_real_corpus_retrieval_and_chunk_text():
    request = valid_request()
    trace = valid_trace()
    errors = stage2.audit_request(request)
    errors.extend(
        stage2.audit_trace(
            trace,
            request,
            request_sha256="request-sha",
            required_tool_groups={"LOCAL"},
            require_corpus_access=True,
        )
    )
    assert errors == []


def test_stage2_rejects_scoring_answer_and_other_condition_material():
    request = valid_request()
    request["judge_score"] = 1
    errors = stage2.audit_request(request)
    assert "forbidden_request_field:judge_score" in errors

    trace = valid_trace()
    trace["request"] = {"other_condition_answer": "B0 answer"}
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        require_corpus_access=True,
    )
    assert any("forbidden_trace_field" in error for error in errors)


def test_stage2_rejects_reused_session_and_multiple_questions():
    request = valid_request()
    request["session_mode"] = "REUSED"
    request["question_count"] = 2
    errors = stage2.audit_request(request)
    assert "session_mode_must_be_NEW" in errors
    assert "question_count_must_equal_one" in errors


def test_stage2_allows_citation_corpus_but_blocks_scoring_control_material():
    request = valid_request()
    trace = valid_trace()
    trace["tool_events"][0]["arguments"] = {
        "path": "/srv/citrus/data/03_benchmarks/citation60/chunks_FROZEN.jsonl"
    }
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert not any("control_material" in error for error in errors)

    trace = valid_trace()
    trace["tool_events"][0]["arguments"] = {
        "path": "/srv/citrus/07_scoring/expert/scores.csv"
    }
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert any("control_material" in error for error in errors)


def test_stage2_requires_request_session_and_answer_binding():
    trace = valid_trace()
    trace["request_sha256"] = "different"
    trace["session_id"] = "different"
    trace["tool_events"][0]["condition"] = "B0"
    trace["persisted_answer"] = "different"
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert "trace_request_sha256_mismatch" in errors
    assert "trace_session_id_mismatch" in errors
    assert "persisted_answer_differs_from_final" in errors
    assert any("trace_nested_identity_mismatch" in error for error in errors)


def test_stage2_allows_raw_answer_to_be_finalized_before_publication():
    trace = valid_trace()
    trace["raw_answer"] = "Raw Agent text before verified download-link append."
    trace["final_answer"] = "Raw Agent text before verified download-link append.\n[download](verified)"
    trace["persisted_answer"] = trace["final_answer"]
    trace["sse_events"][-1]["answer"] = trace["final_answer"]
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert errors == []


def test_stage2_rejects_done_answer_that_differs_from_persisted_final():
    trace = valid_trace()
    trace["sse_events"][-1]["answer"] = "Different displayed final answer."
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert "sse_done_answer_differs_from_final" in errors


def test_stage2_requires_sha256_for_each_artifact():
    trace = valid_trace()
    trace["artifact_paths"] = ["artifacts/review.xlsx"]
    trace["artifact_sha256"] = {}
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert "artifact_sha256_missing_or_invalid:0" in errors

    trace["artifact_sha256"] = {"artifacts/review.xlsx": "a" * 64}
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert errors == []


def test_stage2_rejects_extra_model_generation_settings():
    errors = stage2.audit_request(
        valid_request(),
        expected_model_config={
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
    )
    assert errors == []
    request = valid_request()
    request["model_config"]["top_p"] = 0.5
    errors = stage2.audit_request(
        request,
        expected_model_config={
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
    )
    assert "model_config_differs_from_frozen" in errors


def test_stage2_requires_corpus_and_backend_evidence_when_requested():
    trace = valid_trace()
    trace["retrieval_events"] = []
    trace["backend_log_refs"] = []
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert "normal_corpus_retrieval_not_observed" in errors
    assert "backend_log_refs_empty" in errors


def test_boundary_output_is_write_once(tmp_path):
    path = tmp_path / "audit.json"
    stage2.write_new_json(path, {"status": "PASS"})
    with pytest.raises(FileExistsError):
        stage2.write_new_json(path, {"status": "BLOCKED"})
