#!/usr/bin/env python3
"""Live smoke test for a running CitrusQA service: one real chat turn that must
produce a Word document, then an independent download + DOCX integrity check.

This exercises the real HTTP surface (cookie auth, SSE stream, job state,
history/turns, file library, download bytes).  It never edits the repository,
gold data or benchmark files, and it deletes its temporary account afterwards
unless --keep-account is given.

Usage (repository root, service already running):
    set CITRUS_SMOKE_PASSWORD=...
    python -X utf8 engineering/word_delivery_20261112/smoke_service.py \
        --username smoke_20261113 --timeout 300
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import time
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree

import requests

ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = Path(__file__).resolve().parent / "smoke_artifacts"

QUERY = (
    "请只使用 Word 文档工具，把下面这段现成文字制作成一份 Word 文档并交付下载，"
    "不要检索，不要扩写，不要生成其它格式。标题：《冒烟测试文档》。"
    "第一节标题：测试范围。正文原样写入：本文件由冒烟测试自动生成，用于验证 Word 导出链路。"
    "完成后告诉我实际文件名。"
)
TITLE = "冒烟测试文档"
SECTION = "测试范围"
BODY = "本文件由冒烟测试自动生成，用于验证 Word 导出链路。"

CHECKS: list[dict] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    CHECKS.append({"name": name, "ok": bool(ok), "detail": detail})
    print(("PASS  " if ok else "FAIL  ") + name + ((" :: " + detail) if detail else ""),
          flush=True)
    return bool(ok)


def note(text: str) -> None:
    print("----  " + text, flush=True)


def iter_sse(response):
    """Yield (event, id, data) from an sse-starlette response."""
    event = None
    data: list[str] = []
    event_id = None
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        if raw == "":
            if event is not None or data:
                yield event or "message", event_id, "\n".join(data)
            event, data, event_id = None, [], None
            continue
        if raw.startswith(":"):
            continue
        field, _, value = raw.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if field == "event":
            event = value
        elif field == "data":
            data.append(value)
        elif field == "id":
            event_id = value


def docx_text(blob: bytes) -> tuple[str, list[str], str]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        bad = zf.testzip()
        if bad:
            raise RuntimeError(f"corrupt zip member: {bad}")
        names = zf.namelist()
        raw = zf.read("word/document.xml")
    root = ElementTree.fromstring(raw)
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    texts = [node.text or "" for node in root.iter(
        "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t")]
    return raw.decode("utf-8", "replace"), names, "".join(texts)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8000")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", default=os.getenv("CITRUS_SMOKE_PASSWORD", ""))
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--keep-account", action="store_true")
    args = parser.parse_args()
    if not args.password:
        print("missing --password or CITRUS_SMOKE_PASSWORD", file=sys.stderr)
        return 2
    base = args.base.rstrip("/")
    started = time.time()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # ---- 1. service surface -------------------------------------------------
    health = requests.get(base + "/health", timeout=10)
    check("health endpoint", health.status_code == 200 and health.json().get("status") == "ok",
          f"HTTP {health.status_code} {health.text[:120]}")

    index = requests.get(base + "/", timeout=10)
    check("index page serves app.js?v=36",
          index.status_code == 200 and "app.js?v=36" in index.text,
          f"HTTP {index.status_code}, {len(index.text)} bytes")

    # ---- 2. account + login -------------------------------------------------
    session = requests.Session()
    # The service rejects non-GET requests that carry the auth cookie but no
    # same-origin header.  The browser sends this automatically.
    session.headers["Origin"] = base
    login = session.post(base + "/api/v2/auth/login",
                         json={"username": args.username, "password": args.password},
                         timeout=20)
    check("smoke account login", login.status_code == 200,
          f"HTTP {login.status_code} {login.text[:160]}")
    if login.status_code != 200:
        _write_report(args, started, None, None)
        return 1

    runtime = session.get(base + "/api/v2/config", timeout=10).json()
    model = runtime.get("model") or {}
    has_key = bool(runtime.get("has_api_key"))
    check("model provider configured", has_key,
          f"provider={model.get('provider_id')} main={model.get('main')} "
          f"version={runtime.get('version')}")
    if not has_key:
        note("provider not configured; real chat turn skipped")
        _write_report(args, started, None, None)
        return 1

    # ---- 3. one real chat turn ---------------------------------------------
    client_request_id = uuid.uuid4().hex
    payload = {"query": QUERY, "client_request_id": client_request_id,
               "attachments": [], "file_refs": []}
    frames: list[dict] = []
    done_payload: dict | None = None
    error_payload: dict | None = None
    job_payload: dict | None = None
    tool_events: list[dict] = []
    seqs: list[int] = []
    bad_seq: list[str] = []
    deadline = time.time() + args.timeout
    note("POST /api/v2/chat (real model turn)")
    with session.post(base + "/api/v2/chat", json=payload, stream=True,
                      timeout=(10, 90)) as response:
        if not check("chat stream accepted", response.status_code == 200,
                     f"HTTP {response.status_code} {response.text[:200]}"):
            _write_report(args, started, None, None)
            return 1
        for event, event_id, data in iter_sse(response):
            if time.time() > deadline:
                bad_seq.append("deadline exceeded while streaming")
                break
            try:
                body = json.loads(data) if data else {}
            except ValueError:
                bad_seq.append(f"unparseable frame {event}: {data[:80]}")
                continue
            seq = body.get("seq")
            if isinstance(seq, int):
                if seqs and seq <= seqs[-1]:
                    bad_seq.append(f"non-monotonic seq {seq} after {seqs[-1]}")
                seqs.append(seq)
                if event_id is not None and event_id != str(seq):
                    bad_seq.append(f"id {event_id} != seq {seq}")
            frames.append({"event": event, "seq": seq})
            if event == "job":
                job_payload = body
            elif event.startswith("tool_"):
                tool_events.append({key: body.get(key) for key in
                                    ("tool_name", "display_name", "tool_call_id",
                                     "message", "status", "tool_status",
                                     "summary", "action_hint")
                                    if key in body})
            elif event == "done":
                done_payload = body
                break
            elif event == "error":
                error_payload = body

    check("SSE seq/id monotonic", not bad_seq, "; ".join(bad_seq[:3]))
    check("job frame carries identity/protocol",
          bool(job_payload) and job_payload.get("protocol") == "citrus-stream/1"
          and bool(job_payload.get("job_id")) and bool(job_payload.get("session_id"))
          and job_payload.get("client_request_id") == client_request_id,
          json.dumps({k: job_payload.get(k) for k in
                      ("protocol", "job_id", "session_id", "client_request_id")},
                     ensure_ascii=False) if job_payload else "no job frame")
    job_id = str((job_payload or {}).get("job_id") or "")
    session_id = str((job_payload or {}).get("session_id")
                     or (done_payload or {}).get("session_id") or "")
    check("terminal done frame received", done_payload is not None,
          ("error: " + json.dumps(error_payload, ensure_ascii=False)[:300])
          if error_payload else f"events={len(frames)}")

    envelope = (done_payload or {}).get("turn_envelope") or {}
    display = envelope.get("display_status") or (done_payload or {}).get("turn_status")
    done_seq = next((f["seq"] for f in frames if f["event"] == "done"), None)
    check("turn completed (server envelope)",
          display == "completed", f"display_status={display} "
          f"execution={envelope.get('execution_status')} acceptance={envelope.get('acceptance_status')}")
    check("terminal_seq matches done frame seq",
          isinstance(done_seq, int) and envelope.get("terminal_seq") == done_seq,
          f"done seq={done_seq} terminal_seq={envelope.get('terminal_seq')}")
    check("history saved", bool((done_payload or {}).get("history_saved",
                                                         envelope.get("history_saved"))),
          f"history_saved={envelope.get('history_saved')}")
    artifact_ids = [str(x) for x in ((done_payload or {}).get("artifact_ids")
                                     or envelope.get("artifact_ids") or [])]
    check("done carries verified artifact ids", bool(artifact_ids),
          f"artifact_ids={artifact_ids[:4]} verified={envelope.get('verified_artifacts')}")

    def _is_word_event(event: dict) -> bool:
        text = " ".join(str(event.get(key) or "") for key in
                        ("tool_name", "display_name", "message", "summary",
                         "action_hint")).lower()
        return "word" in text or "文档" in text

    word_events = [e for e in tool_events if _is_word_event(e)]
    check("word tool executed in stream", bool(word_events),
          f"tool_events={len(tool_events)} word_events={len(word_events)} "
          f"names={sorted({str(e.get('display_name') or e.get('tool_name') or '') for e in tool_events})[:5]}")

    gate = (done_payload or {}).get("export_gate") or {}
    check("delivery gate satisfied without gaps",
          gate.get("requested") is True and gate.get("status") == "satisfied"
          and not gate.get("missing_requirements"),
          json.dumps({"status": gate.get("status"),
                      "missing": gate.get("missing_requirements"),
                      "initiator": gate.get("initiator")},
                     ensure_ascii=False))
    call_id = ""
    for event in word_events:
        if event.get("tool_call_id"):
            call_id = str(event["tool_call_id"])

    answer = str((done_payload or {}).get("answer") or "")
    check("answer text non-empty", bool(answer.strip()), f"{len(answer)} chars")

    # ---- 4. job + history ---------------------------------------------------
    if job_id:
        job = session.get(base + f"/jobs/{job_id}", timeout=20)
        job_body = job.json() if job.status_code == 200 else {}
        snapshot = job_body.get("terminal_snapshot") or {}
        check("job terminal state persisted",
              job.status_code == 200 and job_body.get("status") == "completed"
              and snapshot.get("display_status") == display,
              f"HTTP {job.status_code} status={job_body.get('status')} "
              f"snapshot={snapshot.get('display_status')}")
    else:
        check("job terminal state persisted", False, "no job_id")

    history_items: list[dict] = []
    if session_id:
        turns = session.get(base + f"/api/v2/session/{session_id}/turns",
                            params={"limit": 50}, timeout=20).json()
        turn_list = turns.get("turns") or []
        mine = [t for t in turn_list
                if str(t.get("turn_request_id") or t.get("request_id") or "")
                == client_request_id]
        check("canonical turn index has this request",
              bool(mine), f"turns={len(turn_list)} matched={len(mine)}")

        history = session.get(base + f"/api/v2/session/{session_id}/messages",
                              params={"limit": 50}, timeout=30).json()
        items = history.get("messages") or []
        history_items = items
        user_items = [m for m in items if m.get("role") == "user"
                      and str(m.get("turn_request_id") or "") == client_request_id]
        assistant_items = [m for m in items if m.get("role") == "assistant"
                           and str(m.get("turn_request_id") or "") == client_request_id]
        check("history keeps the real question",
              any(QUERY[:24] in str(m.get("content") or "") for m in user_items),
              f"user_items={len(user_items)}")
        check("history keeps an assistant answer for this turn",
              bool(assistant_items) and
              any(str(m.get("content") or "").strip() for m in assistant_items),
              f"assistant_items={len(assistant_items)}")
    else:
        check("canonical turn index has this request", False, "no session_id")

    # ---- 5. download + docx integrity --------------------------------------
    docx_path = None
    if session_id and artifact_ids:
        files = session.get(base + f"/api/v2/session/{session_id}/files",
                            timeout=20).json()
        generated = files.get("generated") or []
        matched = [f for f in generated
                   if str(f.get("artifact_id") or "") in artifact_ids
                   or str(f.get("file_id") or "") in artifact_ids]
        if not matched:
            matched = [f for f in generated
                       if str(f.get("turn_request_id") or "") == client_request_id]
        check("artifact attributed to this turn",
              bool(matched) and any(str(f.get("turn_request_id") or "") == client_request_id
                                    for f in matched),
              f"generated={len(generated)} matched={len(matched)}")
        if matched:
            record = matched[0]
            download = session.get(base + str(record.get("download_url")
                                              or f"/api/v2/files/{record['file_id']}/download"),
                                   timeout=60)
            blob = download.content
            check("download returns bytes",
                  download.status_code == 200 and len(blob) > 2000,
                  f"HTTP {download.status_code} bytes={len(blob)} "
                  f"type={download.headers.get('Content-Type')}")
            declared = int(record.get("size_bytes") or -1)
            check("download size matches library record", len(blob) == declared,
                  f"download={len(blob)} record={declared}")
            digest = hashlib.sha256(blob).hexdigest()
            check("download sha256 matches library record",
                  not record.get("sha256") or digest == record.get("sha256"),
                  f"sha256={digest[:16]}… record={str(record.get('sha256'))[:16]}…")
            check("download mime is docx",
                  "wordprocessingml" in str(download.headers.get("Content-Type") or ""),
                  str(download.headers.get("Content-Type")))
            try:
                raw_xml, names, text = docx_text(blob)
                check("docx zip opens and passes CRC",
                      "[Content_Types].xml" in names and "word/document.xml" in names,
                      f"members={len(names)}")
                check("docx contains title/section/body verbatim",
                      TITLE in text and SECTION in text and BODY in text,
                      f"text_chars={len(text)}")
                check("docx document.xml is complete",
                      raw_xml.rstrip().endswith("</w:document>"),
                      f"xml_chars={len(raw_xml)}")
                docx_path = OUT_DIR / f"smoke_{time.strftime('%Y%m%d_%H%M%S')}.docx"
                docx_path.write_bytes(blob)
            except Exception as exc:  # noqa: BLE001 - report as a failed check
                check("docx zip opens and passes CRC", False,
                      f"{type(exc).__name__}: {exc}")
    else:
        check("artifact attributed to this turn", False,
              "no session_id or artifact_ids")

    # ---- 6. tool evidence ---------------------------------------------------
    if not call_id:
        for item in history_items:
            for call in item.get("calls") or []:
                text = f"{call.get('name') or ''} {call.get('summary') or ''}".lower()
                if "word" in text or "文档" in text:
                    call_id = str(call.get("id") or "")
    if session_id and call_id:
        detail = session.get(base + f"/api/v2/session/{session_id}/tool-result/{call_id}",
                             timeout=20)
        record = detail.json() if detail.status_code == 200 else {}
        check("tool result persisted for the word call",
              detail.status_code == 200
              and record.get("status") in {"complete", "completed"},
              f"HTTP {detail.status_code} status={record.get('status')} "
              f"chars={record.get('result_chars')}")
    else:
        check("tool result persisted for the word call", False,
              "no word tool_call_id captured")

    # ---- 7. cleanup ---------------------------------------------------------
    if not args.keep_account:
        removed = session.delete(base + "/api/v2/auth/account",
                                 json={"password": args.password}, timeout=60)
        check("temporary account deleted", removed.status_code == 200,
              f"HTTP {removed.status_code} {removed.text[:160]}")
    else:
        note("account kept on request: " + args.username)

    report_path = _write_report(args, started, done_payload, docx_path,
                                {"export_gate": (done_payload or {}).get("export_gate"),
                                 "answer": (done_payload or {}).get("answer"),
                                 "tool_events": tool_events})
    failed = [c for c in CHECKS if not c["ok"]]
    print(f"\n{len(CHECKS) - len(failed)}/{len(CHECKS)} checks passed; report={report_path}",
          flush=True)
    return 1 if failed else 0


def _write_report(args, started, done_payload, docx_path, details=None) -> Path:
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "base": args.base,
        "username": args.username,
        "query": QUERY,
        "elapsed_sec": round(time.time() - started, 1),
        "checks": CHECKS,
        "passed": sum(1 for c in CHECKS if c["ok"]),
        "failed": sum(1 for c in CHECKS if not c["ok"]),
        "done_envelope": (done_payload or {}).get("turn_envelope"),
        "done_artifact_ids": (done_payload or {}).get("artifact_ids"),
        "docx_copy": str(docx_path) if docx_path else "",
        "details": details or {},
    }
    path = OUT_DIR / f"smoke_{time.strftime('%Y%m%d_%H%M%S')}.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(main())
