# -*- coding: utf-8 -*-
"""Probe: does the provider stream public text *alongside* tool calls?

Why: the audit needs to know whether a Codex-style "分析" line can be produced
without extra model requests. `_call_llm` buffers the stream
(`src/graph/general_agent_graph.py:932-939`) and never passes `on_text`, so
anything the provider does emit is invisible in the persisted transcript. This
probe streams the same model + same bound tools and counts the chunk kinds, so
the conclusion rests on measurement instead of inference.

Read-only with respect to production code: nothing is imported for its side
effects beyond building a client, no database or session is touched.

Usage:
    python experiments/trace/probes/probe_public_text.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402

from src.config import settings  # noqa: E402
from src.core.llm_pool import get_llm  # noqa: E402
from src.graph.general_agent_graph import _toolset, get_deepseek_model  # noqa: E402

QUERY = "柑橘木虱的防治措施有哪些？先用检索工具查证据再回答。"


async def run() -> dict:
    tools, _schemas = _toolset()
    llm = get_llm(
        model=get_deepseek_model(),
        api_key=settings.RESOLVED_MAIN_API_KEY,
        base_url=settings.RESOLVED_MAIN_BASE_URL,
        temperature=settings.TEMPERATURE_MAIN,
        max_tokens=4096,
        timeout=120,
    ).bind_tools(tools)

    messages = [
        SystemMessage(content="你是柑橘科研助手。需要证据时先调用检索工具。"),
        HumanMessage(content=QUERY),
    ]

    stats = {
        "chunks": 0,
        "content_chunks": 0,
        "content_chars": 0,
        "text_before_tool_chunks": 0,
        "tool_call_chunks": 0,
        "reasoning_chunks": 0,
        "content_samples": [],
        "aggregated_content_chars": 0,
        "aggregated_tool_calls": [],
        "finish_reason": None,
    }
    full = None
    async for chunk in llm.astream(messages):
        stats["chunks"] += 1
        full = chunk if full is None else full + chunk
        text = chunk.content if isinstance(chunk.content, str) else ""
        if text:
            stats["content_chunks"] += 1
            stats["content_chars"] += len(text)
            if stats["tool_call_chunks"] == 0 and len(stats["content_samples"]) < 5:
                stats["content_samples"].append(text[:200])
        if getattr(chunk, "tool_call_chunks", None):
            stats["tool_call_chunks"] += len(chunk.tool_call_chunks)
        extra = getattr(chunk, "additional_kwargs", None) or {}
        if extra.get("reasoning_content"):
            stats["reasoning_chunks"] += 1
        meta = getattr(chunk, "response_metadata", None) or {}
        if meta.get("finish_reason"):
            stats["finish_reason"] = meta["finish_reason"]

    if full is not None:
        content = full.content if isinstance(full.content, str) else str(full.content)
        stats["aggregated_content_chars"] = len(content or "")
        stats["aggregated_tool_calls"] = [
            {"name": tc.get("name"), "args": tc.get("args")}
            for tc in (getattr(full, "tool_calls", None) or [])
        ]
        stats["text_before_tool_chunks"] = stats["content_chunks"] if stats["tool_call_chunks"] else 0
    return stats


def main() -> int:
    stats = asyncio.run(run())
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    out = pathlib.Path(__file__).resolve().parent / "probe_public_text.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
