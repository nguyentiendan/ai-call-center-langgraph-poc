"""Phase 2 controller. YAML transitions choose the next node; the agent subgraph only picks tools."""
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from langgraph.graph import END, START, StateGraph

from ..services.grounding_v2 import amounts_grounded, collect_sources, fallback_answer
from ..services.model import is_operator_request
from ..services.planner import task_labels
from .agent_graph import PIN_GUIDANCE, build_agent_subgraph
from .nodes import make_node
from .state import CallState


class _Action:
    def __init__(self, action: str):
        self.action = action
        self.terminal = False
        self.prompt_ref = None
        self.transitions = {}


class _Shim:
    """Adapts a Phase 2 config so Phase 1 identity nodes can run unchanged."""

    def __init__(self, cfg):
        self.initial_state = cfg.initial_state
        self.settings = cfg.settings
        actions = {
            "collect_field": "collect_field",
            "collect_secret": "collect_secret",
            "identity_verification": "verify_demo_customer",
        }
        self.states = {
            name: _Action(actions[state.kind])
            for name, state in cfg.states.items()
            if state.kind in actions
        }


def _trace(state, node, outcome):
    return list(state.get("trace") or []) + [{"state": node, "outcome": outcome}]


def build_graph_v2(cfg, deps, tools):
    shim = _Shim(cfg)
    subgraphs = {
        name: build_agent_subgraph(state.policy, cfg, deps, tools)
        for name, state in cfg.states.items()
        if state.kind == "agent_subgraph"
    }
    graph = StateGraph(CallState)

    def preempt(state: dict) -> dict:
        if state.get("pin_rejected"):
            return {"turn_message": "", "pin_rejected": False, "pin_notice": True}
        return {"pin_notice": False}

    graph.add_node("preempt", preempt)

    def classify(state: dict) -> dict:
        if state.get("pin_notice") and not (state.get("turn_message") or "").strip():
            return {
                "current_state": "classify",
                "status": "AWAITING_INPUT",
                "required_input": None,
                "response_text": PIN_GUIDANCE,
                "pin_notice": False,
                "_outcome": "__pause__",
                "trace": _trace(state, "classify", "pin_in_chat"),
            }
        message = (state.get("turn_message") or "")[:2000]
        scope = deps.model.classify_scope(message)
        label = scope.scope if scope.confidence >= cfg.settings.confidence_threshold else "unclear"
        verified = deps.bindings.get(state.get("conversation_id"))
        outcome = f"{label}_verified" if label in {"protected", "mixed"} and verified else label
        labels = task_labels(message) if label in {"public", "protected", "mixed"} else list(state.get("pending_tasks") or [])
        messages = list(state.get("messages") or [])
        if message:
            messages.append({"role": "user", "content": message})
        return {
            "current_state": "classify",
            "status": "ACTIVE",
            "intent": label,
            "intent_confidence": scope.confidence,
            "agent_query": message or state.get("agent_query") or "",
            "pending_tasks": labels,
            "pending_intents": labels,
            "verified_customer_id": verified,
            "messages": messages,
            "required_input": None,
            "_outcome": outcome,
            "trace": _trace(state, "classify", outcome),
        }

    def clarify(state: dict) -> dict:
        message = (state.get("turn_message") or "")[:2000]
        entry = state.get("_turn_entry_state")
        base = {"current_state": "clarify", "_last_node": "clarify"}
        if entry == "clarify" and message:
            scope = deps.model.classify_scope(message)
            if scope.scope != "unclear" and scope.confidence >= cfg.settings.confidence_threshold:
                return {**base, "status": "ACTIVE", "_outcome": "clarified", "trace": _trace(state, "clarify", "clarified")}
            attempts = int(state.get("clarification_attempts") or 0) + 1
            if attempts >= cfg.settings.max_clarifications:
                return {
                    **base, "status": "ACTIVE", "clarification_attempts": attempts, "_outcome": "exhausted",
                    "handoff_reason": "unknown_intent", "trace": _trace(state, "clarify", "exhausted"),
                }
            return {
                **base, "status": "ACTIVE", "clarification_attempts": attempts, "_outcome": "clarified",
                "trace": _trace(state, "clarify", "clarified"),
            }
        question = deps.model.clarify(state.get("agent_query") or message)
        return {
            **base, "status": "AWAITING_INPUT", "required_input": None, "response_text": question,
            "_outcome": "__pause__", "trace": _trace(state, "clarify", "awaiting_input"),
        }

    def run_agent(state_name: str):
        subgraph = subgraphs[state_name]

        def node(state: dict) -> dict:
            payload = {
                "conversation_id": state.get("conversation_id"),
                "agent_query": state.get("agent_query") or state.get("turn_message") or "",
                "tool_results": [],
                "evidence": [],
                "tool_call_count": 0,
                "agent_iterations": 0,
                "consecutive_denials": 0,
                "trace_events": list(state.get("trace_events") or []),
            }
            try:
                final = subgraph.invoke(payload, {"recursion_limit": 50})
            except GraphRecursionError:
                final = {
                    "_outcome": "handoff",
                    "response_text": "処理の上限に達したため、担当者への引き継ぎが必要です。",
                    "agent_status": "budget_exhausted",
                    "handoff_reason": "budget_exhausted",
                    "trace_events": payload["trace_events"],
                    "sources": [],
                }
            outcome = final.get("_outcome") or "failure"
            return {
                "current_state": state_name,
                "response_text": final.get("response_text") or "",
                "sources": final.get("sources") or [],
                "evidence": final.get("evidence") or [],
                "tool_results": final.get("tool_results") or [],
                "trace_events": final.get("trace_events") or [],
                "agent_status": final.get("agent_status"),
                "handoff_reason": final.get("handoff_reason"),
                "tool_call_count": final.get("tool_call_count") or 0,
                "verified_customer_id": deps.bindings.get(state.get("conversation_id")),
                "_outcome": outcome,
                "trace": _trace(state, state_name, outcome),
            }

        return node

    def validate(state: dict) -> dict:
        evidence = list(state.get("evidence") or [])
        text = state.get("response_text") or ""
        query = state.get("agent_query") or ""
        verified = deps.bindings.get(state.get("conversation_id"))
        replaced = False
        if not amounts_grounded(text, evidence):
            text = deps.model.compose(query, evidence, verified_customer_id=verified)
            replaced = True
        if not amounts_grounded(text, evidence):
            text = fallback_answer(query, evidence, verified)
            replaced = True
        if not amounts_grounded(text, evidence):
            return {
                "current_state": "validate_response",
                "status": "ACTIVE",
                "_outcome": "failure",
                "handoff_reason": "ungrounded_response",
                "guardrail_replaced": True,
                "trace": _trace(state, "validate_response", "failure"),
            }
        return {
            "current_state": "validate_response",
            "status": "ACTIVE",
            "response_text": text,
            "sources": collect_sources(evidence),
            "guardrail_replaced": replaced,
            "_outcome": "success",
            "trace": _trace(state, "validate_response", "success"),
        }

    def respond(state: dict) -> dict:
        return {
            "current_state": "respond",
            "status": "ACTIVE",
            "required_input": None,
            "response_text": state.get("response_text") or "",
            "sources": state.get("sources") or [],
            "pending_tasks": [],
            "_outcome": "__pause__",
            "trace": _trace(state, "respond", "completed"),
        }

    def handoff(state: dict) -> dict:
        reason = state.get("handoff_reason") or "operator_requested"
        ticket = deps.handoff.create(
            state["conversation_id"],
            reason,
            f"intent={state.get('intent')} reason={reason}",
            idempotency_key=f"handoff:{state['conversation_id']}",
        )
        prefix = (state.get("response_text") or "").strip()
        notice = (
            f"担当者への引き継ぎを受け付けました。デモ用チケット {ticket['ticket_id']} は queued_demo です。"
            "実際のオペレーターには接続されていません。"
        )
        text = f"{prefix}\n{notice}" if prefix else notice
        return {
            "current_state": "human_handoff",
            "status": "HANDOFF",
            "required_input": None,
            "handoff_ticket_id": ticket["ticket_id"],
            "handoff_reason": reason,
            "response_text": text,
            "_outcome": "__terminal__",
            "trace": _trace(state, "human_handoff", "created"),
        }

    graph.add_node("classify", classify)
    graph.add_node("clarify", clarify)
    graph.add_node("validate_response", validate)
    graph.add_node("respond", respond)
    graph.add_node("human_handoff", handoff)
    for name, state_def in cfg.states.items():
        if state_def.kind in {"collect_field", "collect_secret", "identity_verification"}:
            graph.add_node(name, make_node(name, shim, deps))
        elif state_def.kind == "agent_subgraph":
            graph.add_node(name, run_agent(name))

    def route_entry(state: dict) -> str:
        if state.get("turn_kind") in ("verify_success", "verify_failed"):
            return "verify_identity"
        message = state.get("turn_message") or ""
        current = state.get("current_state") or cfg.initial_state
        if current == "respond":
            current = cfg.initial_state
        if state.get("turn_kind") == "message" and is_operator_request(message) and current != "human_handoff":
            return "human_handoff"
        if state.get("pin_notice") and current == cfg.initial_state:
            return cfg.initial_state
        return current

    graph.add_edge(START, "preempt")
    graph.add_conditional_edges("preempt", route_entry, {name: name for name in cfg.states})

    def make_router(state_name: str):
        state_def = cfg.states[state_name]

        def router(state: dict) -> str:
            if state.get("status") == "AWAITING_INPUT" or state.get("_outcome") == "__pause__":
                return END
            if state_def.kind == "handoff" or state.get("_outcome") == "__terminal__":
                return END
            target = state_def.transitions.get(state.get("_outcome", ""))
            if target is None:
                return "human_handoff"
            return target

        return router

    for name in cfg.states:
        graph.add_conditional_edges(name, make_router(name), {n: n for n in cfg.states} | {END: END})
    compiled = graph.compile(checkpointer=InMemorySaver())
    compiled.agent_subgraphs = subgraphs
    return compiled
