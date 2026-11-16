"""Request-scoped PDF citation extraction, highlighting and OA lookup."""
from __future__ import annotations

import base64
import re
from typing import Any


def _reference_start(pages: list[str]) -> tuple[int, int] | None:
    pattern = re.compile(r"^\s*(references|bibliography|literature cited|参考文献)\s*$", re.I)
    for page_no, text in enumerate(pages):
        for line_no, line in enumerate(text.splitlines()):
            if pattern.match(line):
                return page_no, line_no
    return None


def review_pdf(name: str, data_url: str, session_id: str, *, owner_user_id: int | None = None) -> dict[str, Any]:
    raw = str(data_url or "")
    if raw.startswith("data:") and "," in raw:
        raw = raw.split(",", 1)[1]
    try:
        content = base64.b64decode(raw, validate=True)
    except Exception as exc:
        raise ValueError("PDF 内容无法解码") from exc
    if len(content) > 20 * 1024 * 1024:
        raise ValueError("PDF 超过 20 MB 限制")
    if not content.startswith(b"%PDF"):
        raise ValueError("文件不是有效 PDF")

    import fitz
    doc = fitz.open(stream=content, filetype="pdf")
    pages = [doc.load_page(i).get_text("text") for i in range(min(len(doc), 300))]
    from src.research.reference_extraction import extract_inventory
    from src.research.reference_audit import _create_inventory
    try:
        inventory = extract_inventory(name=name, content=content, _pdf_pages=pages, _pdf_page_count=len(doc))
        audit = _create_inventory(owner_user_id, session_id, inventory) if owner_user_id is not None else None
    except BaseException:
        doc.close()
        raise
    audit_info = ({'audit_id': audit['audit_id'], 'audit': {key: value for key, value in audit.items() if key != 'rows'}}
                  if audit else {})
    if not any(text.strip() for text in pages):
        doc.close()
        return {"status": "incomplete", "reason": "未提取到文本，可能是扫描版 PDF；未执行 OCR。",
                "references": [], "highlighted_pdf_url": "", "pages": len(pages),
                'extraction': inventory['extraction'], **audit_info}
    start = _reference_start(pages)
    refs = [{**entry['claims'], 'number': entry['record_index'] + 1,
             'record_id': entry['record_id'], 'record_index': entry['record_index'],
             'text': entry['citation'], 'positions': entry['positions'],
             'parse_status': entry['parse_status']}
            for entry in inventory['entries']]

    # Add visible PDF highlight annotations to numbered in-text citation markers.
    citation_pattern = re.compile(
        r"\[(?:\d{1,3})(?:\s*[,–-]\s*\d{1,3})*\]"
        r"|\([A-Z][A-Za-z'’.-]+(?:\s+et\s+al\.)?[,]?\s*(?:19|20)\d{2}[a-z]?(?:\s*;[^)]*)?\)"
    )
    for page_no, text in enumerate(pages):
        if start and page_no > start[0]:
            continue
        page = doc.load_page(page_no)
        for match in citation_pattern.finditer(text):
            if start and page_no == start[0]:
                lines = text.splitlines(keepends=True)
                heading_offset = sum(len(line) for line in lines[:start[1]])
                if match.start() >= heading_offset:
                    continue
            for rect in page.search_for(match.group(0))[:10]:
                annot = page.add_highlight_annot(rect)
                annot.set_info(title="CitrusScholar 引用标记", content="正文中检测到的编号引用，供人工核对。")
                annot.update()
    output = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    if owner_user_id is not None:
        from src.research.artifacts import publish_artifact
        receipt = publish_artifact(owner=owner_user_id, session_id=session_id,
            filename=_safe_pdf_name(name), content=output, mime="application/pdf",
            producer_tool="paper_review", request_id=audit['audit_id'])
        artifact_url = receipt['artifact_url']
    else:
        # Retain the internal adapter for callers that already bind identity.
        from src.research.tools import _save_artifact
        artifact_url = _save_artifact(_safe_pdf_name(name), output, "application/pdf")
    return {"status": "partial",
            "reason": "参考文献使用保守解析，条目边界和字段需复核；不代表全文参考文献已全部识别。",
            "pages": len(pages), "references": refs, 'extraction': inventory['extraction'],
            "highlighted_pdf_url": artifact_url, **audit_info}


def _safe_pdf_name(name: str) -> str:
    base = re.sub(r"[^\w.-]+", "_", str(name or "paper.pdf"), flags=re.UNICODE).strip("._") or "paper.pdf"
    if not base.lower().endswith(".pdf"):
        base += ".pdf"
    return base[:100]


