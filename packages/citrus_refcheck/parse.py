"""Citation line parsing, retaining heuristic uncertainty."""
import re

def _parse_reference_line(line: str, doi: str) -> dict:
    """从参考文献条目文本里尽力拆出题名、作者、年份（供元数据错配检测）。"""
    text = re.sub(r"^\s*[\[（(]?\d{1,3}[\]）)]?[.、)]?\s*", "", line or "").strip()
    text = re.sub(r"(?i)https?://(?:dx\.)?doi\.org/\S+", "", text)
    text = re.sub(r"(?i)\bdoi\s*[:：]?\s*\S+", "", text).strip(" .;，,、")
    year = ""
    match_year = re.search(r"\b(19|20)\d{2}\b", text)
    if match_year:
        year = match_year.group(0)
    authors = ""
    title = ""
    if match_year:
        head = text[: match_year.start()].strip(" ().,;")
        tail = text[match_year.end():].strip(" ().,;")
        authors = head[:200]
        pieces = [p for p in re.split(r"(?<=[.!?])\s+", tail) if p.strip()]
        if pieces:
            first = pieces[0].strip()
            if len(first) < 25 and len(pieces) > 1:
                first = (first + " " + pieces[1].strip()).strip()
            title = first[:400]
    else:
        title = text[:400]
    return {"title": title, "authors": authors[:200], "year": year}

