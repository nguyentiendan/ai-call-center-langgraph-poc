"""API-boundary conversation service. Owns meta store; PIN never enters the graph."""
import hashlib
import hmac
import re
import secrets
import uuid

from ..workflow.compiler import Deps, build_graph
from ..workflow.loader import WorkflowConfig
from .billing import BillingService
from .faq import FaqService
from .handoff import HandoffService
from .verification import VerificationService


class ConversationNotFound(LookupError):
    pass

class ConversationClosed(RuntimeError):
    pass

class InvalidState(RuntimeError):
    pass

class CustomerMismatch(ValueError):
    """The /verify customer_id differs from the ID collected in this conversation."""

class ConversationService:
    def __init__(self, cfg: WorkflowConfig, model, billing: BillingService, faq: FaqService,
                 handoff: HandoffService, verification: VerificationService, bindings, tools=None,
                 dev_trace: bool = False):
        self.cfg = cfg
        self.billing, self.verification, self.bindings = billing, verification, bindings
        self.handoff = handoff
        self.tools = tools
        self.dev_trace = dev_trace
        deps = Deps(model=model, billing=billing, faq=faq, handoff=handoff, bindings=bindings)
        if getattr(cfg, "schema_version", 1) == 2:
            from ..workflow.compiler_v2 import build_graph_v2

            self.graph = build_graph_v2(cfg, deps, tools)
        else:
            self.graph = build_graph(cfg, deps)
        self.meta: dict[str, dict] = {}

    def create(self) -> dict:
        cid = str(uuid.uuid4())
        # Demo capability token: returned once at creation, only its hash is stored.
        # This is bearer-token demo auth, not production identity (see implementation_report).
        token = secrets.token_urlsafe(32)
        self.meta[cid] = {"conversation_id": cid, "status": "ACTIVE", "workflow_state": self.cfg.initial_state,
                          "required_input": None, "handoff_ticket_id": None, "intent": None,
                          "_token_hash": hashlib.sha256(token.encode()).hexdigest()}
        return {"conversation_id": cid, "status": "ACTIVE", "conversation_token": token,
                "message": "こんにちは。Sakura Mobile サポートです。お問い合わせ内容を教えてください。"}

    def authorize(self, cid: str, token: str | None) -> bool:
        m = self.meta.get(cid)
        if m is None or not token:
            return False
        return hmac.compare_digest(hashlib.sha256(token.encode()).hexdigest(), m.get("_token_hash", ""))

    def _require(self, cid: str) -> dict:
        if cid not in self.meta:
            raise ConversationNotFound(cid)
        return self.meta[cid]

    def _sync(self, cid: str) -> dict:
        snap = self.graph.get_state({"configurable": {"thread_id": cid}})
        v = snap.values if snap else {}
        m = self.meta[cid]
        if v:
            m.update({"status": v.get("status", m["status"]), "workflow_state": v.get("current_state", m["workflow_state"]),
                      "required_input": v.get("required_input"), "handoff_ticket_id": v.get("handoff_ticket_id"),
                      "intent": v.get("intent"), "intent_confidence": v.get("intent_confidence"),
                      "pending_customer_id": v.get("pending_customer_id"),
                      "trace": v.get("trace", []), "response": v.get("response_text"),
                      "sources": v.get("sources") or [], "agent_status": v.get("agent_status")})
        return m

    def _invoke(self, cid: str, payload: dict) -> dict:
        self.graph.invoke(payload, config={"configurable": {"thread_id": cid}, "recursion_limit": 25})
        return self._sync(cid)

    def state_of(self, cid: str) -> dict:
        self._require(cid)
        return self._sync(cid)

    def checkpoint_values(self, cid: str) -> dict:
        snap = self.graph.get_state({"configurable": {"thread_id": cid}})
        return dict(snap.values) if snap and snap.values else {}

    def send_message(self, cid: str, message: str) -> dict:
        m = self._require(cid)
        if m["status"] in ("CLOSED", "HANDOFF"):
            raise ConversationClosed(cid)
        entry = m["workflow_state"]
        pin_rejected = getattr(self.cfg, "schema_version", 1) == 2 and bool(re.fullmatch(r"\d{4}", message.strip()))
        if pin_rejected:
            message = ""
        out = self._invoke(cid, {"conversation_id": cid, "turn_message": message, "turn_kind": "message",
                                 "_turn_entry_state": entry, "current_state": entry, "pin_rejected": pin_rejected})
        return self._response(cid, out)

    def verify(self, cid: str, customer_id: str, demo_pin: str) -> dict:
        m = self._require(cid)
        if m["status"] in ("CLOSED", "HANDOFF"):
            raise ConversationClosed(cid)
        if m["workflow_state"] != "collect_pin" or m.get("required_input") != "demo_pin":
            raise InvalidState("conversation is not awaiting PIN verification")
        # The verified identity must be the one collected in THIS conversation.
        # Reject before any PIN check or billing call; reveal nothing about either ID.
        pending = m.get("pending_customer_id")
        if not pending or customer_id != pending:
            raise CustomerMismatch("customer_id does not match the ID collected in this conversation")
        # PIN lives only in this local variable and the hash check below.
        result = self.verification.verify(customer_id, demo_pin)
        demo_pin = ""  # discard secret immediately
        if result.verified:
            # Trusted binding is set server-side; the graph only reads it via the billing service.
            self.bindings.bind(cid, result.verified_customer_id)
            out = self._invoke(cid, {"conversation_id": cid, "turn_message": "", "turn_kind": "verify_success",
                                     "_turn_entry_state": "collect_pin", "current_state": "collect_pin"})
        else:
            out = self._invoke(cid, {"conversation_id": cid, "turn_message": "", "turn_kind": "verify_failed",
                                     "_turn_entry_state": "collect_pin", "current_state": "collect_pin"})
        return self._response(cid, out)

    def _response(self, cid: str, m: dict) -> dict:
        body = {"conversation_id": cid, "status": m["status"], "workflow_state": m["workflow_state"],
                "message": m.get("response") or "", "required_input": m.get("required_input"),
                "handoff_ticket_id": m.get("handoff_ticket_id"), "intent": m.get("intent")}
        if getattr(self.cfg, "schema_version", 1) == 2:
            body["workflow_version"] = 2
            body["agent_status"] = m.get("agent_status")
            body["sources"] = m.get("sources") or []
            body["handoff_ticket"] = m.get("handoff_ticket_id")
            body["developer_trace_available"] = bool(self.dev_trace)
        return body
