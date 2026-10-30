"""Server-side tool authorization. Model output cannot set verified or customer_id."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..workflow.loader_v2 import PolicyDef
from ..workflow.tool_specs import KNOWN_TOOLS, PROTECTED_TOOLS
from .planner import mentioned_customers


class FaqArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    top_k: int = Field(default=3, ge=1, le=3)


class CatalogArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=300)
    min_gb: int | None = Field(default=None, ge=0, le=999)
    max_price_yen: int | None = Field(default=None, ge=0, le=100000)


class EmptyArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HandoffArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(default="agent_requested", pattern=r"[a-z_]{1,40}")


_SCHEMAS = {
    "search_faq": FaqArgs,
    "search_plan_catalog": CatalogArgs,
    "get_billing_info": EmptyArgs,
    "get_current_plan": EmptyArgs,
    "request_handoff": HandoffArgs,
}


class PolicyDecision(BaseModel):
    allowed: bool
    arguments: dict
    error: str | None = None
    reason_code: str


class PolicyGateway:
    def authorize(self, tool: str, arguments: dict | None, policy: PolicyDef, *,
                  conversation_id: str, bindings, query: str) -> PolicyDecision:
        raw = dict(arguments or {})
        raw.pop("customer_id", None)
        raw.pop("verified", None)
        raw.pop("role", None)
        if tool not in KNOWN_TOOLS or tool not in _SCHEMAS:
            return PolicyDecision(allowed=False, arguments={}, error="invalid_args", reason_code="schema_invalid")
        if tool not in policy.allowed_tools:
            return PolicyDecision(allowed=False, arguments={}, error="denied", reason_code="policy_denied")
        verified = bindings.get(conversation_id)
        if tool in PROTECTED_TOOLS:
            if not policy.requires_verified_customer or not verified:
                return PolicyDecision(allowed=False, arguments={}, error="denied", reason_code="not_verified")
            mentioned = mentioned_customers(query)
            if any(customer_id != verified for customer_id in mentioned):
                return PolicyDecision(allowed=False, arguments={}, error="denied", reason_code="cross_customer")
        try:
            parsed = _SCHEMAS[tool].model_validate(raw)
        except ValidationError:
            return PolicyDecision(allowed=False, arguments={}, error="invalid_args", reason_code="schema_invalid")
        return PolicyDecision(allowed=True, arguments=parsed.model_dump(), error=None, reason_code="allowed")
