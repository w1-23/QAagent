"""Thin chat adapter for the same durable reference audit used by the PDF panel."""
from __future__ import annotations

import json
from langchain_core.tools import tool


def _continuation(payload):
    """Method-local next step; do not change audit evidence or tool permissions."""
    action = payload.get("next_action")
    coverage = payload.get("coverage", {})
    ready = bool(payload.get("terminal") or action in {
        "export", "export_and_review", "budget_exhausted_export_partial"})
    return {
        "action": "report_saved_results" if ready else "follow_audit_next_action",
        "audit_next_action": action,
        "pending": coverage.get("pending"),
        "retryable": coverage.get("retryable"),
        "guidance": (
            "本次核验已到交付或复核边界：依据已保存结果回答，明确未检出、身份冲突、来源错误和待复核项。"
            "如需解释具体冲突，按 evidence_read 读取本条已存证据；不为证明绝对不存在或消除待复核状态，"
            "再用 search_literature/deepseek_search/deepseek_fetch 重查相同 DOI、题名、期刊或登记库 API。"
            "只有用户明确要求额外独立来源，或已存证据暴露会改变结论的具体缺口时，才按该缺口补查并说明目的；"
            "来源错误仍保留，未检出不等于虚构。用户需要文件时才按同 audit_id 导出；否则直接答复。"
            if ready else
            "库存尚未到交付边界，遵循 next_action 处理待核条目或恢复条件，不把当前分页当全部完成。"
        ),
    }


def _compact_audit(payload):
    """Project model observations only; durable audit/API/export rows stay intact."""
    keys = ("record_id", "record_index", "citation", "original", "claims", "positions",
            "source_sha256", "parse_status", "parse_error", "field_certainty", "uncertain_fields",
            "status", "existence", "identity", "fields", "errors", "attempts", "retryable", "verdict")
    from src.core.request_context import text_tokens

    rows = []
    response = {**payload, "rows": rows, "result_view": "compact"}
    for row in payload.get("rows", []):
        compact = {"existence": "not_checked", "identity": "not_checked", "fields": {}, "errors": {},
                   **{key: row[key] for key in keys if key in row}}
        compact["evidence_read"] = {
            "tool": "verify_uploaded_references",
            "args": {"operation": "read", "audit_id": payload["audit_id"],
                     "offset": row["record_index"], "max_rows": 1, "include_evidence": True}}
        rows.append(compact)
        encoded = json.dumps(response, ensure_ascii=False, allow_nan=False)
        if len(rows) > 10 or len(encoded.encode('utf-8')) > 12_000 or text_tokens(encoded) > 3500:
            rows.pop()
            response["next_offset"] = row["record_index"]
            response["read_more"] = {"operation": "read", "audit_id": payload["audit_id"],
                                      "offset": row["record_index"], "max_rows": 10}
            if not rows:
                # Keep exact row identity and an evidence pointer in the first
                # delivery even when a single original citation is very large.
                rows.append({key: compact[key] for key in ("record_id", "record_index", "status", "positions",
                            "evidence_read") if key in compact} | {"row_body_omitted": True})
                following = row["record_index"] + 1
                response["next_offset"] = following if following < payload["coverage"]["total"] else None
                response["read_more"] = ({"operation": "read", "audit_id": payload["audit_id"],
                                          "offset": following, "max_rows": 10}
                                         if response["next_offset"] is not None else None)
            break
    return response


