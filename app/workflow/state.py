from typing import TypedDict


class CallState(TypedDict, total=False):
    conversation_id: str
    messages: list
    current_state: str
    intent: str
    intent_confidence: float
    pending_customer_id: str | None
    verified_customer_id: str | None
    verification_attempts: int
    clarification_attempts: int
    billing_facts: dict | None
    response_text: str
    handoff_reason: str | None
    handoff_ticket_id: str | None
    status: str  # ACTIVE | AWAITING_INPUT | CLOSED | HANDOFF
    error_code: str | None
    required_input: str | None
    trace: list
    turn_message: str
    turn_kind: str  # message | verify_success | verify_failed
    guardrail_replaced: bool
    _turn_entry_state: str
    _last_node: str
    _outcome: str
    agent_query: str
    pending_tasks: list
    pending_intents: list
    agent_iterations: int
    tool_call_count: int
    consecutive_denials: int
    tool_results: list
    evidence: list
    sources: list
    agent_status: str
    trace_events: list
    errors: list
    pin_rejected: bool
    pin_notice: bool
    planner_decision: dict
    authorized_call: dict
    _agent_route: str
    workflow_version: int
