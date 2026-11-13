"""Agent adapter for controlled Word drafts and existing account-file delivery."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Literal

from langchain_core.tools import tool

from src.config import PROJECT_ROOT
from src.core.deliverable_check import DeliverableRejected
from src.core.tracing import get_session_id, get_user_id, get_client_request_id
from src.core.word_documents import DocumentError, WordDocuments, MIME


def _store():
    from src.session import files
    owner = int(get_user_id() or 0)

    def image_reader(file_id):
        if len(file_id) != 32:
            raise DocumentError("invalid_image", "图片需要有效的账号 file_id。")
        entry = files.get_account_file(file_id, owner)
        if not entry or entry.get("size_bytes", 0) > 20 * 1024 * 1024:
            raise DocumentError("image_unavailable", "本账号中没有该图片，或图片超过 20 MB。")
        data = entry["path"].read_bytes()
        if hashlib.sha256(data).hexdigest() != entry["sha256"]:
            raise DocumentError("image_integrity", "图片字节与资料库登记不一致。")
        return data

    return WordDocuments(PROJECT_ROOT / "state" / "word_drafts.sqlite3", owner=owner,
                         session=str(get_session_id() or ""), image_reader=image_reader)


def _verify_publication(result):
    from src.session import files
    entry = files.get_account_file(str(result.get("file_id", "")), int(get_user_id() or 0))
    if (not entry or entry.get("artifact_id") != result.get("artifact_id")
            or entry.get("filename") != result.get("filename")
            or entry.get("mime_type") != MIME
            or entry.get("sha256") != result.get("sha256")
            or entry.get("size_bytes") != result.get("size")
            or hashlib.sha256(entry["path"].read_bytes()).hexdigest() != result.get("sha256")):
        raise DocumentError("publication_unavailable", "DOCX 资料库记录或字节验证失败；未确认交付。")


def _publish(filename, payload):
    from src.research.tools import _save_artifact
    from src.session import files
    url = _save_artifact(filename, payload, MIME)
    artifact_id = url.rsplit("/", 1)[-1]
    entries = files.list_files(str(get_session_id() or ""), int(get_user_id() or 0))
    entry = next((e for e in entries if e.get("artifact_id") == artifact_id), None)
    if not entry:
        raise DocumentError("publication_unavailable", "文件未登记到当前账号资料库。")
    # Report the registered bytes. The publisher canonicalizes OOXML container
    # timestamps, so the stored file can differ from the rendered snapshot
    # while holding exactly the same document.
    return {"artifact_id": artifact_id, "file_id": entry["file_id"],
            "filename": entry["filename"],
            "sha256": entry["sha256"], "size": entry["size_bytes"],
            "download_url": entry["download_url"], "artifact_url": url}


@tool(response_format="content_and_artifact")
def word_document(
    operation: Literal["create", "inspect", "append_blocks", "replace_blocks", "finalize"],
    document_id: str = "", title: str = "", profile: str = "general",
    outline: list[dict] | None = None, blocks: list[dict] | None = None,
    batch_id: str = "", expected_version: int | None = None,
    block_ids: list[str] | None = None, expected_last_block_id: str = "",
    filename: str = "", offset: int = 0, limit: int = 40,
) -> tuple[str, dict]:
    """受控创建/续写/修改/发布可编辑 Word；不执行模型代码，不接受任意磁盘路径。

    create: title/profile/outline=[{section_id,title}]/唯一batch_id；返回document_id/version。
    append_blocks: document_id/batch_id/expected_version/blocks（每批≤60000字符）。
    块有block_id/type/section_id；type为heading(level 1–3)、paragraph(text或runs)、
    list_item、emphasis、table(columns/rows/caption)、image(file_id/caption/width_cm)、
    caption、reference、section_end、document_end。每节先heading和正文，最后section_end。
    完整文档最后一个块为document_end（无section_id）；各批block_id唯一。
    inspect: 查看版本、已提交块、完成章节；超时先inspect，重发原batch_id不会重复写。
    replace_blocks: block_ids指定连续的已有块，blocks为替换内容，其他部分保留。
    finalize: expected_version、expected_last_block_id、filename='.docx'；只有完成所有
    章节并验证真实文件库登记才返回下载链接。草稿不算可交付文件。
    五种profile: experiment_plan/methods_comparison/data_audit/research_report/general。
    暂不支持上传任意既有DOCX的保真局部编辑或期刊模板导入，请明确说明限制。
    """
    try:
        store = _store()
        if operation == "create":
            result = store.create(title=title, profile=profile, outline=outline, batch_id=batch_id)
        elif operation == "inspect":
            result = store.inspect(document_id, offset=offset, limit=limit)
        elif operation in {"append_blocks", "replace_blocks"}:
            result = store.mutate(document_id, operation=operation, batch_id=batch_id,
                                  expected_version=expected_version, blocks=blocks, block_ids=block_ids)
        elif operation == "finalize":
            result = store.finalize(document_id, expected_version=expected_version,
                                    expected_last_block_id=expected_last_block_id, filename=filename,
                                    publisher=_publish, verifier=_verify_publication,
                                    publication_scope=str(get_client_request_id() or ""))
        else:
            raise DocumentError("invalid_operation", "不支持的文档操作。")
    except (DocumentError, DeliverableRejected, ValueError, OSError, sqlite3.Error) as exc:
        # Argument/size faults may succeed with a smaller, corrected batch;
        # authorization, conflict and integrity faults may not be retried blindly.
        code = getattr(exc, "code", "word_document_failed")
        if code in {"invalid_argument", "invalid_table", "invalid_outline", "invalid_profile",
                    "invalid_filename", "invalid_image", "batch_too_large", "document_too_large"}:
            code = "invalid_arguments"
        result = {"operation": operation, "document_id": document_id, "error": str(exc),
                  "_tool_outcome": {"status": "failed", "reason_code": code, "artifacts": []}}
        return f"[WORD_DOCUMENT_ERROR:{code}] {exc}", result
    result["operation"] = operation
    published = operation == "finalize"
    result["_tool_outcome"] = {"status": "complete", "reason_code": "word_published" if published else "draft_saved",
                              "artifacts": [result["artifact_url"]] if published else []}
    if published:
        text = f"Word 文档已生成：[下载 {result['filename']}]({result['artifact_url']})。可继续编辑。"
    else:
        text = json.dumps(result, ensure_ascii=False)
    return text, result
