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
boundary = load_module(
    "agent_input_boundary",
    SCRIPT_DIR / "20_audit_agent_input_boundary.py",
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
        "attachment": "",
        "research_constraints": ["Do not invent values."],
        "tool_permissions": ["LOCAL"],
        "model_config": {
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
        "session_mode": "NEW",
        "conversation_history": [],
        "question_count": 1,
    }


def valid_trace() -> dict:
    return {
        "request_id": "REQ_001",
        "run_id": "QA_L01_B1_T1",
        "session_id": "SESSION_QA_L01_B1_T1",
        "question_id": "L01",
        "condition": "B1",
        "request_sha256": "request-sha",
        "tool_events": [
            {
                "tool_group": "LOCAL",
                "arguments": {"query": "recorded result"},
                "result": {
                    "chunk_id": "target-is-allowed-after-normal-retrieval",
                    "excerpt": "Platform-retrieved evidence",
                },
            }
        ],
    }


def valid_probe_plan() -> dict:
    return {
        "schema_version": "1.0",
        "probe_version": "4.3.3",
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
    }

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["path"] = self.path
            state["authorization"] = self.headers.get("Authorization")
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


def test_agent_boundary_accepts_normal_platform_retrieval(monkeypatch):
    errors = boundary.audit_request(valid_request())
    errors.extend(
        boundary.audit_trace(
            valid_trace(),
            valid_request(),
            request_sha256="request-sha",
            denied_roots=[],
            allowed_tool_groups={"LOCAL"},
        )
    )
    assert errors == []


def test_agent_boundary_rejects_target_locator_and_hidden_text():
    request = valid_request()
    request["target_chunk_id"] = "chunk-secret-001"
    errors = boundary.audit_request(request)
    assert "forbidden_request_field:target_chunk_id" in errors

    request = valid_request()
    request["question"] += " target phrase from hidden evidence"
    errors = boundary.audit_request(
        request,
        hidden_tokens={"target phrase from hidden evidence"},
    )
    assert "hidden_reference_token_in_request" in errors


def test_agent_boundary_rejects_reused_session_and_multiple_questions():
    request = valid_request()
    request["session_mode"] = "REUSED"
    request["question_count"] = 2
    errors = boundary.audit_request(request)
    assert "session_mode_must_be_NEW" in errors
    assert "question_count_must_equal_one" in errors


def test_agent_boundary_rejects_tool_access_to_l1_and_scoring(tmp_path):
    l1_root = tmp_path / "l1"
    trace = valid_trace()
    trace["tool_events"][0]["arguments"] = {
        "path": str(l1_root / "07_scoring/expert/scores.csv")
    }
    errors = boundary.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        denied_roots=[l1_root],
        allowed_tool_groups={"LOCAL"},
    )
    assert any("tool_path_inside_denied_root" in error for error in errors)
    assert any("tool_path_matches_protected_area" in error for error in errors)


def test_agent_boundary_rejects_attachment_path_to_protected_root(tmp_path):
    l1_root = tmp_path / "l1"
    request = valid_request()
    request["attachment"] = str(l1_root / "07_scoring/expert/scores.csv")
    errors = boundary.audit_request(request, denied_roots=[l1_root])
    assert "request_references_denied_root" in errors
    assert "request_references_protected_area" in errors


def test_agent_boundary_requires_request_and_session_sha_binding():
    trace = valid_trace()
    trace["request_sha256"] = "different"
    trace["session_id"] = "different"
    trace["tool_events"][0]["condition"] = "B0"
    errors = boundary.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        denied_roots=[],
        allowed_tool_groups={"LOCAL"},
    )
    assert "trace_request_sha256_mismatch" in errors
    assert "trace_session_id_mismatch" in errors
    assert any("trace_nested_identity_mismatch" in error for error in errors)


def test_agent_boundary_rejects_extra_model_generation_settings():
    errors = boundary.audit_request(
        valid_request(),
        expected_model_config={
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
    )
    assert errors == []
    request = valid_request()
    request["model_config"]["top_p"] = 0.5
    errors = boundary.audit_request(
        request,
        expected_model_config={
            "model": "deepseek-v4.1-flash",
            "temperature": 0,
        },
    )
    assert "model_config_differs_from_frozen" in errors


def test_boundary_output_is_write_once(tmp_path):
    path = tmp_path / "audit.json"
    boundary.write_new_json(path, {"status": "PASS"})
    with pytest.raises(FileExistsError):
        boundary.write_new_json(path, {"status": "BLOCKED"})