@tool
def verify_uploaded_references(file_index: int = 0, references_json: str | list | dict = "",
                               allow_web: bool = True, batch_size: int = 10,
                               start_index: int = 0, max_rows: int = 0,
                               publish: bool = False, audit_id: str = "", text: str = "",
                               file_id: str = "", operation: str = "run",
                               resume: bool = False, offset: int = 0,
                               include_evidence: bool = False,
                               column_map: dict[str, str] | None = None, encoding: str = "") -> str:
    """核验文献存在性与书目字段，共用持久 ReferenceAudit。CSV/TSV 可用 column_map 将核验字段映射到来源列名，同名列可省略映射。默认严格读取 UTF-8 或带 BOM 的 UTF-16，GBK/GB18030 等需指定 encoding。首次提供 references_json、粘贴 text、账号 file_id 或本轮附件 file_index；返回 audit_id 后必须沿该 ID 续查，勿重建库存。默认返回紧凑逐条结果，保留无 DOI、重复条目与原文位置；完整候选、证据和 attempt_history 用各行 evidence_read（operation=read、include_evidence=True）按需回读，导出直接使用 audit_id，无需先回读全部证据。operation=run/create/read/list/cancel；run 每次最多 max_rows（默认20，上限100）并继承任务截止和持久请求预算；取消后或重试联网错误用 resume=True；terminal=True/processing_complete=True 表示库存已全部尝试且无待重试项；next_action=export_and_review 时应先交付并人工复核，即使 partial/retryable 仍为真也无需持续默认 resume；不要因 extraction partial 反复 resume。start_index 已退役，不能跳过条目。batch_size 仅保留兼容，不创建独立线程池。登记库未查到不等于虚构；存在性与逐字段准确性分别报告。allow_web 仅兼容旧接口，通用网页回退未执行，所有登记库查询仍服从当前请求的联网授权。publish=True 按此 audit_id 导出全量 XLSX；其他格式用 deliver_research_artifact(kind=bibliography,audit_id=...)。
    """
    from src.core.tracing import get_user_id, get_session_id, get_client_request_id
    from src.research import reference_audit as service
    from src.core.tool_errors import ToolArgumentError

    def rejected(message, reason):
        return json.dumps({"summary": message, "rows": [],
            "_tool_outcome": {"status": "not_run", "reason_code": reason, "artifacts": []}}, ensure_ascii=False)

    owner, session = get_user_id(), get_session_id()
    if type(owner) is not int or owner <= 0 or not session:
        return rejected("需要已认证的账号和会话。", "reference_identity_required")
    if operation not in {"create", "run", "read", "list", "cancel"}:
        return rejected("operation 仅支持 create/run/read/list/cancel。", "invalid_reference_operation")
    if start_index:
        return rejected("不再使用 start_index 跳过库存；请传 audit_id 继续未核验条目。", "audit_id_required")
    if type(max_rows) is not int or not 0 <= max_rows <= 100:
        return rejected("max_rows 需要 0–100 的整数（0 使用默认20）。", "invalid_reference_limit")
    try:
        if include_evidence and operation != "read":
            raise ToolArgumentError("include_evidence 仅用于 operation=read；运行返回紧凑结果。")
        if operation == "list":
            payload = {"audits": service.list_audits(owner, session),
                "_tool_outcome": {"status": "complete", "reason_code": "reference_audits_listed", "artifacts": []}}
        else:
            supplied = references_json
            if isinstance(supplied, str):
                supplied = json.loads(supplied) if supplied.strip() else None
            if supplied is not None and not isinstance(supplied, list):
                raise ToolArgumentError("references_json 必须是条目数组。")
            if audit_id and (supplied or text or file_id or column_map is not None or encoding):
                raise ToolArgumentError("已有 audit_id 不能同时创建新条目或文件来源。")
            if not audit_id:
                if operation in {"read", "cancel"}:
                    raise ToolArgumentError("读取或取消需要明确 audit_id。")
                if sum(bool(value) for value in (supplied, text, file_id)) > 1:
                    raise ToolArgumentError("请选择 references_json、text 或 file_id 中的一项。")
                content, name, source_ref = None, "references.txt", None
                if file_id:
                    from src.session import files
                    entry = files.get_account_file(file_id, owner)
                    if not entry:
                        raise ToolArgumentError("账号文件不存在或无权读取。")
                    if int(entry.get("size_bytes") or 0) > 20 * 1024 * 1024:
                        raise ToolArgumentError("文献文件超过20MB限制。")
                    from pathlib import Path
                    with Path(entry["path"]).open("rb") as source:
                        content = source.read(20 * 1024 * 1024 + 1)
                    if len(content) > 20 * 1024 * 1024:
                        raise ToolArgumentError("文献文件超过20MB限制。")
                    import hashlib
                    if hashlib.sha256(content).hexdigest() != entry["sha256"]:
                        raise ToolArgumentError("文件摘要不匹配，未创建审查。")
                    name, source_ref = entry["filename"], {"file_id": file_id, "sha256": entry["sha256"]}
                elif not supplied and not text:
                    from src.research.context import get_request_tables
                    attachments = get_request_tables()
                    if type(file_index) is not int or not 0 <= file_index < len(attachments):
                        raise ToolArgumentError("请提供参考文献文件、原文或条目数组。")
                    item = attachments[file_index]
                    content, name = item.get("data"), item.get("name", name)
                    source_ref = item.get("source_ref")
                    if not content:
                        raise ToolArgumentError("附件没有可读取字节；请使用实际 file_id。")
                created = service.create_audit(owner, session, name=name, content=content,
                    text=text, references=supplied, source_ref=source_ref, column_map=column_map, encoding=encoding)
                audit_id = created["audit_id"]
                payload = created
            if operation == "cancel":
                payload = service.cancel_audit(owner, session, audit_id)
            elif operation == "read":
                payload = service.get_audit(owner, session, audit_id, offset=offset, limit=1 if include_evidence else (max_rows or 10))
            elif operation == "run":
                from src.core.progress_bus import emit_progress
                payload = service.run_audit(owner, session, audit_id, max_rows=max_rows or 20,
                    resume=resume, progress=lambda event: emit_progress("reference_audit_progress", event))
            elif operation == "create" and "payload" not in locals():
                payload = service.get_audit(owner, session, audit_id)
            payload["result_ref"] = {"kind": "reference_audit", "audit_id": audit_id}
            payload["read_more"] = ({"operation": "read", "audit_id": audit_id,
                                     "offset": payload["next_offset"], "max_rows": 10}
                                    if payload.get("next_offset") is not None else None)
            payload["export_requests"] = [
                {"kind": "bibliography", "audit_id": audit_id, "output_format": fmt}
                for fmt in ("json", "xlsx")]
            payload["export_status"] = "not_requested"
            if publish:
                try:
                    from src.research.artifacts import publish_artifact
                    content, mime, filename = service.export_audit(owner, session, audit_id, format="xlsx")
                    receipt = publish_artifact(owner=owner, session_id=session, filename=filename,
                        content=content, mime=mime, producer_tool="export_literature_evidence",
                        request_id=str(get_client_request_id() or ""))
                    payload.update(download_url=receipt["artifact_url"], export_status="complete")
                    payload["_tool_outcome"]["artifacts"] = [receipt["artifact_url"]]
                except (OSError, ValueError) as exc:
                    payload.update(export_status="failed", export_error=str(exc))
                    payload["_tool_outcome"].update(status="partial", reason_code="publication_failed")
        if operation != "list":
            payload["continuation"] = _continuation(payload)
            payload = ({**payload, "result_view": "full_evidence"} if include_evidence
                       else _compact_audit(payload))
        return json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (ValueError, LookupError, PermissionError, ToolArgumentError) as exc:
        return rejected(str(exc), "reference_audit_rejected")
