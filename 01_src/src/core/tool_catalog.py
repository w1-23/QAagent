"""One declarative catalog for tool capability groups and public labels.

The executable tool objects and their ``requires/produces/authorization`` live in
``src.tools._TOOL_DECLARATIONS``. This module adds the other facts that used to
be copied into four separate name lists: user-facing labels and derived source
capabilities. Importing it never imports the tool implementations, so the low
level progress bus can use it without creating an import cycle.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolMetadata:
    display_label: str
    link_label: str = ""
    capabilities: frozenset[str] = frozenset()


# Capabilities are environment facts, never topic keywords.
NETWORK = "network"
LOCAL_CORPUS = "local_corpus"
WEB_RETRIEVAL = "web_retrieval"
LOCAL_RETRIEVAL = "local_retrieval"
LITERATURE_RETRIEVAL = "literature_retrieval"
ROUTE_WEB = "route_web"
ROUTE_LOCAL = "route_local"
RESEARCH = "research"
ARTIFACT = "artifact"
VISUALIZATION = "visualization"

TOOL_METADATA: dict[str, ToolMetadata] = {
    "deepseek_search": ToolMetadata(
        "网页搜索", capabilities=frozenset({NETWORK, WEB_RETRIEVAL, ROUTE_WEB,
                                           LITERATURE_RETRIEVAL})),
    "deepseek_fetch": ToolMetadata(
        "读取网页", capabilities=frozenset({NETWORK, WEB_RETRIEVAL, ROUTE_WEB,
                                           LITERATURE_RETRIEVAL})),
    "deepseek_fetch_paper": ToolMetadata(
        "获取论文全文", capabilities=frozenset({NETWORK, WEB_RETRIEVAL, ROUTE_WEB,
                                               LITERATURE_RETRIEVAL})),
    "local_hybrid_search": ToolMetadata(
        "本地检索", capabilities=frozenset({LOCAL_CORPUS, LOCAL_RETRIEVAL,
                                           ROUTE_LOCAL, LITERATURE_RETRIEVAL})),
    # Candidate tools expose the request-scoped candidate pool. They declare
    # LOCAL_CORPUS only: the "local search was done" completion gate must keep
    # being satisfied by local_hybrid_search, never by a candidate tool.
    "inspect_candidates": ToolMetadata(
        "查看候选", capabilities=frozenset({LOCAL_CORPUS})),
    "read_candidate": ToolMetadata(
        "读取候选原文", capabilities=frozenset({LOCAL_CORPUS})),
    "search_candidates": ToolMetadata(
        "补充候选检索", capabilities=frozenset({LOCAL_CORPUS})),
    "draw_svg": ToolMetadata(
        "绘图", "下载 SVG", frozenset({VISUALIZATION, ARTIFACT})),
    "read_account_file": ToolMetadata(
        "读取账户文件", capabilities=frozenset()),
    "inspect_uploaded_table": ToolMetadata(
        "读取表格", capabilities=frozenset({RESEARCH})),
    "audit_uploaded_data": ToolMetadata(
        "数据审查", "下载数据审查 Excel", frozenset({RESEARCH, ARTIFACT})),
    "search_literature": ToolMetadata(
        "文献检索", capabilities=frozenset({NETWORK, LITERATURE_RETRIEVAL})),
    "verify_uploaded_references": ToolMetadata(
        "文献核验", "下载 DOI 核验表", frozenset({RESEARCH, ARTIFACT})),
    "fetch_and_audit_open_supplements": ToolMetadata(
        "获取补充数据", "下载补充材料",
        frozenset({NETWORK, RESEARCH, ARTIFACT, LITERATURE_RETRIEVAL})),
    "word_document": ToolMetadata(
        "处理 Word 文档", "下载 Word 文档", frozenset({RESEARCH, ARTIFACT})),
    "export_table": ToolMetadata(
        "导出表格", "下载表格", frozenset({RESEARCH, ARTIFACT})),
    "export_literature_evidence": ToolMetadata(
        "导出文献证据", "下载文献证据表", frozenset({RESEARCH, ARTIFACT})),
    "export_paper_analysis": ToolMetadata(
        "导出论文解读", "下载论文解读", frozenset({RESEARCH, ARTIFACT})),
    "export_experiment_design": ToolMetadata(
        "导出实验方案", "下载实验设计", frozenset({RESEARCH, ARTIFACT})),
    "save_research_note": ToolMetadata(
        "保存课题记录", "下载课题记录", frozenset({RESEARCH, ARTIFACT})),
    "recall_research_notes": ToolMetadata(
        "查阅课题记录", capabilities=frozenset({RESEARCH})),
    "export_research_notes": ToolMetadata(
        "导出课题记录", "下载课题记录", frozenset({RESEARCH, ARTIFACT})),
    "export_corpus_gap_report": ToolMetadata(
        "导出语料清单", "下载语料缺口报告", frozenset({RESEARCH, ARTIFACT})),
    "analyze_research_data": ToolMetadata(
        "统计分析", "下载统计结果", frozenset({RESEARCH, ARTIFACT})),
    "create_research_figure": ToolMetadata(
        "生成科研图", "下载图件", frozenset({RESEARCH, ARTIFACT, VISUALIZATION})),
    "run_research_figure_script": ToolMetadata(
        "执行绘图脚本", "下载图件", frozenset({RESEARCH, ARTIFACT, VISUALIZATION})),
}


def capability_groups() -> dict[str, frozenset[str]]:
    """Derive all source/route groups from one catalog."""
    groups: dict[str, set[str]] = {
        key: set() for key in (
            "network", "local_corpus", "web_retrieval", "local_retrieval",
            "literature_retrieval", "route_web", "route_local", "research",
            "artifact", "visualization",
        )
    }
    for name, metadata in TOOL_METADATA.items():
        for capability in metadata.capabilities:
            groups.setdefault(capability, set()).add(name)
    groups["web"] = groups["network"]
    groups["local"] = groups["local_corpus"]
    groups["retrieval"] = (groups["web_retrieval"] | groups["local_retrieval"]
                           | groups["literature_retrieval"])
    groups["web_route"] = groups["route_web"]
    groups["local_route"] = groups["route_local"]
    return {key: frozenset(value) for key, value in groups.items()}


def display_labels() -> dict[str, str]:
    return {name: metadata.display_label for name, metadata in TOOL_METADATA.items()}


def artifact_link_labels() -> dict[str, str]:
    return {name: metadata.link_label for name, metadata in TOOL_METADATA.items()
            if metadata.link_label}


def capabilities(name: str) -> frozenset[str]:
    return TOOL_METADATA.get(str(name or ""), ToolMetadata("")).capabilities


__all__ = [
    "ARTIFACT", "LITERATURE_RETRIEVAL", "LOCAL_CORPUS", "LOCAL_RETRIEVAL",
    "NETWORK", "RESEARCH", "ROUTE_LOCAL", "ROUTE_WEB", "TOOL_METADATA",
    "ToolMetadata", "VISUALIZATION", "WEB_RETRIEVAL", "artifact_link_labels",
    "capabilities", "capability_groups", "display_labels",
]
