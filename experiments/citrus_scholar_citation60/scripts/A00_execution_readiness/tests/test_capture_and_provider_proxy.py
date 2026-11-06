from __future__ import annotations

import hashlib
import importlib.util
import json
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


HERE = Path(__file__).resolve()
SCRIPT_DIR = HERE.parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


capture_schema = load_module(
    "capture_schema_test",
    SCRIPT_DIR.parents[1] / "scripts" / "capture_schema.py",
)
provider_capture = load_module(
    "provider_capture_test",
    SCRIPT_DIR / "provider_capture.py",
)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, payload: object) -> bytes:
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data


def valid_b2_response(root: Path) -> dict:
    run_id = "PILOT_L01_B2_T1"
    client_request_id = "CLIENT_001"
    server_request_id = "SERVER_001"
    session_id = "SESSION_001"
    job_id = "JOB_001"
    response_id = "PROVIDER_RESPONSE_001"
    model = "deepseek-v4.1-flash"
    final_answer = "Evidence-backed answer."
    raw_response = {
        "id": response_id,
        "model": model,
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": final_answer}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 3},
    }
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": "Question"}],
        "stream": True,
    }
    app_request = {
        "session_id": session_id,
        "query": "Question",
        "client_request_id": client_request_id,
    }
    sse_events = [
        {"sequence": 1, "event": "answer_start", "data": {}},
        {
            "sequence": 2,
            "event": "text",
            "data": {"content": final_answer},
        },
        {
            "sequence": 3,
            "event": "job",
            "data": {"job_id": job_id, "session_id": session_id},
        },
        {
            "sequence": 4,
            "event": "done",
            "data": {
                "answer": final_answer,
                "job_id": job_id,
                "session_id": session_id,
            },
        },
    ]
    app_request_bytes = write_json(root / "request.json", app_request)
    raw_bytes = write_json(root / "provider_capture/cap-001/response_envelope.json", raw_response)
    request_bytes = write_json(root / "provider_capture/cap-001/request_body.json", request_body)
    sse_bytes = write_json(root / "sse.json", sse_events)
    history_bytes = write_json(
        root / "history.json",
        {
            "session_id": session_id,
            "turns": [
                {
                    "request_id": client_request_id,
                    "answer": {"content": final_answer},
                }
            ],
        },
    )
    citations_bytes = write_json(root / "citations.json", [])
    backend_text = (
        f"server_request_id={server_request_id} "
        f"client_request_id={client_request_id} "
        f"run_id={run_id} job_id={job_id}\n"
    ).encode("utf-8")
    (root / "logs").mkdir()
    (root / "logs/backend_api.log").write_bytes(backend_text)
    index_record = {
        "capture_id": "cap-001",
        "run_id": run_id,
        "client_request_id": client_request_id,
        "server_request_id": server_request_id,
        "session_id": session_id,
        "job_id": job_id,
        "provider_response_id": response_id,
        "requested_model": model,
        "response_model": model,
        "request_sha256": sha(request_bytes),
        "raw_response_sha256": sha(raw_bytes),
        "raw_response_path": "provider_capture/cap-001/response_envelope.json",
        "request_body_path": "provider_capture/cap-001/request_body.json",
        "status": "CAPTURED",
    }
    index_bytes = write_json(root / "provider_capture/index.jsonl", index_record)
    return {
        "status": "completed",
        "final_answer": final_answer,
        "raw_answer": final_answer,
        "run_id": run_id,
        "client_request_id": client_request_id,
        "server_request_id": server_request_id,
        "session_id": session_id,
        "job_id": job_id,
        "requested_model": model,
        "response_model": model,
        "response_id": response_id,
        "provider_response_id": response_id,
        "request_body": request_body,
        "request_sha256": sha(request_bytes),
        "raw_response": raw_response,
        "raw_response_ref": "provider_capture/cap-001/response_envelope.json",
        "raw_response_sha256": sha(raw_bytes),
        "provider_http_status": 200,
        "tool_calls": [],
        "citations": [],
        "artifact_paths": [],
        "artifact_sha256": {},
        "tokens_in": 10,
        "tokens_out": 3,
        "model_calls": 1,
        "total_input_tokens": 10,
        "total_output_tokens": 3,
        "retrieval_calls": 0,
        "context_tokens_used": 10,
        "context_token_metric": "sum_provider_prompt_tokens_proxy",
        "tool_call_count": 0,
        "budget_status": "WITHIN_BUDGET",
        "budget": {
            "status": "WITHIN_BUDGET",
            "violations": [],
            "unknown_metrics": [],
            "caps": {},
        },
        "provider_capture_records": [
            {
                "capture_id": "cap-001",
                "provider_response_id": response_id,
                "requested_model": model,
                "response_model": model,
                "request_sha256": sha(request_bytes),
                "raw_response_sha256": sha(raw_bytes),
                "raw_response_path": "provider_capture/cap-001/response_envelope.json",
                "http_status": 200,
                "usage": raw_response["usage"],
            }
        ],
        "source_scope_policy": {
            "canonical": "local",
            "allow_local": True,
            "allow_web": False,
            "allow_attachment": False,
        },
        "allowed_corpus_family_ids": ["literature"],
        "source_scope_validation": {"status": "PASS", "errors": []},
        "app_request": app_request,
        "app_request_ref": "request.json",
        "app_request_sha256": sha(app_request_bytes),
        "sse_events": sse_events,
        "sse_ref": "sse.json",
        "sse_sha256": sha(sse_bytes),
        "history_ref": "history.json",
        "history_sha256": sha(history_bytes),
        "history_answer": final_answer,
        "history_session_id": session_id,
        "history_request_id": client_request_id,
        "citations_ref": "citations.json",
        "citations_sha256": sha(citations_bytes),
        "provider_capture_index_ref": "provider_capture/index.jsonl",
        "provider_capture_index_sha256": sha(index_bytes),
        "backend_log_refs": [
            {
                "path": "logs/backend_api.log",
                "sha256": sha(backend_text),
                "reference": "backend_log_matched_ids",
            }
        ],
        "retrieval_events": [],
        "evidence_root": str(root),
    }


