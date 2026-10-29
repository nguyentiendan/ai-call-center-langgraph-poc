"""Compile validated YAML into a real LangGraph StateGraph.

Graph shape: START -> preempt -> (state nodes)* -> END.
After each state node, a conditional router reads the node's outcome and
looks up YAML transitions[outcome] to pick the next node. Pause outcomes
(AWAITING_INPUT) and terminal states end the turn; the checkpointer
(InMemorySaver, thread_id=conversation_id) persists state between turns.
Side effects: billing reads are idempotent; handoff uses an idempotency key,
so a resumed/replayed turn cannot create duplicate tickets or billing writes
(billing is read-only).
"""
from dataclasses import dataclass

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ..services.model import is_operator_request
from .loader import WorkflowConfig
from .nodes import make_node
from .state import CallState


@dataclass
class Deps:
    model: object
    billing: object
    faq: object
    handoff: object
    bindings: object

def build_graph(cfg: WorkflowConfig, deps: Deps):
    graph = StateGraph(CallState)

    def preempt(state: dict) -> dict:
        return {}

    graph.add_node("preempt", preempt)
    for name in cfg.states:
        graph.add_node(name, make_node(name, cfg, deps))

    def route_entry(state: dict) -> str:
        if state.get("turn_kind") in ("verify_success", "verify_failed"):
            return "verify_identity"
        msg = state.get("turn_message", "") or ""
        current = state.get("current_state") or cfg.initial_state
        if state.get("turn_kind") == "message" and is_operator_request(msg) and current not in ("handoff", "close"):
            return "handoff"
        return current

    graph.add_edge(START, "preempt")
    graph.add_conditional_edges("preempt", route_entry, {name: name for name in cfg.states})

    def make_router(state_name: str):
        sdef = cfg.states[state_name]

        def router(state: dict) -> str:
            if state.get("status") == "AWAITING_INPUT" or state.get("_outcome") == "__pause__":
                return END
            if sdef.terminal or state.get("_outcome") == "__terminal__":
                return END
            target = sdef.transitions.get(state.get("_outcome", ""))
            if target is None:
                return "handoff"  # fail closed on undefined transition
            return target

        return router

    for name in cfg.states:
        graph.add_conditional_edges(name, make_router(name),
                                    {n: n for n in cfg.states} | {END: END})
    return graph.compile(checkpointer=InMemorySaver())
