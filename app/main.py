import os
import time
from collections import defaultdict, deque
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.schemas import MessageIn, VerifyIn
from .core.config import ROOT, get_model_provider, get_workflow_path
from .services.billing import BillingService, TrustedBindings
from .services.conversation import (
    ConversationClosed,
    ConversationNotFound,
    ConversationService,
    CustomerMismatch,
    InvalidState,
)
from .services.faq import FaqService
from .services.handoff import HandoffService
from .services.model import build_model
from .services.verification import VerificationService
from .workflow.loader import load_workflow


class RateLimiter:
    """In-process sliding window per client IP. Demo-only (single process, resets on restart)."""

    def __init__(self, limit_per_minute: int):
        self.limit = max(1, limit_per_minute)
        self.hits: dict[str, deque] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        q = self.hits[key]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True


def create_app(workflow_path: Path | None = None, model=None, billing=None,
               debug_enabled: bool | None = None, rate_limit_per_minute: int | None = None,
               dev_trace_enabled: bool | None = None, accounts=None, catalog=None,
               support_faq=None) -> FastAPI:
    cfg = load_workflow(workflow_path or get_workflow_path(), prompt_dir=ROOT / "config/prompts")
    bindings = billing.bindings if billing is not None else TrustedBindings()
    mdl = model or build_model(get_model_provider(), os.environ.get("OPENAI_API_KEY") or None,
                               os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    bill = billing or BillingService(ROOT / "data/demo_invoices.json", bindings,
                                     retries=cfg.settings.billing_read_retries)
    handoff = HandoffService()
    tools = None
    if getattr(cfg, "schema_version", 1) == 2:
        from .services.tools_v2 import AccountService, CatalogService, SupportFaqIndex, ToolRegistry

        accounts = accounts or AccountService(ROOT / "data/customer_plans.json")
        catalog = catalog or CatalogService(ROOT / "data/plan_catalog.yaml")
        support_faq = support_faq or SupportFaqIndex(ROOT / "data/support_faq.yaml", cfg.agent.faq_min_relevance)
        tools = ToolRegistry(bill, accounts, catalog, support_faq, handoff, cfg.agent)
    if dev_trace_enabled is None:
        dev_trace_enabled = os.environ.get("ENABLE_DEV_TRACE", "false").lower() == "true"
    svc = ConversationService(cfg, mdl, bill, FaqService(ROOT / "data/approved_faq.yaml"),
                              handoff, VerificationService(ROOT / "data/demo_customers.json"), bindings,
                              tools=tools, dev_trace=bool(dev_trace_enabled))
    if debug_enabled is None:
        debug_enabled = os.environ.get("DEBUG_ENDPOINT_ENABLED", "false").lower() == "true"
    limiter = RateLimiter(rate_limit_per_minute or int(os.environ.get("RATE_LIMIT_PER_MINUTE", "120")))
    app = FastAPI(title="AI Call Center PoC", version="0.1.0")
    app.state.service = svc
    app.state.cfg = cfg
    app.state.model_name = getattr(mdl, "name", type(mdl).__name__)
    app.state.debug_enabled = debug_enabled
    app.add_middleware(CORSMiddleware, allow_origins=[
        "http://localhost:3000", "http://localhost:8080", "http://localhost:3010", "http://127.0.0.1:3010",
    ], allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            raise exc
        return JSONResponse(status_code=503, content={"detail": "backend unavailable"})

    def guard(request: Request, cid: str, token: str | None) -> None:
        if cid not in svc.meta:
            raise HTTPException(404, "unknown conversation")
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(f"{client}:{cid}"):
            raise HTTPException(429, "too many requests; retry later")
        if not svc.authorize(cid, token):
            raise HTTPException(401, "missing or invalid conversation token")

    @app.get("/health")
    def health():
        return {"status": "ok", "workflow_id": cfg.workflow_id, "model": app.state.model_name}

    @app.get("/readiness")
    def readiness():
        return {"status": "ready", "workflow_id": cfg.workflow_id, "model": app.state.model_name,
                "debug_endpoint": debug_enabled, "dev_trace": bool(dev_trace_enabled)}

    @app.post("/api/v1/conversations")
    def create(request: Request):
        client = request.client.host if request.client else "unknown"
        if not limiter.allow(f"{client}:create"):
            raise HTTPException(429, "too many requests; retry later")
        return svc.create()

    @app.post("/api/v1/conversations/{cid}/messages")
    def message(cid: str, body: MessageIn, request: Request,
                x_conversation_token: str | None = Header(default=None)):
        guard(request, cid, x_conversation_token)
        if not body.message.strip():
            raise HTTPException(400, "empty message")
        try:
            return svc.send_message(cid, body.message)
        except ConversationNotFound:
            raise HTTPException(404, "unknown conversation") from None
        except ConversationClosed:
            raise HTTPException(409, "conversation closed; create a new conversation") from None

    @app.post("/api/v1/conversations/{cid}/verify")
    def verify(cid: str, body: VerifyIn, request: Request,
               x_conversation_token: str | None = Header(default=None)):
        guard(request, cid, x_conversation_token)
        try:
            return svc.verify(cid, body.customer_id, body.demo_pin)
        except ConversationNotFound:
            raise HTTPException(404, "unknown conversation") from None
        except ConversationClosed:
            raise HTTPException(409, "conversation closed; create a new conversation") from None
        except CustomerMismatch:
            raise HTTPException(400, "customer_id does not match this conversation") from None
        except InvalidState:
            raise HTTPException(409, "conversation is not awaiting verification") from None

    @app.get("/api/v1/conversations/{cid}/trace")
    def trace(cid: str, request: Request, x_conversation_token: str | None = Header(default=None)):
        """Sanitized tool trace. Off unless ENABLE_DEV_TRACE=true, and still token-scoped."""
        if not dev_trace_enabled:
            raise HTTPException(404, "trace endpoint disabled")
        guard(request, cid, x_conversation_token)
        events = svc.checkpoint_values(cid).get("trace_events") or []
        return {"events": [_public_trace(event) for event in events]}

    @app.get("/api/v1/conversations/{cid}/debug")
    def debug(cid: str, request: Request, x_conversation_token: str | None = Header(default=None)):
        """Sanitized debug view. Disabled unless DEBUG_ENDPOINT_ENABLED=true (demo only)."""
        if not debug_enabled:
            raise HTTPException(404, "debug endpoint disabled")
        guard(request, cid, x_conversation_token)
        m = svc.state_of(cid)
        return {"conversation_id": cid, "status": m["status"], "workflow_state": m["workflow_state"],
                "intent": m.get("intent"), "intent_confidence": m.get("intent_confidence"),
                "required_input": m.get("required_input"), "handoff_ticket_id": m.get("handoff_ticket_id"),
                "trace": m.get("trace", [])}

    return app

def _public_trace(event: dict) -> dict:
    return {
        "step": event.get("step"),
        "tool": event.get("tool"),
        "policy": event.get("policy"),
        "duration_ms": event.get("duration_ms"),
        "outcome": event.get("outcome"),
        "source_ids": list(event.get("source_ids") or []),
    }


app = create_app()
