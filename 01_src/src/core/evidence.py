# -*- coding: utf-8 -*-
"""证据单例 + render_evidence（v8.13-b4c，审计 §五.4 收敛点）。

chunk 全文此前在 ToolMessage / artifact / 检索回执 / 证据账本 / 历史五处各自截断
（1000 / 2000 / 3000 硬编码散落、截断 marker 也不统一），字段选择
（text → abstract → snippet）在 4 处重复实现。这里收敛为：

  - render_evidence()  单一全套字段选择 + 单一截断口径 + 统一透明标记
  - 具名预算常量       账本片段 / 回执与材料全文，按用途各一个语义名

引用编号重排（``renumber_refs`` / ``filter_refs_by_answer``）与证据编号池
（``canonical_evidence_items``）曾在本模块，现已随统一 Agent 链路下线：编号由
前端按实际回执解析，本模块不再保留没有调用方的旧实现。
"""
import re

# 单条渲染预算（语义名，替换散落各文件的 1000/2000/3000 魔数）
EVIDENCE_SNIPPET_MAX_CHARS = 2000  # 证据账本 snippet（跨轮复用/侧栏回查）
EVIDENCE_RENDER_MAX_CHARS = 3000   # 检索回执 / 写作材料包的"完整片段"安全阀

# 证据全文的字段回退顺序（text 含机制/数字细节，优先；摘要/片段次之）
_TEXT_KEYS = ("text", "abstract", "snippet")


def normalize_source_key(src) -> str:
    """v9.4: 来源分组键规范化——非内置来源去尾部数字（paper1→paper、
    Citrus varieties1→Citrus varieties），前端 index.html srcKey/srcMeta
    与恢复接口（/api/v2/.../citations）用同一规则，同名文件夹自动归组。"""
    s = str(src or "").strip()
    if not s:
        return "rag"
    norm = re.sub(r"\d+$", "", s).strip()
    return norm or s


def src_of(r) -> str:
    """证据来源解析：优先 chunk 上的显式字段，退化为 'rag'。"""
    if not isinstance(r, dict):
        return "rag"
    v = r.get("_src") or r.get("source") or ""
    if v:
        return str(v).strip() or "rag"
    # 兼容 UCR 品种库 chunk（无 _src 时按 source_type 判定）
    st = str(r.get("source_type") or "")
    return "ucr" if "UCR" in st else "rag"


def render_evidence(r, *, max_chars: int = EVIDENCE_RENDER_MAX_CHARS) -> str:
    """单一证据渲染：text 优先，摘要/片段次之；单一口径截断 + 透明标记。

    返回空串表示该证据无正文。``max_chars`` 使用具名预算常量，
    调用方不再各自硬编码截断阈值与 marker。
    """
    text = ""
    for k in _TEXT_KEYS:
        t = str((r.get(k) if isinstance(r, dict) else None) or "").strip()
        if t:
            text = t
            break
    if not text:
        return ""
    if len(text) > max_chars:
        return f"{text[:max_chars]} …[超长片段截断: 原文 {len(text)} 字符]"
    return text


def clean_doi(raw) -> str:
    """DOI 字段清洗（v9.4.5，实跑发现语料入库残留脏字符）。

    实测侧栏出现过 `10.1186/s12870-025-07372-2"` / `10.1371/journal.ppat.1010071.g001"`
    （尾部英文引号，入库 CSV/JSON 解析尾巴）→ DOI 超链接打不开；且脏 DOI 与干净
    DOI 字符串不相等会让同篇论文的 chunk 去重键分裂、同一篇以两条出现。

    规则：str 化 → strip → 循环剥除首尾成组/单边的引号/常见包裹符 → 小写留给
    调用方（展示位保留原大小写，键归一另做）。空值返回 ""。
    """
    s = str(raw or "").strip()
    if not s or s.upper() in ("N/A", "NONE", "NULL"):
        return ""
    # 反复剥除首尾的引号/空白/包裹符号（仅剥边缘，不动 DOI 内部字符）
    while s and (s[0] in "\"'`“”‘’([{<）】>" or s[-1] in "\"'`“”‘’)]}>,;"):
        s = s.strip("\"'`“”‘’([{<>）】) ,;")
    return s


__all__ = [
    "EVIDENCE_RENDER_MAX_CHARS", "EVIDENCE_SNIPPET_MAX_CHARS",
    "clean_doi", "normalize_source_key", "render_evidence", "src_of",
]
