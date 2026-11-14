"""LangGraph assembly and compatibility imports for the research agent."""
from langgraph.graph import END, StateGraph
from src.graph.state import AgentState
from src.graph.agent_runtime import general_agent_node
from src.graph.agent_operations import (
    _toolset, _read_uploaded_paper_without_search, _final_reserve_sec,
    _task_working_state, _capture_interaction_refs,
)
from src.graph.model_driver import request_deadline_from_settings, _model_timeout


def __getattr__(name):
    # Historical private imports remain readable during the one-way migration.
    # There is one implementation of each helper, and one execution path.
    from src.graph import agent_operations
    if name.startswith('_') and hasattr(agent_operations, name):
        return getattr(agent_operations, name)
    raise AttributeError(name)


def build_general_agent_graph():
    from src.graph.session_nodes import load_session_context, save_session_context
    from src.graph.candidate_prefetch import prefetch_candidates

    graph = StateGraph(AgentState)
    graph.add_node("session_load", load_session_context)
    # Request-scoped local prefetch: one synchronous corpus search whose
    # candidate text never enters the model context. The supervisor exposes the
    # pool through the bounded candidate tools.
    graph.add_node("candidate_prefetch", prefetch_candidates)
    graph.add_node("supervisor", general_agent_node)
    graph.add_node("session_save", save_session_context)
    graph.set_entry_point("session_load")
    graph.add_edge("session_load", "candidate_prefetch")
    graph.add_edge("candidate_prefetch", "supervisor")
    graph.add_edge("supervisor", "session_save")
    graph.add_edge("session_save", END)
    return graph.compile()
