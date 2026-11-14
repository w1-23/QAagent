"""Authenticated tool adapter for bounded image-reuse evidence reports."""
from __future__ import annotations

import hashlib
import json
import logging
import re

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict, Field

from src.core.account_leases import AccountUnavailable, account_lease
from src.core.cancel import ToolCancelled, raise_if_cancelled
from src.core import tracing
from src.research.artifacts import publish_artifact
from src.research.image_integrity import (
    ImageIntegrityError, ImageSource, Limits, analyze_image_reuse,
)

logger = logging.getLogger(__name__)
_LIMITS = Limits()
_TOOL_NAME = "audit_research_images"
_REPORT_FILENAME = "image_reuse_evidence.json"
_SAMPLE_LIMIT = 3


class ImageAuditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    file_ids: list[str] = Field(
        min_length=1, max_length=8,
        description="本账号资料库中 1–8 个不同的图片 file_id；单图可查局部复用/面板/动态范围/来源声明，不接受路径、URL 或账号参数。")


def _result(payload: dict, status: str, reason: str, coverage: str) -> tuple[str, dict]:
    review = payload.get("integrity_review") or {}
    facts = {"findings_count": payload.get("finding_count", 0), "coverage": coverage,
             "review_url": review.get("review_url", "")}
    refs = ([{"kind": "integrity_review", "review_id": review["review_id"], "url": review["review_url"]}]
            if review.get("review_id") else [])
    if payload.get("report"):
        refs.append({"kind": "artifact", **payload["report"]})
    payload.update(facts=facts, result_refs=refs)
    payload["_tool_outcome"] = {
        "status": status, "reason_code": reason, "coverage": coverage,
        "facts": facts, "result_refs": refs,
        "artifacts": [payload["report"]["artifact_url"]] if payload.get("report") else [],
    }
    return json.dumps(payload, ensure_ascii=False, allow_nan=False), payload


def _failed(code: str, message: str, reason: str = "tool_error") -> tuple[str, dict]:
    return _result({"analysis_status": "failed", "publication_status": "not_run",
                    "human_review_required": True,
                    "error": {"code": code, "message": message}, "report": None},
                   "failed", reason, "未完成图像核查，未确认报告交付")


def _load_sources(file_ids: list[str], owner: int) -> list[ImageSource]:
    from src.session import files

    if (not isinstance(file_ids, list) or not 1 <= len(file_ids) <= _LIMITS.max_images
            or any(not isinstance(fid, str) or not re.fullmatch(r"[a-f0-9]{32}", fid)
                   for fid in file_ids) or len(set(file_ids)) != len(file_ids)):
        raise ImageIntegrityError("invalid_file_ids", "需要 1–8 个不同的本账号图片 file_id，不接受路径或 URL。")
    sources, used = [], 0
    for fid in file_ids:
        raise_if_cancelled("image_audit_read")
        # The existing owner-scoped resolver is authoritative; never infer an
        # owner from a file, session, model argument, or generated-artifact path.
        entry = files.get_account_file(fid, owner, touch=False)
        if not entry:
            raise ImageIntegrityError("source_unavailable", "图片不存在、已失效或不属于当前账号。")
        expected = str(entry.get("sha256") or "")
        if not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ImageIntegrityError("checksum_mismatch", "图片缺少有效的登记摘要。")
        remaining = min(_LIMITS.max_image_bytes, _LIMITS.max_total_bytes - used)
        declared = int(entry.get("size_bytes", -1))
        if remaining <= 0 or declared < 0 or declared > remaining:
            raise ImageIntegrityError("byte_limit", "图片超过单图或本次总读取字节上限。")
        try:
            with entry["path"].open("rb") as handle:
                # Do not trust stale index sizes and never use unbounded read_bytes.
                data = handle.read(remaining + 1)
        except OSError as exc:
            raise ImageIntegrityError("source_unavailable", "图片读取失败或已失效。") from exc
        if len(data) > remaining:
            raise ImageIntegrityError("byte_limit", "实际图片字节超过单图或本次总读取上限。")
        used += len(data)
        if len(data) != declared or hashlib.sha256(data).hexdigest() != expected:
            raise ImageIntegrityError("checksum_mismatch", "图片实际字节与资料库登记不一致。")
        sources.append(ImageSource(fid, data, expected_sha256=expected))
    return sources


