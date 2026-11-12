"""Concise final bubbles for completed, registry-verified Word deliveries."""
from __future__ import annotations

import re
from src.graph.export_contract import deliverable_match_map


def compact_word_delivery(answer: str, validations: list[dict], requirements: list[dict]) -> str:
    mapping = deliverable_match_map(validations, requirements)
    if len(mapping) != len(requirements):
        return answer
    selected = [validations[mapping[i]] for i in range(len(requirements))]
    if not any(str(v.get("filename", "")).lower().endswith(".docx") for v in selected):
        return answer
    links = []
    for item in selected:
        url = str(item.get("link") or "")
        if not re.fullmatch(r"/api/v2/session/[A-Za-z0-9_-]+/artifacts/[a-f0-9]{32}", url):
            return answer
        label = str(item.get("filename") or "下载文件").replace("[", "（").replace("]", "）")
        links.append(f"[{label}]({url})")
    return ("已生成可继续编辑的 Word 文档，完整正文、来源和适用限制见文件。\n\n"
            + "\n\n".join(dict.fromkeys(links)))
