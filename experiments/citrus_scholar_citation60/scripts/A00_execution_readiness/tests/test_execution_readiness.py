from __future__ import annotations

import importlib.util
import hashlib
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
        "reviewed_commit": "1" * 40,
        "reviewed_tree": "2" * 40,
        "status": "READY",
        "execution_authorized": True,
        "review_status": "APPROVED_FOR_EXECUTION",
        "execution_guards": {
            "required": True,
            "experiment_origin_allowlist": ["https://experiment.invalid"],
            "stage0_report_sha256": "3" * 64,
            "checkout_root_env": "CITRUS_GIT_CHECKOUT_ROOT",
            "rate_limit_policy": "DEFERRED unless an isolated endpoint and budget are approved.",
        },
        "probes": [
            {"id": "server_probe", "kind": "local_resources", "required": True}
        ],
    }


def write_reference(
    root: Path,
    relative: str,
    payload,
) -> str:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        data = payload.encode("utf-8")
    else:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    path.write_bytes(data)
    return hashlib.sha256(data).hexdigest()


def materialize_trace_evidence(root: Path, trace: dict) -> None:
    trace["raw_response_ref"] = "raw/agent_response.json"
    trace["raw_response_sha256"] = write_reference(
        root,
        trace["raw_response_ref"],
        {
            "id": trace["response_id"],
            "model": trace["actual_model"],
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": trace["raw_answer"],
                    }
                }
            ],
            "usage": trace.get("usage"),
        },
    )
    trace["sse_ref"] = "raw/sse_events.jsonl"
    trace["sse_sha256"] = write_reference(
        root,
        trace["sse_ref"],
        trace["sse_events"],
    )
    trace["retrieval_ref"] = "raw/retrieval_events.jsonl"
    trace["retrieval_sha256"] = write_reference(
        root,
        trace["retrieval_ref"],
        trace["retrieval_events"],
    )
    event = trace["tool_events"][0]
    event["result_ref"] = "raw/tool_results/001.json"
    event["result_sha256"] = write_reference(
        root,
        event["result_ref"],
        event["result"],
    )
    backend_path = "logs/backend_api.log"
    backend_sha = write_reference(
        root,
        backend_path,
        (
            f"request_id={trace['request_id']} "
            f"run_id={trace['run_id']} "
            f"response_id={trace['response_id']}\n"
        ),
    )
    trace["backend_log_refs"] = [
        {
            "reference": f"{backend_path}#L1",
            "path": backend_path,
            "sha256": backend_sha,
        }
    ]


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
            message_text = json.dumps(body.get("messages", []), ensure_ascii=False)
            content = '{"ok":true}' if "JSON" in message_text else "OK"
            payload = json.dumps(
                {
                    "id": "response-fixture-001",
                    "model": body.get("model", "test-model"),
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": content,
                            }
                        }
                    ],
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
        "experiment_origin_allowlist_invalid",
        "stage0_report_sha256_not_frozen",
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


