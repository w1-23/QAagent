"""Connect the general loop to existing provider transport and attempt metering."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from langchain_core.messages import AIMessage, BaseMessage

from .capabilities import CapabilityCatalog
from .identity import ExecutionIdentity


@dataclass
class ModelDriver:
    model: object
    catalog: CapabilityCatalog
    allowed: Callable[[ExecutionIdentity], set[str]]
    on_text: Callable[[str], None] | None = None

    async def __call__(self, messages: tuple[BaseMessage, ...],
                       identity: ExecutionIdentity) -> AIMessage:
        # Resolving implementations is the executor's job. Tool discovery cannot
        # import an unavailable scientific plugin before the model can act.
        schemas = self.catalog.schemas(self.allowed(identity))
        from langchain_core.utils.function_calling import convert_to_openai_function
        # convert_to_openai_tool returns already-wrapped function tools unchanged,
        # ignoring strict. Apply the conversion to the function definition itself.
        schemas = [{"type": "function", "function": convert_to_openai_function(schema["function"], strict=True)}
                   if self.catalog.execution_mode(schema["function"]["name"]) == "interaction"
                   else schema for schema in schemas]
        bound = self.model.bind_tools(schemas) if schemas else self.model
        from src.core.stream_llm import stream_llm_response
        from src.core.task_events import run_model_attempt
        response = await run_model_attempt(
            bound, list(messages),
            lambda: stream_llm_response(bound, list(messages), self.on_text),
            purpose="general_agent", usage_session_id=identity.session_id)
        return response