def test_capture_schema_accepts_fully_bound_b2_record(tmp_path):
    response = valid_b2_response(tmp_path)
    assert capture_schema.validate_capture(response, condition="B2") == []


def test_capture_schema_rejects_forged_capture_and_backend_files(tmp_path):
    response = valid_b2_response(tmp_path)
    (tmp_path / "provider_capture/cap-001/response_envelope.json").write_text(
        '{"id":"forged"}\n',
        encoding="utf-8",
    )
    response["backend_log_refs"][0]["path"] = str(
        (tmp_path / "logs/backend_api.log").resolve()
    )
    errors = capture_schema.validate_capture(response, condition="B2")
    assert "b2_raw_response_sha_mismatch" in errors
    assert "b2_backend_log:0_path_not_relative" in errors


def test_provider_capture_proxy_records_json_without_credentials(tmp_path):
    upstream_state: dict[str, str | int] = {}

    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            upstream_state["authorization"] = self.headers.get("Authorization", "")
            upstream_state["cookie"] = self.headers.get("Cookie", "")
            upstream_state["body"] = body.decode("utf-8")
            payload = json.dumps(
                {
                    "id": "upstream-response-001",
                    "model": "test-model",
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": "proxy answer",
                            }
                        }
                    ],
                    "usage": {"prompt_tokens": 2, "completion_tokens": 2},
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    root = tmp_path / "capture"
    root.mkdir()
    store = provider_capture.CaptureStore(root, root / "provider_capture/index.jsonl")
    proxy = provider_capture.CaptureServer(
        ("127.0.0.1", 0),
        upstream_base=f"http://127.0.0.1:{upstream.server_port}/v1",
        store=store,
        timeout=5,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                }
            ).encode("utf-8"),
            headers={
                "Authorization": "Bearer should-not-be-recorded",
                "Cookie": "session=should-not-be-recorded",
                "x-citrus-run-id": "RUN_PROXY_001",
                "x-citrus-client-request-id": "CLIENT_PROXY_001",
                "x-citrus-server-request-id": "SERVER_PROXY_001",
                "x-citrus-session-id": "SESSION_PROXY_001",
                "x-citrus-job-id": "JOB_PROXY_001",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            response.read()
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        proxy_thread.join(timeout=5)
        upstream_thread.join(timeout=5)

    index_records = [
        json.loads(line)
        for line in (root / "provider_capture/index.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert len(index_records) == 1
    record = index_records[0]
    assert record["run_id"] == "RUN_PROXY_001"
    assert record["client_request_id"] == "CLIENT_PROXY_001"
    assert record["server_request_id"] == "SERVER_PROXY_001"
    assert record["session_id"] == "SESSION_PROXY_001"
    assert record["job_id"] == "JOB_PROXY_001"
    assert record["status"] == "CAPTURED"
    assert upstream_state["authorization"] == "Bearer should-not-be-recorded"
    assert upstream_state["cookie"] == "session=should-not-be-recorded"
    evidence_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )
    assert "should-not-be-recorded" not in evidence_text


def test_provider_capture_proxy_parses_sse_without_credentials(tmp_path):
    class Upstream(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            payload = b"".join(
                [
                    b'data: {"id":"sse-response-001","model":"test-model",'
                    b'"choices":[{"index":0,"delta":{"content":"streamed "}}]}\n\n',
                    b'data: {"id":"sse-response-001","model":"test-model",'
                    b'"choices":[{"index":0,"delta":{"content":"answer"},'
                    b'"finish_reason":"stop"}],"usage":{"prompt_tokens":2,'
                    b'"completion_tokens":2}}\n\n',
                    b"data: [DONE]\n\n",
                ]
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    root = tmp_path / "capture-sse"
    root.mkdir()
    store = provider_capture.CaptureStore(root, root / "provider_capture/index.jsonl")
    proxy = provider_capture.CaptureServer(
        ("127.0.0.1", 0),
        upstream_base=f"http://127.0.0.1:{upstream.server_port}/v1",
        store=store,
        timeout=5,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    try:
        request = urllib.request.Request(
            f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
            data=json.dumps(
                {
                    "model": "test-model",
                    "messages": [{"role": "user", "content": "hello"}],
                    "stream": True,
                }
            ).encode("utf-8"),
            headers={
                "Authorization": "Bearer sse-secret",
                "x-citrus-run-id": "RUN_SSE_001",
                "x-citrus-client-request-id": "CLIENT_SSE_001",
                "x-citrus-server-request-id": "SERVER_SSE_001",
                "x-citrus-session-id": "SESSION_SSE_001",
                "x-citrus-job-id": "JOB_SSE_001",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            assert response.status == 200
            stream_body = response.read()
            assert b'"content":"streamed "' in stream_body
            assert b'"content":"answer"' in stream_body
            assert b"data: [DONE]" in stream_body
    finally:
        proxy.shutdown()
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        proxy_thread.join(timeout=5)
        upstream_thread.join(timeout=5)

    record = json.loads(
        (root / "provider_capture/index.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()[0]
    )
    assert record["stream_format"] == "sse"
    assert record["status"] == "CAPTURED"
    envelope = json.loads(
        (root / record["raw_response_path"]).read_text(encoding="utf-8")
    )
    assert envelope["id"] == "sse-response-001"
    assert envelope["choices"][0]["message"]["content"] == "streamed answer"
    evidence_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in root.rglob("*")
        if path.is_file()
    )
    assert "sse-secret" not in evidence_text