def _verify_report(publication: dict, content: bytes, owner: int, session_id: str) -> dict:
    from src.session import files

    fid = str(publication.get("file_id") or "")
    entry = files.get_account_file(fid, owner, touch=False)
    expected = hashlib.sha256(content).hexdigest()
    if (not entry or entry.get("session_id") != session_id
            or entry.get("origin") != files.ORIGIN_GENERATED
            or entry.get("producer_tool") != _TOOL_NAME
            or entry.get("artifact_id") != publication.get("artifact_id")
            or not entry.get("artifact_id")
            or entry.get("filename") != _REPORT_FILENAME
            or entry.get("sha256") != expected or publication.get("sha256") != expected
            or entry.get("size_bytes") != len(content)
            or not entry.get("download_url")
            or entry.get("download_url") != publication.get("download_url")):
        raise ValueError("published report registration is unavailable")
    with entry["path"].open("rb") as handle:
        stored = handle.read(len(content) + 1)
    if stored != content:
        raise ValueError("published report bytes do not match evidence")
    from src.research.tools import validate_artifact_links
    artifact_url = str(publication.get("artifact_url") or "")
    validations = validate_artifact_links(
        [artifact_url], session_id=session_id, owner_user_id=owner,
        request_id=tracing.get_client_request_id() or "")
    if (len(validations) != 1 or not validations[0].get("valid")
            or validations[0].get("producer_tool") != _TOOL_NAME
            or validations[0].get("artifact_id") != entry["artifact_id"]):
        raise ValueError("published artifact is not accepted by the delivery validator")
    return {**{key: entry[key] for key in (
        "file_id", "artifact_id", "filename", "sha256", "size_bytes", "download_url")},
        "artifact_url": artifact_url}


