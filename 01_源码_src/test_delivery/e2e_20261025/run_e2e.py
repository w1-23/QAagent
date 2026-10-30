from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import sqlite3
import string
import time
import zipfile
from datetime import datetime
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
DOWNLOADS = HERE / "downloads"
RUNTIME = HERE / "runtime"
BASE = "http://127.0.0.1:8011"
TIMEOUT = (15, 360)
DOWNLOADS.mkdir(parents=True, exist_ok=True)
session = requests.Session()
session.headers.update({"Origin": BASE})
results: list[dict] = []
event_log: list[dict] = []
test_session_id = ""
_URL_AUTH = re.compile(r"(?i)(https?://)[^\s/:@]+:[^\s/@]+@")


def redact_text(value: str) -> str:
    """Remove embedded HTTP basic-auth credentials from captured remote content."""
    return _URL_AUTH.sub(r"\1", str(value))


def redact_value(value):
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, dict):
        return {key: redact_value(item) for key, item in value.items()}
    return value


def redact_file_bytes(data: bytes) -> tuple[bytes, bool]:
    """Redact credentials in text artifacts and text members of Office archives."""
    source = bytes(data)
    if zipfile.is_zipfile(io.BytesIO(source)):
        input_buffer = io.BytesIO(source)
        output_buffer = io.BytesIO()
        changed = False
        with zipfile.ZipFile(input_buffer, "r") as source_zip, zipfile.ZipFile(output_buffer, "w") as target_zip:
            for info in source_zip.infolist():
                raw = source_zip.read(info.filename)
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    safe = raw
                else:
                    safe_text = redact_text(text)
                    safe = safe_text.encode("utf-8")
                    changed = changed or safe != raw
                target_zip.writestr(info, safe)
        return (output_buffer.getvalue(), True) if changed else (source, False)
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError:
        return source, False
    safe_text = redact_text(text)
    if safe_text == text:
        return source, False
    prefix = b"\xef\xbb\xbf" if source.startswith(b"\xef\xbb\xbf") else b""
    return prefix + safe_text.encode("utf-8"), True


