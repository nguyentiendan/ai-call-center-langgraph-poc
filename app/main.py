import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .api.schemas import MessageIn, VerifyIn
from .core.config import ROOT, get_model_provider, get_workflow_path
from .services.billing import BillingService, TrustedBindings
from .services.conversation import ConversationClosed, ConversationNotFound, ConversationService, InvalidState
from .services.faq import FaqService
from .services.handoff import HandoffService
from .services.model import build_model
from .services.verification import VerificationService
from .workflow.loader import load_workflow


def create_app(workflow_path: Path | None = None, model=None, billing=None) -> FastAPI:
    cfg = load_workflow(workflow_path or get_workflow_path(), prompt_dir=ROOT / "config/prompts")
    bindings = billing.bindings if billing is not None else TrustedBindings()
    mdl = model or build_model(get_model_provider(), os.environ.get("OPENAI_API_KEY") or None,
                               os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))
    bill = billing or BillingService(ROOT / "data/demo_invoices.json", bindings,
                                     retries=cfg.settings.billing_read_retries)
    svc = ConversationService(cfg, mdl, bill, FaqService(ROOT / "data/approved_faq.yaml"),
                              HandoffService(), VerificationService(ROOT / "data/demo_customers.json"), bindings)
    app = FastAPI(title="AI Call Center PoC", version="0.1.0")
    app.state.service = svc
    app.state.cfg = cfg
    app.state.model_name = getattr(mdl, "name", type(mdl).__name__)
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:3000", "http://localhost:8080"],
                       allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        if isinstance(exc, HTTPException):
            raise exc
        return JSONResponse(status_code=503, content={"detail": "backend unavailable"})

    @app.get("/health")
    def health():
        return {"status": "ok", "workflow_id": cfg.workflow_id, "model": app.state.model_name}

    @app.post("/api/v1/conversations")
    def create():
        return svc.create()

    @app.post("/api/v1/conversations/{cid}/messages")
    def message(cid: str, body: MessageIn):
        if not body.message.strip():
            raise HTTPException(400, "empty message")
        try:
            return svc.send_message(cid, body.message)
        except ConversationNotFound:
            raise HTTPException(404, "unknown conversation") from None
        except ConversationClosed:
            raise HTTPException(409, "conversation closed; create a new conversation") from None

    @app.post("/api/v1/conversations/{cid}/verify")
    def verify(cid: str, body: VerifyIn):
        try:
            return svc.verify(cid, body.customer_id, body.demo_pin)
        except ConversationNotFound:
            raise HTTPException(404, "unknown conversation") from None
        except ConversationClosed:
            raise HTTPException(409, "conversation closed; create a new conversation") from None
        except InvalidState:
            raise HTTPException(409, "conversation is not awaiting verification") from None

    @app.get("/api/v1/conversations/{cid}/debug")
    def debug(cid: str):
        """Sanitized debug view: state/intent/trace only. Never secrets or billing facts."""
        try:
            m = svc.state_of(cid)
        except ConversationNotFound:
            raise HTTPException(404, "unknown conversation") from None
        return {"conversation_id": cid, "status": m["status"], "workflow_state": m["workflow_state"],
                "intent": m.get("intent"), "intent_confidence": m.get("intent_confidence"),
                "required_input": m.get("required_input"), "handoff_ticket_id": m.get("handoff_ticket_id"),
                "trace": m.get("trace", [])}

    return app

app = create_app()