def _audit(file_ids: list[str], owner: int, session_id: str) -> tuple[str, dict]:
    from src.session.manager import session_manager

    if (not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", session_id)
            or session_manager.get_session_owner(session_id) != owner):
        return _failed("session_unavailable", "当前账号没有可用的已授权会话。", "auth")
    try:
        sources = _load_sources(file_ids, owner)
        report = analyze_image_reuse(sources, limits=_LIMITS, near_duplicates=True, local_checks=True)
        raise_if_cancelled("image_audit_analysis_complete")
    except ToolCancelled:
        raise
    except ImageIntegrityError as exc:
        reason = "invalid_arguments" if exc.code == "invalid_file_ids" else "source_unavailable"
        return _failed(exc.code, str(exc), reason)
    except Exception as exc:
        logger.warning("Image evidence execution failed: %s", type(exc).__name__)
        return _failed("analysis_failed", "图像证据计算失败，未生成可确认结果。")

    coverage = report["coverage"]
    partial = report["status"] == "partial"
    review = {"status": "failed"}
    try:
        from src.research.integrity_review import record_report
        for item in report.get("common_findings", []):
            for loc in item["locations"]:
                loc["file_id"] = loc["source_id"]
        review = {"status": "complete", **record_report(owner, session_id, report)}
        report["integrity_review"] = review
    except ToolCancelled:
        raise
    except Exception as exc:
        logger.warning("Image review persistence failed: %s", type(exc).__name__)
        review = {"status": "failed", "reason": "review_persistence_failed"}
        partial = True
    payload = {
        "method_version": report["method_version"],
        "analysis_status": "partial" if partial else "complete",
        "publication_status": "pending", "human_review_required": True,
        "finding_count": len(report["findings"]) + len(report.get("local_findings", [])),
        "local_finding_count": len(report.get("local_findings", [])),
        "provenance_inventory_count": len(report.get("provenance_findings", [])),
        "local_evidence_samples": [{**{key: item[key] for key in ("finding_id", "rule", "summary", "locations")},
                                    "evidence": {key: value for key, value in item["evidence"].items() if key != "histogram"}}
                                   for item in report.get("local_findings", [])[:1]],
        "evidence_samples": report["findings"][:_SAMPLE_LIMIT],
        "sample_limit": _SAMPLE_LIMIT,
        "observation_count": len(report["observations"]),
        "coverage": {key: coverage[key] for key in (
            "input_images", "total_pairs", "attempted_pairs", "completed_pairs",
            "complete_within_declared_search", "stop_reason", "crop_search",
            "candidate_comparisons", "verification_pixels")},
        "method_boundaries": [
            "精确对应验证 RGBA 像素；近似候选使用 ORB/RANSAC 几何与灰度重叠验证，可覆盖部分缩放/旋转/重压缩，未命中不代表无复用。",
            "图内复用用分离区域自匹配和几何/灰度核对；边界背景阶跃与灰度端点集中仅为有限观察，合法面板、背景或曝光也可触发。",
            "来源清单只记录现有声明字段和原文件哈希，不认证采集历史；任意拼接、局部调色与无纹理复用不能保证检出。",
            "纯色与图框可能无信息；合法共享对照、许可图片和模板也会匹配。证据必须人工复核，不能证明造假。",
        ],
        "report": None, "integrity_review": review,
        "near_duplicate_coverage": report.get("near_duplicate_coverage", {}),
        "unsupported_checks": report.get("unsupported_checks", {}),
        "local_analysis_coverage": {
            "status": report.get("local_analysis_coverage", {}).get("status"),
            "source_count": len(report.get("local_analysis_coverage", {}).get("sources", [])),
            "findings_omitted": report.get("local_analysis_coverage", {}).get("findings_omitted", 0),
            "incomplete_sources": [{"source_id": receipt["source_id"], "checks": {
                key: value for key, value in receipt["checks"].items() if value == "unavailable" or value.startswith("partial:")}}
                for receipt in report.get("local_analysis_coverage", {}).get("sources", [])
                if any(value == "unavailable" or value.startswith("partial:") for value in receipt["checks"].values())],
        },
    }
    summary = (f"跨图 {coverage['completed_pairs']}/{coverage['total_pairs']} 对完成限定搜索；"
               f"图内检查 {len(report.get('local_analysis_coverage', {}).get('sources', []))} 幅；"
               f"{payload['finding_count']} 条待复核线索；来源字段仅作清单")
    try:
        content = json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2,
                             allow_nan=False).encode("utf-8")
        source_digest = hashlib.sha256(json.dumps(
            [{"file_id": item.source_id, "sha256": item.expected_sha256} for item in sources],
            sort_keys=True).encode("utf-8")).hexdigest()
        publication = publish_artifact(
            owner=owner, session_id=session_id, filename=_REPORT_FILENAME,
            content=content, mime="application/json", producer_tool=_TOOL_NAME,
            request_id=tracing.get_client_request_id() or "", source_sha256=source_digest)
        payload["report"] = _verify_report(publication, content, owner, session_id)
        payload["publication_status"] = "complete"
    except ToolCancelled:
        raise
    except Exception as exc:
        logger.warning("Image evidence publication failed: %s", type(exc).__name__)
        payload["publication_status"] = "failed"
        payload["error"] = {"code": "publication_failed", "message": "核查结果已计算，但报告文件未确认交付；没有可用下载链接。"}
        return _result(payload, "partial", "tool_error", summary + "；报告发布失败")
    return _result(payload, "partial" if partial else "complete",
                   "coverage_incomplete" if partial else "audit_completed", summary)


@tool(args_schema=ImageAuditInput, response_format="content_and_artifact")
def audit_research_images(file_ids: list[str]) -> tuple[str, dict]:
    """核查科研图片跨图/图内复用、几何近似、面板背景边界、灰度动态范围与来源字段，发布人工复核证据。

    file_ids 为当前账号资料库中 2–8 个不同图片 ID，可核查跨轮历史图片；账号与会话由系统绑定，
    不接受 owner、磁盘路径、URL 或网络图片。只处理有字节/像素/比较预算的静态图片。
    结果包含双源 SHA256、对应框、变换、方法版本与覆盖限制；只返回少量证据样本和真实报告链接。
    合法共享对照及模板也可能匹配，纯色/图框无信息；必须人工复核，不能据此证明篡改或造假。
    """
    owner = int(tracing.get_user_id() or 0)
    if owner <= 0:
        return _failed("account_unavailable", "缺少有效的已认证账号，不能读取图片。", "auth")
    try:
        # Recheck current account state and retain deletion admission for the
        # entire read/analysis/publication, including direct tool invocations.
        with account_lease(owner):
            return _audit(file_ids, owner, str(tracing.get_session_id() or ""))
    except AccountUnavailable:
        return _failed("account_unavailable", "账号不可用，不能读取图片或发布报告。", "auth")
    except ToolCancelled:
        raise
    except Exception as exc:
        logger.warning("Image evidence admission failed: %s", type(exc).__name__)
        return _failed("admission_failed", "无法完成本次请求的授权或执行检查，未确认报告交付。")