def verify_reference(doi: str = "", title: str = "", citation_text: str = "") -> dict[str, Any]:
    """Legacy one-entry projection of the same audit comparison rules."""
    from src.research.reference_audit import verify_record
    from src.research.reference_extraction import parse_reference_claims
    claims = parse_reference_claims(citation_text) if citation_text else {}
    uncertain = list(claims) if citation_text else []
    if doi:
        claims['doi'] = doi
        uncertain = [key for key in uncertain if key != 'doi']
    if title:
        claims['title'] = title
        uncertain = [key for key in uncertain if key != 'title']
    result = verify_record(claims, uncertain_fields=uncertain)
    result.update(registered_title=result.get('metadata_title', ''),
                  registered_doi=(result.get('candidates') or [{}])[0].get('doi', ''),
                  authors=result.get('metadata_authors', ''), journal=result.get('metadata_journal', ''),
                  year=result.get('metadata_year', ''),
                  checks={key: value['status'] for key, value in result.get('fields', {}).items()},
                  message=result.get('detail', '未完成核验；不能据此判断文献不存在。'))
    return result


def fetch_open_access_pdf(doi: str, session_id: str) -> dict[str, Any]:
    """Find and download only a PDF exposed by Europe PMC's OA service."""
    import requests
    from xml.etree import ElementTree as ET
    from urllib.parse import urljoin
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", str(doi or "").strip(), flags=re.I)
    if not re.fullmatch(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", doi, re.I):
        return {"status": "unavailable", "message": "没有有效 DOI，无法按 DOI 查找开放全文。"}
    try:
        result = requests.get("https://www.ebi.ac.uk/europepmc/webservices/rest/search",
                              params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 1}, timeout=12)
        result.raise_for_status()
        rows = (result.json().get("resultList") or {}).get("result") or []
        pmcid = next((str(row.get("pmcid") or "") for row in rows if row.get("pmcid")), "")
        if not pmcid:
            return {"status": "not_open_access", "message": "未找到 Europe PMC 开放全文；可能是付费文献或尚未收录。"}
        metadata = requests.get(f"https://www.ncbi.nlm.nih.gov/pmc/utils/oa/oa.fcgi?id={pmcid}", timeout=12)
        metadata.raise_for_status()
        root = ET.fromstring(metadata.content)
        href = next((node.attrib.get("href", "") for node in root.iter() if node.tag.lower().endswith("link") and node.attrib.get("format") == "pdf"), "")
        if not href:
            return {"status": "not_open_access", "message": "该记录没有可直接下载的开放 PDF。"}
        pdf_url = urljoin("https://ftp.ncbi.nlm.nih.gov/", href.replace("ftp://ftp.ncbi.nlm.nih.gov/", "https://ftp.ncbi.nlm.nih.gov/"))
        parsed = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(pdf_url)
        if parsed.scheme != "https" or parsed.hostname not in {"ftp.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov"}:
            return {"status": "unavailable", "message": "开放全文来源不在允许的公共仓储域名内。"}
        allowed_hosts = {"ftp.ncbi.nlm.nih.gov", "pmc.ncbi.nlm.nih.gov", "www.ncbi.nlm.nih.gov",
                         "ncbi.nlm.nih.gov", "cdn.ncbi.nlm.nih.gov", "www.ebi.ac.uk"}
        response = None
        for _ in range(4):
            parsed = __import__("urllib.parse", fromlist=["urlsplit"]).urlsplit(pdf_url)
            if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
                return {"status": "unavailable", "message": "开放全文重定向到未允许的域名，已停止下载。"}
            response = requests.get(pdf_url, timeout=30, stream=True, allow_redirects=False)
            if response.is_redirect or response.is_permanent_redirect:
                target = response.headers.get("Location", "")
                if not target:
                    break
                pdf_url = urljoin(pdf_url, target)
                continue
            response.raise_for_status()
            break
        if response is None or response.is_redirect or response.is_permanent_redirect:
            return {"status": "unavailable", "message": "开放全文重定向次数过多，未下载。"}
        chunks = []; size = 0
        for chunk in response.iter_content(1024 * 1024):
            if not chunk: continue
            size += len(chunk)
            if size > 30 * 1024 * 1024:
                return {"status": "too_large", "message": "开放全文超过 30 MB，未下载。"}
            chunks.append(chunk)
        content = b"".join(chunks)
        if not content.startswith(b"%PDF"):
            return {"status": "unavailable", "message": "开放仓储返回的文件不是有效 PDF。"}
        from src.research.tools import _save_artifact
        filename = re.sub(r"[^A-Za-z0-9._-]+", "_", pmcid) + ".pdf"
        url = _save_artifact(filename, content, "application/pdf")
        return {"status": "downloaded", "message": "已从 Europe PMC 开放仓储获取 PDF。", "download_url": url}
    except Exception as exc:
        return {"status": "unverified", "message": f"开放全文查询失败（{type(exc).__name__}）。"}
