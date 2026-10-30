"""Support Agent subgraph: plan -> policy -> execute -> plan, until a terminal decision or a budget."""
from __future__ import annotations

import time

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from ..services.grounding_v2 import collect_sources, denial_text, failure_text, fallback_answer
from ..services.planner import PlannerSchemaError
from ..services.policy import PolicyGateway
from .state import CallState

PIN_GUIDANCE = (
    "確認のため、デモ用PINを専用入力欄（確認ボタン）から入力してください。チャット文にはPINを書かないでください。"
)
SCHEMA_FALLBACK = "ご依頼を処理できませんでした。別の表現でお試しください。"
CLARIFY_TEXT = "ご用件は、料金のご確認、プランのご案内、手続きのご説明のどれに近いでしょうか。一つだけ教えてください。"


def build_agent_subgraph(policy_name: str, cfg, deps, tools):
    policy = cfg.policies[policy_name]
    gateway = PolicyGateway()
    limits = cfg.agent
    graph = StateGraph(CallState)

    def plan(state: dict) -> dict:
        iterations = int(state.get("agent_iterations") or 0) + 1
        tool_count = int(state.get("tool_call_count") or 0)
        query = state.get("agent_query") or ""
        evidence = list(state.get("evidence") or [])
        verified = deps.bindings.get(state.get("conversation_id"))
        if tool_count >= limits.max_tool_calls_per_turn or iterations > limits.max_agent_iterations:
            text = fallback_answer(query, evidence, verified)
            return {
                "agent_iterations": iterations,
                "_agent_route": "finish",
                "_outcome": "success",
                "response_text": text,
                "agent_status": "budget_exhausted",
                "sources": collect_sources(evidence),
                "handoff_reason": "budget_exhausted",
            }
        try:
            decision = deps.model.plan(
                query,
                list(state.get("tool_results") or []),
                list(policy.allowed_tools),
                verified_customer_id=verified,
            )
        except (PlannerSchemaError, ValidationError):
            return {
                "agent_iterations": iterations,
                "_agent_route": "finish",
                "_outcome": "success",
                "response_text": SCHEMA_FALLBACK,
                "agent_status": "schema_rejected",
                "sources": [],
            }
        route = "call_tool" if decision.decision == "call_tool" else "finish"
        return {
            "agent_iterations": iterations,
            "_agent_route": route,
            "planner_decision": decision.model_dump(),
            "verified_customer_id": verified,
        }

    def policy_node(state: dict) -> dict:
        started = time.perf_counter()
        decision = state.get("planner_decision") or {}
        tool_name = decision.get("tool") or ""
        verdict = gateway.authorize(
            tool_name,
            decision.get("arguments") or {},
            policy,
            conversation_id=state.get("conversation_id") or "",
            bindings=deps.bindings,
            query=state.get("agent_query") or "",
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        events = list(state.get("trace_events") or [])
        if verdict.allowed:
            events.append({
                "step": len(events) + 1,
                "tool": tool_name,
                "policy": "allowed",
                "duration_ms": duration_ms,
                "outcome": "pending",
                "source_ids": [],
            })
            return {
                "_agent_route": "exec",
                "authorized_call": {"tool": tool_name, "arguments": verdict.arguments},
                "consecutive_denials": 0,
                "trace_events": events,
            }
        events.append({
            "step": len(events) + 1,
            "tool": tool_name,
            "policy": "denied",
            "duration_ms": duration_ms,
            "outcome": "denied",
            "source_ids": [],
        })
        denials = int(state.get("consecutive_denials") or 0) + 1
        if denials >= limits.max_consecutive_policy_denials:
            return {
                "_agent_route": "finish",
                "_outcome": "success",
                "response_text": denial_text(verdict.reason_code),
                "agent_status": "denied",
                "consecutive_denials": denials,
                "trace_events": events,
                "sources": [],
            }
        return {"_agent_route": "plan", "consecutive_denials": denials, "trace_events": events}

    def execute(state: dict) -> dict:
        call = state.get("authorized_call") or {}
        tool_name = call.get("tool") or ""
        verified = deps.bindings.get(state.get("conversation_id"))
        started = time.perf_counter()
        result = tools.execute(
            tool_name,
            call.get("arguments") or {},
            conversation_id=state.get("conversation_id") or "",
            verified_customer_id=verified,
        )
        duration_ms = int((time.perf_counter() - started) * 1000)
        events = list(state.get("trace_events") or [])
        if events and events[-1].get("outcome") == "pending" and events[-1].get("tool") == tool_name:
            events[-1] = {
                "step": events[-1]["step"],
                "tool": tool_name,
                "policy": "allowed",
                "duration_ms": duration_ms,
                "outcome": "ok" if result["outcome"] == "ok" else result["error"],
                "source_ids": list(result.get("source_ids") or []),
            }
        else:
            events.append({
                "step": len(events) + 1,
                "tool": tool_name,
                "policy": "allowed",
                "duration_ms": duration_ms,
                "outcome": "ok" if result["outcome"] == "ok" else result["error"],
                "source_ids": list(result.get("source_ids") or []),
            })
        tool_results = list(state.get("tool_results") or []) + [result]
        evidence = list(state.get("evidence") or [])
        if result["outcome"] == "ok":
            evidence.append(result)
        return {
            "_agent_route": "plan",
            "tool_results": tool_results,
            "evidence": evidence,
            "tool_call_count": int(state.get("tool_call_count") or 0) + 1,
            "trace_events": events,
            "verified_customer_id": verified,
        }

    def finish(state: dict) -> dict:
        if state.get("response_text") and state.get("_outcome") in {"success", "clarify", "handoff", "failure"}:
            return {"sources": state.get("sources") or collect_sources(list(state.get("evidence") or []))}
        decision = state.get("planner_decision") or {}
        kind = decision.get("decision") or "respond"
        query = state.get("agent_query") or ""
        evidence = list(state.get("evidence") or [])
        verified = deps.bindings.get(state.get("conversation_id"))
        if kind == "clarify":
            text = deps.model.clarify(query) if hasattr(deps.model, "clarify") else CLARIFY_TEXT
            return {
                "_outcome": "clarify",
                "response_text": text,
                "agent_status": "clarifying",
                "sources": [],
            }
        if kind == "handoff":
            return {
                "_outcome": "handoff",
                "response_text": failure_text(list(state.get("tool_results") or [])),
                "agent_status": "handoff",
                "handoff_reason": decision.get("reason_code") or "agent_handoff",
                "sources": collect_sources(evidence),
            }
        text = deps.model.compose(query, evidence, verified_customer_id=verified)
        return {
            "_outcome": "success",
            "response_text": text,
            "agent_status": "answered",
            "sources": collect_sources(evidence),
        }

    graph.add_node("plan", plan)
    graph.add_node("policy", policy_node)
    graph.add_node("execute", execute)
    graph.add_node("finish", finish)
    graph.add_edge(START, "plan")

    def route_plan(state: dict) -> str:
        return "policy" if state.get("_agent_route") == "call_tool" else "finish"

    def route_policy(state: dict) -> str:
        route = state.get("_agent_route")
        if route == "exec":
            return "execute"
        if route == "plan":
            return "plan"
        return "finish"

    def route_execute(state: dict) -> str:
        return "finish" if state.get("_agent_route") == "finish" else "plan"

    graph.add_conditional_edges("plan", route_plan, {"policy": "policy", "finish": "finish"})
    graph.add_conditional_edges("policy", route_policy, {"execute": "execute", "plan": "plan", "finish": "finish"})
    graph.add_conditional_edges("execute", route_execute, {"plan": "plan", "finish": "finish"})
    graph.add_edge("finish", END)
    return graph.compile()