def test_provider_probe_retains_identity_for_all_three_judges(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("PROVIDER_BASE_URL", base_url)
    monkeypatch.setenv("PROVIDER_KEY", "provider-secret-value")
    models = {
        "judge_1": "glm-5.3-flash",
        "judge_2": "mimo-v2.6-flash",
        "judge_3": "minimax-m3.1-flash",
    }
    definition = {
        "id": "go_api_probe",
        "kind": "provider_http",
        "base_url_env": "PROVIDER_BASE_URL",
        "api_key_env": "PROVIDER_KEY",
        "path_candidates": ["/chat/completions"],
        "calls": [
            {
                "role": role,
                "model_id": model_id,
                "allowed_response_models": [model_id],
                "body_template": {
                    "messages": [
                        {"role": "user", "content": f"Return OK for {role}."}
                    ]
                },
            }
            for role, model_id in models.items()
        ],
    }
    result = probe.execute_provider_probe(
        definition,
        timeout_seconds=5,
        attempt_root=tmp_path,
    )
    assert result["status"] == "PASS"
    assert len(result["models"]) == 3
    for model_result, (role, model_id) in zip(
        result["models"],
        models.items(),
        strict=True,
    ):
        assert model_result["role"] == role
        assert model_result["requested_model"] == model_id
        assert model_result["actual_model"] == model_id
        assert model_result["model_identity"] == "MATCH"
        assert model_result["request_id"] == "response-fixture-001"
        assert model_result["request_sha256"]
        attempt_path = (
            tmp_path
            / f"go_api_probe_{role}_attempt_{model_result['attempt']:02d}.json"
        )
        assert attempt_path.is_file()
        archived = json.loads(attempt_path.read_text(encoding="utf-8"))
        assert archived["role"] == role
        assert archived["requested_model"] == model_id
        assert archived["actual_model"] == model_id
        assert archived["request_id"] == "response-fixture-001"
        assert archived["request_sha256"] == model_result["request_sha256"]
        assert "provider-secret-value" not in attempt_path.read_text(
            encoding="utf-8"
        )


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


def test_stage2_accepts_real_corpus_retrieval_and_chunk_text(tmp_path):
    request = valid_request()
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    errors = stage2.audit_request(request)
    errors.extend(
        stage2.audit_trace(
            trace,
            request,
            request_sha256="request-sha",
            evidence_root=tmp_path,
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


def test_stage2_allows_raw_answer_to_be_finalized_before_publication(tmp_path):
    trace = valid_trace()
    trace["raw_answer"] = "Raw Agent text before verified download-link append."
    trace["final_answer"] = "Raw Agent text before verified download-link append.\n[download](verified)"
    trace["persisted_answer"] = trace["final_answer"]
    trace["sse_events"][-1]["answer"] = trace["final_answer"]
    materialize_trace_evidence(tmp_path, trace)
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
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


def test_stage2_requires_sha256_for_each_artifact(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["artifact_paths"] = ["artifacts/review.xlsx"]
    trace["artifact_sha256"] = {}
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
        required_tool_groups={"LOCAL"},
        require_corpus_access=True,
    )
    assert "artifact_sha256_missing_or_invalid:0" in errors

    artifact_sha = write_reference(
        tmp_path,
        "artifacts/review.xlsx",
        "real artifact bytes",
    )
    trace["artifact_sha256"] = {"artifacts/review.xlsx": artifact_sha}
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
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


def expected_model_config() -> dict:
    return {
        "provider_id": "aliyun_bailian",
        "provider_display_name": "Alibaba Cloud Bailian",
        "agent_provider": "Alibaba Cloud Bailian",
        "candidate_model_id": "deepseek-v4.1-flash",
        "allowed_response_models": ["deepseek-v4.1-flash"],
    }


def valid_stage1_manifest() -> dict:
    return {
        "status": "PASS",
        "execution_guard": {
            "tool_names": [
                "search_local_corpus",
                *[f"tool_{index}" for index in range(16)],
            ],
            "tool_registration_evidence_sha256": "c" * 64,
        },
        "runtime_app_config": {
            "model": {
                "provider_id": "aliyun_bailian",
                "main": "deepseek-v4.1-flash",
            }
        },
        "provider_model_identity": {
            "agent_api_probe": [
                {
                    "role": "agent",
                    "requested_model": "deepseek-v4.1-flash",
                    "actual_model": "deepseek-v4.1-flash",
                    "status": "PASS",
                }
            ]
        },
    }


def valid_stage0_report(plan: dict, evidence_root: Path) -> dict:
    def evidence(relative: str, text: str) -> dict:
        sha = write_reference(evidence_root, relative, text)
        return {
            "path": str((evidence_root / relative).resolve()),
            "sha256": sha,
        }

    deployment_manifest = evidence(
        "deployment_manifest.json",
        json.dumps(
            {
                "deployment_id": "d" * 64,
                "git_commit": plan["reviewed_commit"],
                "git_tree": plan["reviewed_tree"],
                "files": [
                    {"path": "app.py", "sha256": "a" * 64},
                    {"path": "static/app.js", "sha256": "b" * 64},
                ],
            }
        ),
    )
    production_identity = evidence(
        "production_identity.json",
        '{"deployment":"production","database":"production"}',
    )
    write_denial = evidence("production_write_denial.txt", "permission denied\n")
    credential_denial = evidence(
        "production_credentials_permissions.txt",
        "credentials unreadable\n",
    )
    index_manifest = evidence(
        "index_manifest.json",
        json.dumps(
            {
                "generation": "generation-1",
                "source_versions": {"paper-001": "v1"},
            }
        ),
    )
    tool_registration = evidence(
        "tool_registration.json",
        json.dumps({"tools": [f"tool_{index}" for index in range(17)]}),
    )
    lock = evidence("requirements.lock", "numpy==test\n")
    service_config = evidence("service.json", '{"port":8012}')
    logs = {
        name: evidence(
            f"logs/{name}.log",
            f"{name}: initialized for experiment\n",
        )
        for name in ("api", "task", "retrieval", "tool")
    }
    registrations = []
    for index in range(5):
        source_manifest = evidence(
            f"corpora/corpus_{index}_sources.json",
            json.dumps(
                {
                    "corpus": index,
                    "sources": [
                        {"source_id": f"source-{index}-{source_index}"}
                        for source_index in range(3)
                    ],
                }
            ),
        )
        access = evidence(
            f"corpora/corpus_{index}_access.txt",
            f"corpus-{index} accessible\n",
        )
        registrations.append(
            {
                "corpus_id": f"corpus-{index}",
                "source_count": 3,
                "accessible_from_locked_account": True,
                "access_evidence_path": access["path"],
                "access_evidence_sha256": access["sha256"],
                "generation": "generation-1",
                "source_manifest_path": source_manifest["path"],
                "source_manifest_sha256": source_manifest["sha256"],
            }
        )
    return {
        "status": "PASS",
        "reviewed_commit": plan["reviewed_commit"],
        "reviewed_tree": plan["reviewed_tree"],
        "experiment": {
            "base_url_origin": "https://experiment.invalid",
            "deployment_id": "d" * 64,
            "deployment_git_sha": plan["reviewed_commit"],
            "deployment_git_tree": plan["reviewed_tree"],
            "database_identity": "database-production-identity",
            "service_identity": "citrus-experiment.service",
            "deployed_file_manifest_path": deployment_manifest["path"],
            "deployed_file_manifest_sha256": deployment_manifest["sha256"],
            "health_status": "ok",
            "health_version": "1.0",
        },
        "isolation": {
            "separate_service": True,
            "separate_database": True,
            "separate_sessions": True,
            "separate_uploads": True,
            "separate_artifacts": True,
            "production_write_denied": True,
            "production_credentials_unreadable": True,
            "experiment_root": str(evidence_root.resolve()),
            "production_root": str(
                (evidence_root.parent / f"{evidence_root.name}-production").resolve()
            ),
        },
        "production": {
            "identity_path": production_identity["path"],
            "identity_sha256": production_identity["sha256"],
            "write_denial_evidence_path": write_denial["path"],
            "write_denial_evidence_sha256": write_denial["sha256"],
            "credentials_unreadable_evidence_path": credential_denial["path"],
            "credentials_unreadable_evidence_sha256": credential_denial["sha256"],
        },
        "corpora": {
            "registrations": registrations,
            "all_five_categories_accessible": True,
        },
        "index": {
            "generation": "generation-1",
            "manifest_path": index_manifest["path"],
            "manifest_sha256": index_manifest["sha256"],
            "source_versions_recorded": True,
        },
        "tools": {
            "registered_count": 17,
            "names": [f"tool_{index}" for index in range(17)],
            "registration_evidence_path": tool_registration["path"],
            "registration_evidence_sha256": tool_registration["sha256"],
        },
        "logs": {
            "api_log": logs["api"],
            "task_log": logs["task"],
            "retrieval_log": logs["retrieval"],
            "tool_log": logs["tool"],
        },
        "runtime": {
            "python_version": "3.12",
            "dependency_lock_path": lock["path"],
            "dependency_lock_sha256": lock["sha256"],
            "system_snapshot": "linux-x86_64",
            "service_config_path": service_config["path"],
            "service_config_sha256": service_config["sha256"],
        },
    }


def test_stage0_requires_explicit_reviewed_head_and_tree(monkeypatch):
    calls = []

    def fake_git(repo, *args):
        calls.append(args)
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("rev-parse", "HEAD^{tree}"):
            return "b" * 40
        if args == ("branch", "--show-current"):
            return ""
        if args[0] == "status":
            return ""
        if args[0] == "remote":
            return ""
        if args[0] == "for-each-ref":
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(stage0, "git", fake_git)
    monkeypatch.setattr(
        stage0,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {"returncode": 0, "stderr": "", "stdout": ""},
        )(),
    )
    report, blockers = stage0.build_repository_audit(
        Path("."),
        reviewed_head="a" * 40,
        reviewed_tree="b" * 40,
        github_repo=None,
        require_private=False,
        require_public=False,
    )
    assert report["head_state"] == "DETACHED"
    assert report["reviewed_tree"] == "b" * 40
    assert blockers == []


def test_stage0_fsck_failure_blocks_and_tree_mismatch_blocks(monkeypatch):
    def fake_git(repo, *args):
        if args == ("rev-parse", "HEAD"):
            return "a" * 40
        if args == ("rev-parse", "HEAD^{tree}"):
            return "c" * 40
        if args == ("branch", "--show-current"):
            return "main"
        if args[0] in {"status", "remote", "for-each-ref"}:
            return ""
        raise AssertionError(args)

    monkeypatch.setattr(stage0, "git", fake_git)
    monkeypatch.setattr(
        stage0,
        "run",
        lambda *args, **kwargs: type(
            "Result",
            (),
            {"returncode": 1, "stderr": "corrupt object", "stdout": ""},
        )(),
    )
    _, blockers = stage0.build_repository_audit(
        Path("."),
        reviewed_head="a" * 40,
        reviewed_tree="b" * 40,
        github_repo=None,
        require_private=False,
        require_public=False,
    )
    assert "git_tree_differs_from_reviewed" in blockers
    assert "git_fsck_failed" in blockers


def test_stage1_provider_requires_content_id_model_and_schema(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("PROVIDER_BASE_URL", base_url)
    monkeypatch.setenv("PROVIDER_KEY", "provider-secret-value")
    definition = {
        "id": "judge_probe",
        "kind": "provider_http",
        "base_url_env": "PROVIDER_BASE_URL",
        "api_key_env": "PROVIDER_KEY",
        "path_candidates": ["/chat/completions"],
        "calls": [
            {
                "role": "judge_1",
                "model_id": "glm-5.3-flash",
                "allowed_response_models": ["glm-5.3-flash"],
                "expected_response_format": "json_object",
                "required_content_keys": ["ok"],
                "body_template": {
                    "messages": [
                        {"role": "user", "content": "Return JSON only."}
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
    assert result["status"] == "PASS"
    row = result["models"][0]
    assert row["request_id"] == "response-fixture-001"
    assert row["generated_content"] == '{"ok":true}'
    assert row["usage_present"] is True
    assert row["response_validation_errors"] == []


def test_stage1_agent_model_env_must_match_frozen_call(
    tmp_path,
    json_server,
    monkeypatch,
):
    base_url, _ = json_server
    monkeypatch.setenv("PROVIDER_BASE_URL", base_url)
    monkeypatch.setenv("PROVIDER_KEY", "provider-secret-value")
    monkeypatch.setenv("AGENT_MODEL_ENV", "wrong-model")
    definition = {
        "id": "agent_probe",
        "kind": "provider_http",
        "base_url_env": "PROVIDER_BASE_URL",
        "api_key_env": "PROVIDER_KEY",
        "path_candidates": ["/chat/completions"],
        "calls": [
            {
                "role": "agent",
                "model_id": "deepseek-v4.1-flash",
                "model_id_env": "AGENT_MODEL_ENV",
                "expected_response_format": "text",
                "body_template": {
                    "messages": [{"role": "user", "content": "Reply OK."}]
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
    assert "model_env_mismatch:AGENT_MODEL_ENV" in result["models"][0]["errors"]


def test_stage1_model_binding_checks_plan_app_probe_and_environment(monkeypatch):
    config = expected_model_config()
    config["status"] = "READY"
    judge = {
        "status": "PILOT_READY",
        "forbidden_judge_family": "DeepSeek",
        "judges": {
            "judge_1": {
                "family": "GLM",
                "candidate_model_id": "glm-5.3-flash",
                "allowed_model_ids": ["glm-5.3-flash"],
                "actual_response_model": None,
            },
            "judge_2": {
                "family": "MiMo",
                "candidate_model_id": "mimo-v2.6-flash",
                "allowed_model_ids": ["mimo-v2.6-flash"],
                "actual_response_model": None,
            },
            "judge_3": {
                "family": "MiniMax",
                "candidate_model_id": "minimax-m3.1-flash",
                "allowed_model_ids": ["minimax-m3.1-flash"],
                "actual_response_model": None,
            },
        },
    }
    plan = {
        "probes": [
            {
                "id": "runtime_config_probe",
                "kind": "http",
                "required_json_paths": {
                    "model.provider_id": "aliyun_bailian",
                    "model.main": "deepseek-v4.1-flash",
                },
            },
            {
                "id": "agent_api_probe",
                "kind": "provider_http",
                "calls": [
                    {
                        "role": "agent",
                        "model_id": "deepseek-v4.1-flash",
                        "model_id_env": "AGENT_MODEL_ENV",
                    }
                ],
            },
            {
                "id": "go_api_probe",
                "kind": "provider_http",
                "calls": [
                    {
                        "role": "judge_1",
                        "model_id": "glm-5.3-flash",
                        "allowed_response_models": ["glm-5.3-flash"],
                    },
                    {
                        "role": "judge_2",
                        "model_id": "mimo-v2.6-flash",
                        "allowed_response_models": ["mimo-v2.6-flash"],
                    },
                    {
                        "role": "judge_3",
                        "model_id": "minimax-m3.1-flash",
                        "allowed_response_models": ["minimax-m3.1-flash"],
                    },
                ],
            },
            {
                "id": "model_identity_probe",
                "kind": "model_identity",
                "allowlists": {
                    "judge_1": ["glm-5.3-flash"],
                    "judge_2": ["mimo-v2.6-flash"],
                    "judge_3": ["minimax-m3.1-flash"],
                },
            },
        ]
    }
    monkeypatch.setenv("AGENT_MODEL_ENV", "deepseek-v4.1-flash")
    assert probe.model_binding_errors(plan, config, judge) == []
    plan["probes"][0]["required_json_paths"]["model.main"] = "other-model"
    errors = probe.model_binding_errors(plan, config, judge)
    assert "runtime_config_model_expectation_mismatch" in errors


def test_stage1_judge_binding_rejects_unconfigured_and_model_mismatch(
    monkeypatch,
):
    config = expected_model_config()
    config["status"] = "READY"
    judge = {
        "status": "PILOT_READY",
        "forbidden_judge_family": "DeepSeek",
        "judges": {
            "judge_1": {
                "family": "GLM",
                "candidate_model_id": "glm-5.3-flash",
                "allowed_model_ids": ["glm-5.3-flash"],
                "actual_response_model": None,
            },
            "judge_2": {
                "family": "MiMo",
                "candidate_model_id": "mimo-v2.6-flash",
                "allowed_model_ids": ["mimo-v2.6-flash"],
                "actual_response_model": None,
            },
            "judge_3": {
                "family": "MiniMax",
                "candidate_model_id": "minimax-m3.1-flash",
                "allowed_model_ids": ["minimax-m3.1-flash"],
                "actual_response_model": None,
            },
        },
    }
    plan = {
        "probes": [
            {
                "id": "runtime_config_probe",
                "kind": "http",
                "required_json_paths": {
                    "model.provider_id": "aliyun_bailian",
                    "model.main": "deepseek-v4.1-flash",
                },
            },
            {
                "id": "agent_api_probe",
                "kind": "provider_http",
                "calls": [
                    {
                        "role": "agent",
                        "model_id": "deepseek-v4.1-flash",
                        "model_id_env": "AGENT_MODEL_ENV",
                    }
                ],
            },
            {
                "id": "go_api_probe",
                "kind": "provider_http",
                "calls": [
                    {
                        "role": "judge_1",
                        "model_id": "glm-5.3-flash",
                        "allowed_response_models": ["glm-5.3-flash"],
                    },
                    {
                        "role": "judge_2",
                        "model_id": "mimo-v2.6-flash",
                        "allowed_response_models": ["mimo-v2.6-flash"],
                    },
                    {
                        "role": "judge_3",
                        "model_id": "minimax-m3.1-flash",
                        "allowed_response_models": ["minimax-m3.1-flash"],
                    },
                ],
            },
            {
                "id": "model_identity_probe",
                "kind": "model_identity",
                "allowlists": {
                    "judge_1": ["glm-5.3-flash"],
                    "judge_2": ["mimo-v2.6-flash"],
                    "judge_3": ["minimax-m3.1-flash"],
                },
            },
        ]
    }
    monkeypatch.setenv("AGENT_MODEL_ENV", "deepseek-v4.1-flash")
    assert probe.model_binding_errors(plan, config, judge) == []

    judge["status"] = "UNCONFIGURED"
    errors = probe.model_binding_errors(plan, config, judge)
    assert "judge_config_status_not_probe_ready" in errors

    judge["status"] = "PILOT_READY"
    plan["probes"][2]["calls"][0]["model_id"] = "other-model"
    plan["probes"][2]["calls"][0]["allowed_response_models"] = ["other-model"]
    errors = probe.model_binding_errors(plan, config, judge)
    assert "judge_probe_model_differs_from_config:judge_1" in errors
    assert "judge_probe_allowlist_differs_from_config:judge_1" in errors

    plan["probes"][2]["calls"][0]["model_id"] = "glm-5.3-flash"
    plan["probes"][2]["calls"][0]["allowed_response_models"] = [
        "glm-5.3-flash"
    ]
    judge["judges"]["judge_1"]["actual_response_model"] = "other-model"
    errors = probe.model_binding_errors(plan, config, judge)
    assert "judge_actual_response_model_not_allowed:judge_1" in errors


def test_stage1_stage0_guard_binds_origin_deployment_and_isolation(tmp_path):
    plan = valid_probe_plan()
    report = valid_stage0_report(plan, tmp_path)
    report_path = tmp_path / "G01.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    plan["execution_guards"]["stage0_report_sha256"] = (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
    guard, errors = probe.stage0_guard_errors(plan, report_path)
    assert errors == []
    assert guard["status"] == "PASS"
    report["isolation"]["production_write_denied"] = False
    report["experiment"]["database_identity"] = ""
    report_path.write_text(json.dumps(report), encoding="utf-8")
    plan["execution_guards"]["stage0_report_sha256"] = (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
    guard, errors = probe.stage0_guard_errors(plan, report_path)
    assert guard["status"] == "BLOCKED"
    assert "stage0_isolation_not_verified:production_write_denied" in errors
    assert "stage0_experiment_identity_missing:database_identity" in errors


def test_stage1_stage0_guard_rejects_weak_evidence(tmp_path):
    plan = valid_probe_plan()
    report = valid_stage0_report(plan, tmp_path)
    report["production"]["write_denial_evidence_sha256"] = "1" * 64
    report["tools"]["names"][-1] = report["tools"]["names"][0]
    report["corpora"]["registrations"][0]["accessible_from_locked_account"] = False
    report["logs"]["api_log"] = "relative/api.log"
    report["runtime"]["service_config_sha256"] = "not-a-sha"
    report["isolation"]["production_root"] = report["isolation"]["experiment_root"]
    report_path = tmp_path / "G01-weakened.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    plan["execution_guards"]["stage0_report_sha256"] = (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
    guard, errors = probe.stage0_guard_errors(plan, report_path)
    assert guard["status"] == "BLOCKED"
    assert (
        "stage0_evidence_sha_mismatch:production.write_denial_evidence"
        in errors
    )
    assert "stage0_registered_tool_names_not_unique" in errors
    assert "stage0_corpus_not_accessible:0" in errors
    assert "stage0_log_evidence_invalid:api_log" in errors
    assert "stage0_runtime_sha_invalid:service_config_sha256" in errors
    assert "stage0_experiment_and_production_roots_not_separate" in errors


def test_repository_remote_credentials_are_redacted_and_blocked():
    safe_https, detected = stage0.redact_remote_url(
        "https://user:token@example.com/org/repo.git"
    )
    assert detected is True
    assert safe_https == "https://[REDACTED]@example.com/org/repo.git"
    safe_query, detected = stage0.redact_remote_url(
        "https://example.com/org/repo.git?access_token=secret&ref=main"
    )
    assert detected is True
    assert "secret" not in safe_query
    assert "access_token=%5BREDACTED%5D" in safe_query
    safe_lines, credential_remotes = stage0.redact_remote_lines(
        [
            "origin\thttps://token@example.com/org/repo.git (fetch)",
            "upstream\thttps://example.com/org/repo.git (push)",
        ]
    )
    assert credential_remotes == ["origin"]
    assert "token@" not in safe_lines[0]
    assert safe_lines[1] == "upstream https://example.com/org/repo.git (push)"


def test_stage1_stage0_guard_enforces_evidence_root_and_manifest_binding(tmp_path):
    plan = valid_probe_plan()
    report = valid_stage0_report(plan, tmp_path)

    deployment_path = Path(report["experiment"]["deployed_file_manifest_path"])
    deployment = json.loads(deployment_path.read_text(encoding="utf-8"))
    deployment["git_tree"] = "f" * 40
    report["experiment"]["deployed_file_manifest_sha256"] = write_reference(
        tmp_path,
        "deployment_manifest.json",
        deployment,
    )

    registration_path = Path(report["tools"]["registration_evidence_path"])
    registration = json.loads(registration_path.read_text(encoding="utf-8"))
    registration["tools"][0] = "different_tool"
    report["tools"]["registration_evidence_sha256"] = write_reference(
        tmp_path,
        "tool_registration.json",
        registration,
    )

    source_manifest_path = Path(
        report["corpora"]["registrations"][0]["source_manifest_path"]
    )
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    source_manifest["sources"] = source_manifest["sources"][:2]
    report["corpora"]["registrations"][0]["source_manifest_sha256"] = write_reference(
        tmp_path,
        "corpora/corpus_0_sources.json",
        source_manifest,
    )

    outside_path = tmp_path.parent / f"{tmp_path.name}-outside-evidence.json"
    outside_sha = write_reference(
        tmp_path.parent,
        outside_path.name,
        {"generation": "generation-1"},
    )
    report["index"]["manifest_path"] = str(outside_path.resolve())
    report["index"]["manifest_sha256"] = outside_sha
    report["isolation"]["production_root"] = (
        str(Path(report["isolation"]["experiment_root"]) / ".")
    )

    report_path = tmp_path / "G01-binding-errors.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    plan["execution_guards"]["stage0_report_sha256"] = (
        hashlib.sha256(report_path.read_bytes()).hexdigest()
    )
    guard, errors = probe.stage0_guard_errors(plan, report_path)
    assert guard["status"] == "BLOCKED"
    assert "stage0_deployed_file_manifest_tree_mismatch" in errors
    assert "stage0_tool_registration_names_mismatch" in errors
    assert "stage0_corpus_source_count_mismatch:0" in errors
    assert "stage0_evidence_path_outside_experiment_root:index.manifest" in errors
    assert "stage0_experiment_and_production_roots_not_separate" in errors


def test_stage1_execute_plan_refuses_network_probe_without_guard(tmp_path, monkeypatch):
    monkeypatch.setenv("CITRUS_EXPERIMENT_BASE_URL", "https://experiment.invalid")
    plan = valid_probe_plan()
    plan["probes"] = [
        {
            "id": "health",
            "kind": "http",
            "required": True,
            "base_url_env": "CITRUS_EXPERIMENT_BASE_URL",
            "path_candidates": ["/health"],
            "success_statuses": [200],
        }
    ]
    manifest = probe.execute_plan(
        plan,
        output_dir=tmp_path / "blocked",
        timeout_seconds=1,
        allow_rate_limit=False,
    )
    assert manifest["status"] == "BLOCKED"
    assert "execution_guard_missing" in manifest["required_probe_failures"]
    assert manifest["attempt_files"] == {}


def test_env_file_must_be_outside_git_checkout(tmp_path, monkeypatch):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    inside = checkout / ".env"
    inside.write_text("SECRET=not-read\n", encoding="utf-8")
    inside.chmod(0o600)
    if os.name == "posix":
        checkout.chmod(0o700)
    monkeypatch.setenv("TEST_ENV_FILE", str(inside))
    monkeypatch.setenv("TEST_CHECKOUT", str(checkout))
    result = probe.env_presence(
        {
            "variables": [],
            "environment_file_env": "TEST_ENV_FILE",
            "checkout_root_env": "TEST_CHECKOUT",
        }
    )
    assert result["environment_file"]["outside_git_checkout"] is False
    assert result["all_required_present"] is False


def test_stage2_rejects_forged_raw_response_file(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["raw_response_sha256"] = "0" * 64
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_corpus_access=True,
    )
    assert "reference_sha256_mismatch:raw_agent_response" in errors


def test_stage2_rejects_raw_response_content_and_identity_mismatch(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["raw_response_sha256"] = write_reference(
        tmp_path,
        trace["raw_response_ref"],
        {
            "id": "different-response-id",
            "model": "different-model",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "different raw answer",
                    }
                }
            ],
        },
    )
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
    )
    assert "raw_response_file_answer_differs_from_trace" in errors
    assert "raw_response_file_id_differs_from_trace" in errors
    assert "raw_response_file_model_differs_from_trace" in errors


def test_stage2_rejects_invalid_sse_and_retrieval_captures(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["sse_sha256"] = write_reference(
        tmp_path,
        trace["sse_ref"],
        "not-an-event-stream",
    )
    mismatched = [
        {"sequence": 99, "event": "done", "answer": "different"},
        {"sequence": 100, "event": "done", "answer": "also-different"},
    ]
    trace["retrieval_sha256"] = write_reference(
        tmp_path,
        trace["retrieval_ref"],
        mismatched,
    )
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_corpus_access=True,
    )
    assert "sse_capture_format_invalid" in errors
    assert "retrieval_capture_event_count_mismatch" in errors
    assert "retrieval_capture_event_mismatch:0" in errors
    assert "retrieval_capture_source_locator_missing" in errors


def test_stage2_rejects_tool_result_file_not_equal_to_trace(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    event = trace["tool_events"][0]
    event["result_sha256"] = write_reference(
        tmp_path,
        event["result_ref"],
        {"chunk_id": "different-result"},
    )
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
    )
    assert "tool_result_file_differs_from_trace:0" in errors


def test_stage2_rejects_string_backend_log_reference(tmp_path):
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["backend_log_refs"] = ["logs/backend_api.log"]
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
    )
    assert "backend_log_ref_string_not_allowed:0" in errors
    assert "backend_log_request_id_not_observed" in errors


def test_stage2_requires_completed_local_tool_for_b2(tmp_path):
    request = valid_request()
    request["condition"] = "B2"
    trace = valid_trace()
    trace["condition"] = "B2"
    trace["tool_events"][0]["status"] = "failed"
    materialize_trace_evidence(tmp_path, trace)
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_successful_completed_run=True,
        stage1_manifest=valid_stage1_manifest(),
    )
    assert "required_completed_tool_groups_missing:LOCAL" in errors

    trace["tool_events"] = []
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_successful_completed_run=True,
        stage1_manifest=valid_stage1_manifest(),
    )
    assert "required_completed_tool_groups_missing:LOCAL" in errors


def test_stage2_binds_tool_identity_to_frozen_stage1_registry(tmp_path):
    request = valid_request()
    request["condition"] = "B2"
    trace = valid_trace()
    trace["condition"] = "B2"
    trace["tool_events"][0]["tool_name"] = "self_reported_unknown_tool"
    materialize_trace_evidence(tmp_path, trace)
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_successful_completed_run=True,
        stage1_manifest=valid_stage1_manifest(),
    )
    assert "tool_event_name_not_in_frozen_registry:0" in errors
    assert "required_completed_tool_groups_missing:LOCAL" in errors


def test_stage2_rejects_wrong_model_provider_and_failed_success_claim(tmp_path):
    request = valid_request()
    trace = valid_trace()
    materialize_trace_evidence(tmp_path, trace)
    trace["provider"] = "Wrong provider"
    trace["actual_model"] = "wrong-model"
    trace["status"] = "failed"
    errors = stage2.audit_trace(
        trace,
        request,
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_successful_completed_run=True,
        expected_model_config=expected_model_config(),
    )
    assert "trace_provider_differs_from_frozen_config" in errors
    assert "trace_actual_model_not_in_frozen_allowlist" in errors
    assert "stage2_requires_completed_run" in errors


def test_stage2_rejects_control_material_in_tool_result(tmp_path):
    trace = valid_trace()
    trace["tool_events"][0]["result"] = {
        "path": "/srv/citrus/07_scoring/expert/scores.csv"
    }
    materialize_trace_evidence(tmp_path, trace)
    errors = stage2.audit_trace(
        trace,
        valid_request(),
        request_sha256="request-sha",
        evidence_root=tmp_path,
        require_corpus_access=True,
    )
    assert any("tool_payload_reaches_control_material" in error for error in errors)


def test_stage2_session_registry_detects_duplicate_session(tmp_path):
    registry = tmp_path / "sessions.csv"
    registry.write_text(
        "\n".join(
            [
                "run_id,request_id,session_id,condition,question_id,created_for_run,previous_session_ids,created_utc",
                "run-a,req-a,same-session,B0,L01,true,,t1",
                "run-b,req-b,same-session,B1,L01,true,,t2",
                "REQ_001,REQ_001,SESSION_QA_L01_B1_T1,B1,L01,true,,t3",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = stage2.audit_session_registry(registry, valid_request())
    assert result["status"] == "BLOCKED"
    assert "session_registry_duplicate_session_id" in result["errors"]
    assert result["duplicate_session_ids"] == ["same-session"]
    assert result["registry_sha256"] == hashlib.sha256(
        registry.read_bytes()
    ).hexdigest()


def test_stage2_session_registry_rejects_empty_values_and_duplicate_request(tmp_path):
    registry = tmp_path / "sessions.csv"
    registry.write_text(
        "\n".join(
            [
                "run_id,request_id,session_id,condition,question_id,created_for_run,previous_session_ids,created_utc",
                ",req-empty-run,session-a,B0,L01,true,,t1",
                "run-b,,session-b,B0,L01,true,,t2",
                "run-c,req-empty-session,,B0,L01,true,,t3",
                "run-d,dup-request,session-d,B0,L01,true,,t4",
                "run-e,dup-request,session-e,B0,L01,true,,t5",
                "REQ_001,REQ_001,SESSION_QA_L01_B1_T1,B1,L01,true,,t6",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    result = stage2.audit_session_registry(registry, valid_request())
    assert result["status"] == "BLOCKED"
    assert "session_registry_empty_run_id" in result["errors"]
    assert "session_registry_empty_request_id" in result["errors"]
    assert "session_registry_empty_session_id" in result["errors"]
    assert "session_registry_duplicate_request_id" in result["errors"]
    assert result["duplicate_request_ids"] == ["dup-request"]


def test_stage2_missing_session_registry_is_not_assessed(tmp_path):
    result = stage2.audit_session_registry(None, valid_request())
    assert result["status"] == "NOT_ASSESSED"
    report = stage2.build_report(
        request=valid_request(),
        trace=valid_trace(),
        request_sha256="request-sha",
        trace_sha256="trace-sha",
        errors=[],
        session_registry=result,
    )
    assert report["cross_condition_contamination"] == "NOT_ASSESSED"
    assert report["status"] == "BLOCKED"


def test_stage1_manifest_cross_binds_app_and_agent_model():
    assert stage2.audit_stage1_binding(
        valid_stage1_manifest(),
        expected_model_config(),
    ) == []
    manifest = valid_stage1_manifest()
    manifest["runtime_app_config"]["model"]["main"] = "different-model"
    manifest["execution_guard"]["tool_names"][-1] = manifest["execution_guard"][
        "tool_names"
    ][0]
    errors = stage2.audit_stage1_binding(manifest, expected_model_config())
    assert "stage1_app_model_differs_from_frozen" in errors
    assert "stage1_tool_registry_names_not_unique" in errors


def test_boundary_output_is_write_once(tmp_path):
    path = tmp_path / "audit.json"
    stage2.write_new_json(path, {"status": "PASS"})
    with pytest.raises(FileExistsError):
        stage2.write_new_json(path, {"status": "BLOCKED"})
