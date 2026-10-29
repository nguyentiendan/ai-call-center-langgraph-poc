"""Node functions. Each node is bound to one YAML state; routing comes from YAML transitions."""
import re

from ..services.billing import BillingAccessDenied, BillingNotFound, BillingUnavailable
from ..services.guardrails import grounded_template, validate_grounded
from ..services.model import is_operator_request
from .loader import WorkflowConfig

CUSTOMER_ID_RE = re.compile(r"^CUST-\d{4}$")
MAX_INPUT_LEN = 2000

def _trace(state, node, outcome):
    return list(state.get("trace", [])) + [{"state": node, "outcome": outcome}]

def make_node(state_name: str, cfg: WorkflowConfig, deps):
    sdef = cfg.states[state_name]
    action = sdef.action

    def node(state: dict) -> dict:
        msg = state.get("turn_message", "") or ""
        entry = state.get("_turn_entry_state", cfg.initial_state)
        is_entry = entry == state_name
        base = {"current_state": state_name, "_last_node": state_name}
        threshold = cfg.settings.confidence_threshold

        if action == "classify_intent":
            result = deps.model.classify(msg)
            intent = result.intent if result.confidence >= threshold else "unknown"
            msgs = list(state.get("messages", [])) + ([{"role": "user", "content": msg[:MAX_INPUT_LEN]}] if msg else [])
            return {**base, "intent": intent, "intent_confidence": result.confidence,
                    "messages": msgs, "status": "ACTIVE", "required_input": None, "_outcome": intent, "trace": _trace(state, state_name, intent)}

        if action == "ask_clarification":
            if is_entry and msg:
                result = deps.model.classify(msg)
                if result.intent != "unknown" and result.confidence >= threshold:
                    return {**base, "status": "ACTIVE", "_outcome": "clarified", "trace": _trace(state, state_name, "clarified")}
                attempts = state.get("clarification_attempts", 0) + 1
                outcome = "exhausted" if attempts >= cfg.settings.max_clarifications else "clarified"
                return {**base, "status": "ACTIVE", "clarification_attempts": attempts, "_outcome": outcome,
                        "handoff_reason": "unknown_intent" if outcome == "exhausted" else None,
                        "trace": _trace(state, state_name, outcome)}
            return {**base, "status": "AWAITING_INPUT", "required_input": None,
                    "response_text": "ご要望をもう少し詳しく教えてください。料金、営業時間、オペレーター希望のいずれかでしょうか。",
                    "_outcome": "__pause__", "trace": _trace(state, state_name, "awaiting_input")}

        if action == "collect_field":
            if is_entry and msg:
                if is_operator_request(msg):
                    return {**base, "status": "ACTIVE", "_outcome": "operator_requested", "handoff_reason": "operator_requested",
                            "trace": _trace(state, state_name, "operator_requested")}
                candidate = msg.strip()
                if not CUSTOMER_ID_RE.match(candidate):
                    return {**base, "status": "AWAITING_INPUT", "required_input": "customer_id",
                            "response_text": "お客様IDの形式を確認してください（例: CUST-1001）。",
                            "_outcome": "__pause__", "trace": _trace(state, state_name, "invalid_format")}
                return {**base, "status": "ACTIVE", "required_input": None, "pending_customer_id": candidate, "_outcome": "supplied",
                        "trace": _trace(state, state_name, "supplied")}
            return {**base, "status": "AWAITING_INPUT", "required_input": "customer_id",
                    "response_text": "確認いたします。デモ用のお客様IDを入力してください。",
                    "_outcome": "__pause__", "trace": _trace(state, state_name, "awaiting_input")}

        if action == "collect_secret":
            # PIN is never accepted here; only POST /verify may submit it.
            return {**base, "status": "AWAITING_INPUT", "required_input": "demo_pin",
                    "response_text": "確認のため、デモ用PINを専用入力欄（確認ボタン）から入力してください。チャット文にはPINを書かないでください。",
                    "_outcome": "__pause__", "trace": _trace(state, state_name, "awaiting_input")}

        if action == "verify_demo_customer":
            kind = state.get("turn_kind")
            if kind == "verify_success":
                verified = deps.bindings.get(state["conversation_id"])
                return {**base, "status": "ACTIVE", "required_input": None, "verified_customer_id": verified, "_outcome": "verified",
                        "trace": _trace(state, state_name, "verified")}
            attempts = state.get("verification_attempts", 0) + 1
            limit = min(cfg.settings.max_verification_attempts, 3)
            if attempts >= limit:
                return {**base, "status": "ACTIVE", "verification_attempts": attempts, "_outcome": "locked",
                        "handoff_reason": "verification_failed", "pending_customer_id": None,
                        "trace": _trace(state, state_name, "locked")}
            return {**base, "status": "ACTIVE", "verification_attempts": attempts, "_outcome": "retry", "pending_customer_id": None,
                    "response_text": "確認できませんでした。もう一度お客様IDから入力してください。",
                    "trace": _trace(state, state_name, "retry")}

        if action == "get_billing":
            try:
                facts = deps.billing.get_billing(state["conversation_id"])
            except BillingNotFound:
                return {**base, "status": "ACTIVE", "_outcome": "not_found", "handoff_reason": "missing_record", "error_code": "BILLING_NOT_FOUND",
                        "trace": _trace(state, state_name, "not_found")}
            except (BillingAccessDenied, BillingUnavailable) as e:
                code = "BILLING_ACCESS_DENIED" if isinstance(e, BillingAccessDenied) else "BILLING_UNAVAILABLE"
                return {**base, "status": "ACTIVE", "_outcome": "error", "handoff_reason": "api_error", "error_code": code,
                        "trace": _trace(state, state_name, "error")}
            return {**base, "status": "ACTIVE", "billing_facts": facts, "_outcome": "found", "trace": _trace(state, state_name, "found")}

        if action == "generate_grounded_response":
            facts = state.get("billing_facts")
            if not facts:
                return {**base, "status": "ACTIVE", "_outcome": "operator_requested", "handoff_reason": "missing_facts",
                        "trace": _trace(state, state_name, "missing_facts")}
            text = deps.model.explain_billing(facts, msg)
            replaced = False
            if not validate_grounded(text, facts):
                text, replaced = grounded_template(facts), True
            return {**base, "status": "ACTIVE", "response_text": text, "guardrail_replaced": replaced,
                    "_outcome": "completed", "trace": _trace(state, state_name, "completed")}

        if action == "answer_approved_faq":
            ans = deps.faq.answer(msg)
            if ans is None:
                return {**base, "status": "ACTIVE", "_outcome": "unsupported", "handoff_reason": "unsupported_faq",
                        "trace": _trace(state, state_name, "unsupported")}
            return {**base, "status": "ACTIVE", "response_text": ans, "_outcome": "completed", "trace": _trace(state, state_name, "completed")}

        if action == "create_handoff":
            reason = state.get("handoff_reason") or "operator_requested"
            ticket = deps.handoff.create(state["conversation_id"], reason,
                                         f"intent={state.get('intent')} state={state_name} reason={reason}",
                                         idempotency_key=f"handoff:{state['conversation_id']}")
            return {**base, "status": "HANDOFF", "required_input": None, "handoff_ticket_id": ticket["ticket_id"],
                    "handoff_reason": reason,
                    "response_text": f"担当者への引き継ぎを受け付けました。デモ用チケット {ticket['ticket_id']} は queued_demo です。実際のオペレーターには接続されていません。",
                    "_outcome": "__terminal__", "trace": _trace(state, state_name, "created")}

        if action == "close_conversation":
            text = state.get("response_text", "")
            suffix = " 他にご不明点があれば、新しいお問い合わせを開始してください。"
            return {**base, "status": "CLOSED", "required_input": None,
                    "response_text": text if suffix.strip() in text else text + suffix,
                    "_outcome": "__terminal__", "trace": _trace(state, state_name, "closed")}

        raise ValueError(f"unhandled action {action}")

    return node
