"""One general interaction tool; schema and identities are host validated."""
from __future__ import annotations

import contextvars
import json

from langchain_core.tools import tool
from src.core.user_questions import UserQuestionInput, create_question

_question_context = contextvars.ContextVar("user_question_context", default=None)


def bind_question_context(context):
    return _question_context.set(context)


def reset_question_context(token):
    _question_context.reset(token)


@tool(args_schema=UserQuestionInput, response_format="content_and_artifact")
def request_user_input(prompt: str, options: list, multi_select: bool, allow_text: bool):
    """Ask one necessary clarification and end this execution segment.

    Use native structured tool calls only, in a separate batch from all other
    tools. options have id, label, description, refs; purpose/preferences may
    have empty refs. Field options MUST cite real schema identities. Never infer
    scientific field roles. Set allow_text for a custom answer; user may cancel.
    IDs, owner, session and version are generated/validated by the host.
    """
    from src.core.tracing import get_job_id, get_user_id, get_session_id
    question = create_question(get_job_id(), owner_user_id=get_user_id(),
        session_id=get_session_id(), prompt=prompt, options=options,
        multi_select=multi_select, allow_text=allow_text,
        context=_question_context.get() or {})
    return json.dumps({"question": question}, ensure_ascii=False), {
        "user_question": question,
        "_tool_outcome": {"status": "complete", "reason_code": "awaiting_input",
                          "summary": "等待用户答复", "artifacts": []}}


def provider_tools(tools):
    """Keep existing executor objects; bind only interaction with strict schema."""
    from langchain_core.utils.function_calling import convert_to_openai_tool
    return [convert_to_openai_tool(t, strict=True) if t.name == "request_user_input" else t
            for t in tools]