def redact_runtime_databases() -> int:
    """Clean any credential-bearing URLs captured in this isolated test runtime."""
    changed = 0
    databases = list((RUNTIME / "state").glob("*.sqlite3")) + list((RUNTIME / "state").glob("*.db"))
    for database in databases:
        try:
            with sqlite3.connect(database) as conn:
                tables = [row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
                for table in tables:
                    columns = [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]
                    if not columns:
                        continue
                    quoted = ",".join(f'"{column}"' for column in columns)
                    for row in conn.execute(f'SELECT rowid,{quoted} FROM "{table}"').fetchall():
                        rowid, *values = row
                        safe = [redact_text(value) if isinstance(value, str) else value for value in values]
                        if safe != values:
                            assignments = ",".join(f'"{column}"=?' for column in columns)
                            conn.execute(f'UPDATE "{table}" SET {assignments} WHERE rowid=?', (*safe, rowid))
                            changed += 1
        except sqlite3.Error:
            continue
    return changed


def attachment(filename: str) -> dict:
    path = FIXTURES / filename
    return {
        "name": path.name,
        "type": "application/octet-stream",
        "data": base64.b64encode(path.read_bytes()).decode("ascii"),
    }


def safe_event(event: str, payload: dict) -> dict:
    row = {"event": event}
    for key in ("stage", "summary", "message", "tool_name", "gen_time_ms",
                "history_saved", "warning", "tools_called", "session_id"):
        value = payload.get(key)
        if value is not None and isinstance(value, (str, int, float, bool, list, dict)):
            row[key] = redact_value(value)
    return row


def refreshed_history_check(done: dict) -> dict:
    response = session.get(
        f"{BASE}/api/v2/session/{test_session_id}/messages",
        params={"limit": 200}, timeout=30)
    response.raise_for_status()
    history = response.json()
    messages = history.get("messages") or []
    assistants = [message for message in messages if message.get("role") == "assistant"]
    latest = assistants[-1] if assistants else {}
    live_ms = int(done.get("gen_time_ms") or 0)
    restored_ms = int(latest.get("gen_time_ms") or 0)
    answer_matches = latest.get("content") == done.get("answer")
    return {
        "history_http": response.status_code,
        "message_count": len(messages),
        "last_role": latest.get("role"),
        "answer_restored": bool(answer_matches),
        "live_gen_time_ms": live_ms,
        "restored_gen_time_ms": restored_ms,
        "timer_restored": live_ms > 0 and restored_ms > 0,
        "history_saved_before_done": bool(done.get("history_saved")),
    }


def send(case: str, query: str, files: tuple[str, ...] = (), visualize: bool = False) -> dict:
    body = {
        "session_id": test_session_id,
        "query": query,
        "attachments": [attachment(name) for name in files],
        "visualization_enabled": visualize,
    }
    started = time.perf_counter()
    event_types: list[str] = []
    safe_events: list[dict] = []
    done: dict | None = None
    refresh_check: dict | None = None
    errors: list[dict] = []
    response = session.post(f"{BASE}/api/v2/chat", json=body, stream=True, timeout=TIMEOUT)
    try:
        if response.status_code != 200:
            result = {
                "case": case,
                "http_status": response.status_code,
                "elapsed_sec": round(time.perf_counter() - started, 2),
                "error": response.text[:1200],
                "completed": False,
            }
        else:
            current_event = "message"
            for raw in response.iter_lines(decode_unicode=True):
                if not raw:
                    continue
                if raw.startswith("event:"):
                    current_event = raw[6:].strip()
                    event_types.append(current_event)
                    continue
                if not raw.startswith("data:"):
                    continue
                try:
                    payload = json.loads(raw[5:].strip())
                except (ValueError, TypeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                item = safe_event(current_event, payload)
                safe_events.append(item)
                if current_event == "done":
                    done = payload
                    # Simulate the browser's immediate history restore as soon
                    # as the completion frame arrives, before the stream closes.
                    refresh_check = refreshed_history_check(done)
                elif current_event == "error":
                    errors.append({k: payload.get(k) for k in ("message", "code") if k in payload})
            result = {
                "case": case,
                "http_status": response.status_code,
                "elapsed_sec": round(time.perf_counter() - started, 2),
                "event_types": event_types,
                "tool_names": (done or {}).get("tools_called", []),
                "answer": (done or {}).get("answer", ""),
                "answer_chars": len((done or {}).get("answer", "")),
                "gen_time_ms": (done or {}).get("gen_time_ms"),
                "history_saved": (done or {}).get("history_saved"),
                "warning": (done or {}).get("warning"),
                "errors": errors,
                "tool_result_summaries": [
                    str(item.get("summary") or "")[:500]
                    for item in safe_events
                    if item.get("event") == "tool_result" and item.get("summary")
                ],
                "completed": bool(done),
            }
            if refresh_check is not None:
                result["refresh_check"] = refresh_check
            event_log.extend({"case": case, **item} for item in safe_events)
    except Exception as exc:
        result = {
            "case": case,
            "http_status": response.status_code,
            "elapsed_sec": round(time.perf_counter() - started, 2),
            "error": f"{type(exc).__name__}: {str(exc)[:800]}",
            "completed": False,
            "event_types": event_types,
        }
    finally:
        response.close()
    results.append(result)
    persist_partial()
    print(json.dumps({
        "case": case,
        "http": result.get("http_status"),
        "completed": result.get("completed"),
        "elapsed_sec": result.get("elapsed_sec"),
        "tools": result.get("tool_names", []),
        "timer_restored": (result.get("refresh_check") or {}).get("timer_restored"),
    }, ensure_ascii=False), flush=True)
    return result


def persist_partial() -> None:
    (HERE / "results.json").write_text(
        json.dumps(redact_value(results), ensure_ascii=False, indent=2), encoding="utf-8")
    (HERE / "safe_sse_log.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in event_log),
        encoding="utf-8")


def check_bad_upload() -> dict:
    response = session.post(
        f"{BASE}/api/v2/chat",
        json={"session_id": test_session_id, "query": "检查损坏工作簿",
              "attachments": [{"name": "damaged.xlsx", "type": "application/octet-stream",
                               "data": base64.b64encode(b"not a zip archive").decode("ascii")}]},
        timeout=30)
    return {
        "case": "损坏 XLSX 上传边界",
        "http_status": response.status_code,
        "expected_http_status": 400,
        "pass": response.status_code == 400,
        "detail": response.json().get("detail", "") if response.headers.get("content-type", "").startswith("application/json") else response.text[:300],
    }


def download_artifacts() -> list[dict]:
    db = RUNTIME / "state" / "research_artifacts.sqlite3"
    if not db.exists():
        return []
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT artifact_id, filename, mime, path FROM artifacts WHERE session_id=? ORDER BY created_at, artifact_id",
            (test_session_id,)).fetchall()
    downloaded = []
    for artifact_id, filename, mime, stored_path in rows:
        response = session.get(
            f"{BASE}/api/v2/session/{test_session_id}/artifacts/{artifact_id}",
            timeout=120)
        entry = {"artifact_id": artifact_id, "filename": filename, "mime": mime,
                 "http_status": response.status_code}
        if response.status_code == 200:
            target = DOWNLOADS / filename
            runtime_bytes = Path(stored_path).read_bytes()
            source_verified = hashlib.sha256(response.content).hexdigest() == hashlib.sha256(runtime_bytes).hexdigest()
            safe_content, redacted = redact_file_bytes(response.content)
            target.write_bytes(safe_content)
            entry.update({
                "saved_to": f"downloads/{filename}",
                "bytes": len(safe_content),
                "sha256": hashlib.sha256(safe_content).hexdigest(),
                "source_sha256": hashlib.sha256(response.content).hexdigest(),
                "source_integrity_verified_before_redaction": source_verified,
                "local_copy_redacted": redacted,
                "download_matches_runtime_file": source_verified,
            })
        else:
            entry["error"] = response.text[:300]
        downloaded.append(entry)
    return downloaded


def main() -> None:
    health = session.get(f"{BASE}/health", timeout=15).json()
    assert health.get("status") == "ok", health
    account_chars = string.ascii_lowercase + string.digits
    username = "e2e_" + "".join(secrets.choice(account_chars) for _ in range(12))
    password = "E2e!" + secrets.token_urlsafe(18)
    registration = session.post(
        f"{BASE}/api/v2/auth/register",
        json={"username": username, "password": password}, timeout=30)
    registration.raise_for_status()
    role = registration.json().get("user", {}).get("role")
    assert role == "user", f"public registration gave unexpected role: {role}"
    runtime_config = session.get(f"{BASE}/api/v2/config", timeout=15)
    runtime_config.raise_for_status()
    cfg = runtime_config.json()
    model = cfg.get("model") or {}

    global test_session_id
    session_response = session.post(f"{BASE}/api/v1/session/new", json={}, timeout=30)
    session_response.raise_for_status()
    test_session_id = session_response.json()["session_id"]
    metadata = {
        "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "service": BASE,
        "isolated_runtime": True,
        "account_role": role,
        "provider": model.get("provider_id"),
        "model": model.get("main"),
        "image_input": model.get("image_input"),
        "new_session_id": test_session_id,
        "credential_values_saved": False,
        "health": health,
    }
    (HERE / "environment.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")

    send("01 普通问答与刷新计时",
         "请直接计算 23+19，只返回数字。")
    send("02 实际图片输入",
         "只描述这张合成科研流程图中可见的文字、颜色和结构，不要诊断病害。",
         ("合成图像输入.png",))
    no_auto_audit = send("03 文件上传不自动审查",
         "只告诉我这份合成论文附件提取到的 DOI 和标题；不要审查数值或生成审查报告。",
         ("合成论文及引用.pdf",))
    send("04 本地英文混合检索",
         "只从本地柑橘文献语料检索关于柑橘黄龙病抗性的英文文献，说明实际命中情况；如果检索服务没有启动，要明确报告服务错误，不能说成无命中。")
    send("05 PaperConan 全集数值审查与导出",
         "请对本轮上传的四份合成 CSV 做完整统一数据审查，覆盖列关系（相同、固定差/比例、恒定和、精确线性、少数差值、连续局部偏移）、行方向关系、局部/整块复用、单列与小数末位模式、跨文件错位复用，以及缺失、混合类型、重复行、离群值和组别/重复数平衡；可适用时注明摘要统计检查。按“重复值、异常值、列关系、行向关系、局部复用、小数尾/末位、摘要统计、组别与重复、扫描覆盖”分类逐行输出；每类没有就写“无”。异常仅是人工复核线索，不判错或推断意图。生成一份自动命名的 HTML 异常报告，不修改原件。",
         ("合成_复杂数值模式.csv", "合成_行向与局部复用.csv",
          "合成_跨文件错位关系.csv", "合成_正常对照与单位换算.csv"))
    send("06 PDF 引用 DOI 核验",
         "核验 PDF 中列出的 DOI。对 10.1534/genetics.114.163105 使用 Web Search 查期刊/出版方页面，再核对登记元数据；对 10.5555/citrusqa-e2e-fake-2026 也查询。分别报告匹配、字段不一致、登记库未查到或联网失败；生成可下载的核验表。",
         ("合成论文及引用.pdf",))
    send("07 Web Search 与 Web Fetch",
         "联网搜索并打开（Web Fetch）Crossref 官方 REST API 的 DOI 元数据文档，给出简短结论、网页标题与链接。若调用失败请说明失败状态，不要把失败说成无结果。")
    send("08 安全 SVG 绘图与文件导出",
         "画一张简洁的科研数据审查流程图：上传→表格解析→数值规则筛查→模型解释→人工复核。需要安全、可编辑的 SVG；把图嵌入可下载的 HTML，并单独提供 SVG 文件，节点文字保持准确，不添加科学结论。",
         visualize=True)
    results.append(check_bad_upload())
    persist_partial()

    history_response = session.get(
        f"{BASE}/api/v2/session/{test_session_id}/messages",
        params={"limit": 300}, timeout=30)
    history_response.raise_for_status()
    history = history_response.json()
    history = redact_value(history)
    (HERE / "session_messages.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    downloads = download_artifacts()
    metadata["runtime_database_rows_redacted"] = redact_runtime_databases()
    (HERE / "downloads_index.json").write_text(
        json.dumps(downloads, ensure_ascii=False, indent=2), encoding="utf-8")
    evidence_db = RUNTIME / "state" / "sessions.db"
    evidence_rows = 0
    if evidence_db.exists():
        with sqlite3.connect(evidence_db) as conn:
            evidence_rows = conn.execute(
                "SELECT COUNT(*) FROM session_evidence WHERE session_id=?",
                (test_session_id,),
            ).fetchone()[0]
    local_diagnostics = {}
    for diagnostic_file in (RUNTIME / "logs" / "diag").glob("*.jsonl"):
        for line in diagnostic_file.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except ValueError:
                continue
            if item.get("session") != test_session_id:
                continue
            if item.get("event") == "local_query_language":
                local_diagnostics.update(
                    query_language=item.get("local_query_language"),
                    translation_used=item.get("translation_used"),
                    language_status=item.get("status"),
                )
            elif item.get("event") == "local_hybrid_search":
                local_diagnostics.update(
                    bm25_candidates=item.get("bm25_candidates"),
                    dense_candidates=item.get("dense_candidates"),
                    rrf_candidates=item.get("rrf_candidates"),
                    selected=item.get("selected"),
                    reranker_fallback=item.get("reranker_fallback"),
                    elapsed_ms=item.get("elapsed_ms"),
                )

    checks = {
        "registration_regular_user": role == "user",
        "session_created": bool(test_session_id),
        "all_completed_turns_restore_timer": all(
            not row.get("completed") or (row.get("refresh_check") or {}).get("timer_restored")
            for row in results if "refresh_check" in row),
        "history_saved_before_done": all(
            (row.get("refresh_check") or {}).get("history_saved_before_done")
            for row in results if row.get("completed")),
        "image_chat_completed": next((r.get("completed") for r in results if r["case"].startswith("02 ")), False),
        "ordinary_upload_did_not_audit": not any(
            name in {"audit_uploaded_data", "audit_uploaded_table", "audit_uploaded_data_bundle"}
            for name in no_auto_audit.get("tool_names", [])),
        "data_audit_tool_called": any(
            name in {"audit_uploaded_data", "audit_uploaded_data_bundle"}
            for row in results for name in row.get("tool_names", [])),
        "data_audit_parameter_errors_absent": not any(
            "[ERR_PARSE]" in summary
            for row in results if row["case"].startswith("05 PaperConan")
            for summary in row.get("tool_result_summaries", [])),
        "data_audit_completed": any(
            "数据审查已完成" in summary
            for row in results if row["case"].startswith("05 PaperConan")
            for summary in row.get("tool_result_summaries", [])),
        "local_retrieval_attempted": any(
            "local_hybrid_search" in row.get("tool_names", []) for row in results),
        "local_only_request_did_not_call_web": not any(
            name in {"deepseek_search", "deepseek_fetch", "deepseek_fetch_paper"}
            for row in results if row["case"].startswith("04 本地")
            for name in row.get("tool_names", [])),
        "reference_verifier_called": any(
            "verify_uploaded_references" in row.get("tool_names", []) for row in results),
        "web_search_called": any(
            any(name in {"deepseek_search", "web_search"} for name in row.get("tool_names", [])) for row in results),
        "web_fetch_called": any(
            any(name in {"deepseek_fetch", "web_fetch"} for name in row.get("tool_names", [])) for row in results),
        "web_evidence_ledger_saved": evidence_rows > 0,
        "svg_tool_called": any(
            "draw_svg" in row.get("tool_names", []) for row in results),
        "svg_validation_succeeded": any(
            "SVG 已通过安全校验" in summary
            for row in results for summary in row.get("tool_result_summaries", [])),
        "paper_export_succeeded": any(
            "export_paper_analysis" in row.get("tool_names", []) and
            "已生成 HTML" in " ".join(row.get("tool_result_summaries", []))
            for row in results),
        "svg_embedded_in_downloaded_html": any(
            item.get("http_status") == 200 and item.get("filename", "").lower().endswith(".html") and
            b"<svg" in (DOWNLOADS / item.get("filename", "")).read_bytes().lower()
            for item in downloads if (DOWNLOADS / item.get("filename", "")).exists()),
        "standalone_svg_downloaded": any(
            item.get("http_status") == 200 and item.get("filename", "").lower().endswith(".svg")
            and (DOWNLOADS / item.get("filename", "")).read_bytes().lstrip().startswith(b"<svg")
            for item in downloads if (DOWNLOADS / item.get("filename", "")).exists()),
        "corrupt_upload_rejected": results[-1].get("pass", False),
        "artifact_downloads_succeeded": bool(downloads) and all(
            row.get("http_status") == 200 and row.get("download_matches_runtime_file")
            for row in downloads),
    }
    metadata["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata["checks"] = checks
    metadata["download_count"] = len(downloads)
    metadata["evidence_ledger_rows"] = evidence_rows
    metadata["local_retrieval"] = local_diagnostics
    metadata["web_fetch_blocked_turns"] = sum(
        "WEB_FETCH_BLOCKED" in str(row.get("answer") or "")
        for row in results)
    (HERE / "environment.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    persist_partial()
    (HERE / "summary.md").write_text(render_summary(metadata, results, downloads), encoding="utf-8")
    print(json.dumps({"session_id": test_session_id, "cases": len(results),
                      "downloads": len(downloads), "checks": checks}, ensure_ascii=False), flush=True)


def render_summary(metadata: dict, rows: list[dict], downloads: list[dict]) -> str:
    lines = [
        "# CitrusQA 本地端到端测试结果",
        "",
        f"- 测试时间：{metadata.get('started_at')} 至 {metadata.get('finished_at')}",
        f"- 服务：`{metadata.get('service')}`（账号、会话和导出目录均为隔离测试数据）",
        f"- 模型：`{metadata.get('provider')}` / `{metadata.get('model')}`；图片输入声明：`{metadata.get('image_input')}`",
        f"- 新会话：`{metadata.get('new_session_id')}`；新注册账号角色：`{metadata.get('account_role')}`",
        "- 密码、API Key 未写入结果文件。",
        "",
        "## 主要验收",
        "",
        "| 检查项 | 结果 |",
        "|---|---|",
    ]
    for key, passed in (metadata.get("checks") or {}).items():
        lines.append(f"| {key} | {'通过' if passed else '未通过/未触发'} |")
    lines += ["", "## 请求逐项结果", "", "| 项目 | 完成 | 秒 | 工具 | 刷新后耗时 |", "|---|---:|---:|---|---:|"]
    for row in rows:
        refresh = row.get("refresh_check") or {}
        lines.append(
            f"| {row.get('case')} | {'是' if row.get('completed', row.get('pass')) else '否'} "
            f"| {row.get('elapsed_sec','')} | {', '.join(row.get('tool_names') or []) or '无'} "
            f"| {'恢复' if refresh.get('timer_restored') else ('不适用' if not refresh else '未恢复')} |")
    lines += ["", "## 下载产物", ""]
    if downloads:
        for item in downloads:
            lines.append(f"- `{item.get('filename')}`：HTTP {item.get('http_status')}，{item.get('bytes',0)} 字节，SHA-256 `{item.get('sha256','')}`")
    else:
        lines.append("本次没有生成可下载文件。")
    lines += [
        "",
        "## 验证方式与限制",
        "",
        "每个完整回答完成后，测试脚本立即请求会话消息接口，模拟页面刷新；对照实时 `done.gen_time_ms`、恢复消息中的 `gen_time_ms` 和最后一条回答内容。",
        "损坏 XLSX 上传通过 API 直接验证边界。其余路由由真实配置的主模型自主选择，因此工具未被调用会作为未触发记录，不伪报通过。",
        "测试文件均为合成数据；报告中的模式只代表筛查线索。联网状态以本次工具回执为准。",
        f"本地检索诊断：query_language={metadata.get('local_retrieval', {}).get('query_language')}，translation_used={metadata.get('local_retrieval', {}).get('translation_used')}，BM25={metadata.get('local_retrieval', {}).get('bm25_candidates')}，Dense={metadata.get('local_retrieval', {}).get('dense_candidates')}，RRF={metadata.get('local_retrieval', {}).get('rrf_candidates')}，selected={metadata.get('local_retrieval', {}).get('selected')}，reranker_fallback={metadata.get('local_retrieval', {}).get('reranker_fallback')}，耗时={metadata.get('local_retrieval', {}).get('elapsed_ms')} ms。",
        f"Web Fetch 被网络目标校验拦截的调用轮数：{metadata.get('web_fetch_blocked_turns', 0)}；这表示抓取通道受阻，不表示目标页面不存在。",
        "本地检索使用明确英文文献请求单独验证；Dense/LanceDB 或重排器不可用时记录实际降级情况，并核对‘仅本地’请求没有调用 Web 工具。",
        "",
        "完整回答和历史：`results.json`、`session_messages.json`；安全执行轨迹：`safe_sse_log.jsonl`；下载校验：`downloads_index.json`。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    main()
