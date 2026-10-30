"""Structured planner decisions. Authorization is not decided here."""
from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..workflow.tool_specs import KNOWN_TOOLS

CUSTOMER_RE = re.compile(r"CUST-\d{4}")
REASON_RE = re.compile(r"[a-z_]{1,40}")

BILLING_MARKERS = ("請求", "料金", "高く", "高い", "課金")
ACCOUNT_MARKERS = ("契約プラン", "現在のプラン", "私のプラン", "今のプラン", "使用量")
CATALOG_MARKERS = ("おすすめ", "安いプラン", "一般的", "一番安い")
FAQ_MARKERS = ("手数料", "営業時間", "受付時間", "解約金", "データ超過", "社内メモ", "学生割引", "ローミング")
MUTATION_MARKERS = ("変更してください", "変更して", "契約して", "解約して", "返金", "支払って", "切り替えて")
INJECTION_MARKERS = ("無視", "本人確認なし", "本人確認を飛ば", "管理者の指示", "前のルール", "指示を無視", "制約を無視")


class PlannerSchemaError(ValueError):
    """The model output is not a valid planner decision."""


class ScopeResult(BaseModel):
    scope: Literal["public", "protected", "mixed", "handoff", "unclear"]
    confidence: float


class PlannerDecision(BaseModel):
    """selection_origin is server-owned. Model JSON cannot set it."""

    model_config = ConfigDict(extra="ignore")
    decision: Literal["call_tool", "respond", "clarify", "handoff"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    reason_code: str
    selection_origin: Literal["llm", "workflow", "recovery"] = "llm"

    @model_validator(mode="before")
    @classmethod
    def _drop_client_origin(cls, value):
        if isinstance(value, dict):
            value = dict(value)
            value.pop("selection_origin", None)
        return value

    @field_validator("reason_code")
    @classmethod
    def _reason(cls, value: str) -> str:
        if not REASON_RE.fullmatch(value):
            raise ValueError("reason_code must be a short snake_case label")
        return value

    @model_validator(mode="after")
    def _tool(self):
        self.arguments.pop("customer_id", None)
        self.arguments.pop("verified", None)
        self.arguments.pop("role", None)
        if self.decision == "call_tool":
            if self.tool not in KNOWN_TOOLS:
                raise ValueError(f"unknown tool: {self.tool}")
        else:
            self.tool = None
            self.arguments = {}
        return self


def wants_billing(text: str) -> bool:
    return any(marker in text for marker in BILLING_MARKERS)


def wants_account(text: str) -> bool:
    return any(marker in text for marker in ACCOUNT_MARKERS)


def wants_catalog(text: str) -> bool:
    return any(marker in text for marker in CATALOG_MARKERS)


def wants_faq(text: str) -> bool:
    return any(marker in text for marker in FAQ_MARKERS)


def is_mutation(text: str) -> bool:
    return any(marker in text for marker in MUTATION_MARKERS)


def is_injection(text: str) -> bool:
    return any(marker in text for marker in INJECTION_MARKERS)


def mentioned_customers(text: str) -> list[str]:
    return CUSTOMER_RE.findall(text or "")


def task_labels(text: str) -> list[str]:
    labels: list[str] = []
    if wants_billing(text):
        labels.append("billing")
    if wants_account(text):
        labels.append("account")
    if wants_catalog(text):
        labels.append("catalog")
    if wants_faq(text):
        labels.append("faq")
    if is_mutation(text):
        labels.append("mutation")
    return labels


def classify_scope_text(text: str, operator: bool) -> ScopeResult:
    raw = text or ""
    if operator:
        return ScopeResult(scope="handoff", confidence=0.99)
    if is_injection(raw):
        if wants_billing(raw) or "データ" in raw or "請求" in raw:
            return ScopeResult(scope="protected", confidence=0.95)
        return ScopeResult(scope="unclear", confidence=0.9)
    if is_mutation(raw) and "プラン" in raw and not (wants_billing(raw) or wants_account(raw)):
        return ScopeResult(scope="public", confidence=0.95)
    private = wants_billing(raw) or wants_account(raw)
    public = wants_catalog(raw) or wants_faq(raw)
    if private and public:
        return ScopeResult(scope="mixed", confidence=0.95)
    if private:
        return ScopeResult(scope="protected", confidence=0.95)
    if public:
        return ScopeResult(scope="public", confidence=0.95)
    if len(raw.strip()) < 3:
        return ScopeResult(scope="unclear", confidence=0.30)
    return ScopeResult(scope="unclear", confidence=0.40)


def _call(tool: str, query: str) -> PlannerDecision:
    reasons = {
        "get_billing_info": "need_billing_evidence",
        "get_current_plan": "need_account_evidence",
        "search_plan_catalog": "need_catalog_evidence",
        "search_faq": "need_faq_evidence",
        "request_handoff": "user_handoff",
    }
    arguments: dict[str, Any] = {}
    if tool == "search_faq":
        if "社内メモ" in query:
            faq_query = "社内メモ"
        elif "手数料" in query:
            faq_query = "プラン変更の手数料"
        elif "営業時間" in query or "受付時間" in query:
            faq_query = "営業時間"
        elif "学生" in query:
            faq_query = "学生割引"
        else:
            faq_query = "データ超過"
        arguments = {"query": faq_query, "top_k": 1}
    elif tool == "search_plan_catalog":
        arguments = {"query": "料金プラン"}
    elif tool == "request_handoff":
        arguments = {"reason_code": "agent_requested"}
    return PlannerDecision(decision="call_tool", tool=tool, arguments=arguments, reason_code=reasons[tool])


def planned_tools(query: str, allowed_tools: list[str]) -> list[str]:
    billing = wants_billing(query)
    account = wants_account(query)
    catalog = wants_catalog(query) and not account
    faq = wants_faq(query) or billing
    steps: list[str] = []
    if billing and "get_billing_info" in allowed_tools:
        steps.append("get_billing_info")
    if (account or (billing and wants_catalog(query))) and "get_current_plan" in allowed_tools:
        steps.append("get_current_plan")
    if catalog and "search_plan_catalog" in allowed_tools:
        steps.append("search_plan_catalog")
    if faq and "search_faq" in allowed_tools:
        steps.append("search_faq")
    return steps


def fake_plan(query: str, tool_results: list[dict], allowed_tools: list[str],
              verified_customer_id: str | None) -> PlannerDecision:
    """Deterministic tool choice for offline tests. It never grants access by itself."""
    errors = [row for row in tool_results if row.get("outcome") == "error"]
    if any(row.get("error") == "timeout" for row in errors):
        return PlannerDecision(decision="handoff", reason_code="tool_error")
    if any(row.get("tool") == "get_billing_info" and row.get("error") == "not_found" for row in errors):
        return PlannerDecision(decision="handoff", reason_code="tool_error")
    done = {row["tool"] for row in tool_results if row.get("outcome") == "ok"}
    if is_mutation(query):
        if (wants_catalog(query) and "search_plan_catalog" in allowed_tools
                and "search_plan_catalog" not in done):
            return _call("search_plan_catalog", query)
        return PlannerDecision(decision="respond", reason_code="mutation_not_supported")
    mentioned = mentioned_customers(query)
    if mentioned and (verified_customer_id is None or any(cid != verified_customer_id for cid in mentioned)):
        return PlannerDecision(decision="respond", reason_code="cross_customer")
    for tool in planned_tools(query, allowed_tools):
        if tool not in done:
            return _call(tool, query)
    if not planned_tools(query, allowed_tools) and not done:
        return PlannerDecision(decision="clarify", reason_code="need_clarification")
    return PlannerDecision(decision="respond", reason_code="sufficient_evidence")
