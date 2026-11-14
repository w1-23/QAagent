"""Host-side acceptance of the installed Nature worker's actual QA receipt."""
from __future__ import annotations
import hashlib
import re

COMMIT = "7bff1d0539c0dfad6c30178bb93ce6dd8a5cebf8"


def validate_nature_receipt(manifest: dict, *, pdf: bytes | None = None) -> dict:
    receipt = manifest.get("nature_skill_receipt")
    if not isinstance(receipt, dict) or receipt.get("version") != 1 or receipt.get("commit") != COMMIT:
        raise ValueError("工作器未返回当前 Nature 核心检查回执；请部署匹配版本的科研沙箱。")
    checks = receipt.get("checks")
    allowed = {
        "source_preflight": {"pass", "review_required"},
        "panel_alignment": {"pass", "warn", "not_applicable"},
        "pdf_text": {"pass", "warn"},
        "pdf_collisions": {"pass", "warn"},
    }
    if not isinstance(checks, dict) or any(
        not isinstance(checks.get(name), dict) or checks[name].get("status") not in states
        for name, states in allowed.items()
    ):
        raise ValueError("Nature 图件检查失败或无法审计；保留诊断，未发布图件。")
    if receipt.get("status") not in {"pass", "warn", "review_required"}:
        raise ValueError("Nature 图件检查总状态无效。")
    digest = receipt.get("pdf_sha256")
    if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Nature 检查缺少最终 PDF 摘要。")
    if pdf is not None and hashlib.sha256(pdf).hexdigest() != digest:
        raise ValueError("发布 PDF 与 Nature 检查的版本不一致。")
    return receipt
