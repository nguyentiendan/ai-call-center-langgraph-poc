"""Read-only Phase 2 tools. Protected tools bind the server conversation, never an LLM customer id."""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .billing import BillingAccessDenied, BillingNotFound, BillingUnavailable

UNTRUSTED_MARKERS = ("無視", "ignore policy", "SYSTEM:")


class ToolTimeout(TimeoutError):
    pass


class ToolNotFound(LookupError):
    pass


class ToolDenied(PermissionError):
    pass


class CatalogService:
    def __init__(self, path: Path):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.plans = list(data.get("plans") or [])

    def search(self, query: str, min_gb: int | None = None, max_price_yen: int | None = None) -> list[dict]:
        del query  # catalog filters are structured; the query is not executed as code
        found: list[dict] = []
        for plan in self.plans:
            allowance = plan.get("data_allowance_gb")
            if min_gb is not None and allowance is not None and allowance < min_gb:
                continue
            if max_price_yen is not None and plan["monthly_yen"] > max_price_yen:
                continue
            found.append(dict(plan))
        found.sort(key=lambda plan: plan["monthly_yen"])
        return found


class AccountService:
    def __init__(self, path: Path, fail_mode: str | None = None):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.accounts = data["accounts"]
        self.fail_mode = fail_mode
        self.attempts = 0

    def get(self, customer_id: str) -> dict:
        self.attempts += 1
        if self.fail_mode == "timeout":
            raise ToolTimeout("simulated plan timeout")
        row = self.accounts.get(customer_id)
        if row is None:
            raise ToolNotFound(customer_id)
        return dict(row)


class SupportFaqIndex:
    def __init__(self, path: Path, min_relevance: float):
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        self.faqs = list(data.get("faqs") or [])
        self.min_relevance = min_relevance

    def search(self, query: str, top_k: int = 3) -> tuple[list[dict], int]:
        ranked: list[tuple[float, dict]] = []
        withheld = 0
        for item in self.faqs:
            score = _score(query, item.get("keywords") or [])
            if score < self.min_relevance:
                continue
            if _untrusted(item.get("answer") or ""):
                withheld += 1
                continue
            ranked.append((score, item))
        ranked.sort(key=lambda pair: pair[0], reverse=True)
        snippets = []
        for score, item in ranked[:top_k]:
            snippets.append({
                "source_id": item["source_id"],
                "title": item["title"],
                "answer": item["answer"],
                "score": round(score, 3),
            })
        return snippets, withheld


def _score(query: str, keywords: list[str]) -> float:
    if not keywords:
        return 0.0
    hits = sum(1 for keyword in keywords if keyword and keyword in (query or ""))
    return hits / len(keywords)


def _untrusted(text: str) -> bool:
    return any(marker in text for marker in UNTRUSTED_MARKERS)


def _ok(tool: str, data: dict, sources: list[dict], yen_amounts: list[int]) -> dict:
    return {
        "tool": tool,
        "outcome": "ok",
        "error": None,
        "source_ids": [source["source_id"] for source in sources],
        "sources": sources,
        "yen_amounts": yen_amounts,
        "data": data,
    }


def _err(tool: str, code: str) -> dict:
    return {
        "tool": tool,
        "outcome": "error",
        "error": code,
        "source_ids": [],
        "sources": [],
        "yen_amounts": [],
        "data": {},
    }


class ToolRegistry:
    def __init__(self, billing, accounts: AccountService, catalog: CatalogService,
                 faq: SupportFaqIndex, handoff, limits):
        self.billing = billing
        self.accounts = accounts
        self.catalog = catalog
        self.faq = faq
        self.handoff = handoff
        self.limits = limits
        self.calls = {name: 0 for name in ("search_faq", "search_plan_catalog", "get_billing_info",
                                           "get_current_plan", "request_handoff")}
        self.order: list[str] = []

    def execute(self, name: str, arguments: dict, *, conversation_id: str, verified_customer_id: str | None) -> dict:
        self.calls[name] = self.calls.get(name, 0) + 1
        self.order.append(name)
        tries = int(self.limits.max_retry_per_tool) + 1
        for attempt in range(tries):
            try:
                return self._once(name, arguments, conversation_id, verified_customer_id)
            except (ToolTimeout, BillingUnavailable):
                if attempt == tries - 1:
                    return _err(name, "timeout")
            except (ToolNotFound, BillingNotFound):
                return _err(name, "not_found")
            except (ToolDenied, BillingAccessDenied):
                return _err(name, "denied")
        return _err(name, "unavailable")

    def _once(self, name: str, arguments: dict, conversation_id: str, verified_customer_id: str | None) -> dict:
        if name == "search_faq":
            snippets, withheld = self.faq.search(arguments["query"], arguments.get("top_k", 3))
            data = {"snippets": snippets, "withheld": withheld}
            sources = [{"source_id": row["source_id"], "title": row["title"]} for row in snippets]
            return _ok(name, data, sources, [])
        if name == "search_plan_catalog":
            plans = self.catalog.search(arguments["query"], arguments.get("min_gb"), arguments.get("max_price_yen"))
            view = [{
                "source_id": plan["source_id"],
                "title": plan["title"],
                "monthly_yen": plan["monthly_yen"],
                "data_allowance_gb": plan.get("data_allowance_gb"),
                "restrictions": plan["restrictions"],
            } for plan in plans]
            sources = [{"source_id": plan["source_id"], "title": plan["title"]} for plan in view]
            return _ok(name, {"plans": view}, sources, [plan["monthly_yen"] for plan in view])
        if name == "get_billing_info":
            return self._billing(conversation_id, verified_customer_id)
        if name == "get_current_plan":
            return self._account(verified_customer_id)
        if name == "request_handoff":
            ticket = self.handoff.create(
                conversation_id,
                arguments.get("reason_code") or "agent_requested",
                "support agent requested handoff",
                idempotency_key=f"handoff:{conversation_id}",
            )
            return _ok(name, {"ticket_id": ticket["ticket_id"], "status": ticket["status"]}, [], [])
        raise ToolDenied(f"unregistered tool {name}")

    def _billing(self, conversation_id: str, verified_customer_id: str | None) -> dict:
        if not verified_customer_id:
            raise ToolDenied("billing requires a verified customer")
        invoice = self.billing.get_billing(conversation_id)
        current, previous = invoice["current"], invoice["previous"]
        yen = [
            current["total"], previous["total"], invoice["delta"],
            current["base_fee"], current["data_overage"], current["options"],
            previous["base_fee"], previous["data_overage"], previous["options"],
        ]
        sources = [
            {"source_id": f"invoice-{current['period']}", "title": f"{current['period']}の請求"},
            {"source_id": f"invoice-{previous['period']}", "title": f"{previous['period']}の請求"},
        ]
        data = {
            "month": current["period"],
            "amount": current["total"],
            "prior_month": previous["period"],
            "prior_amount": previous["total"],
            "delta": invoice["delta"],
            "itemized": {
                "base_fee": current["base_fee"],
                "data_overage": current["data_overage"],
                "options": current["options"],
            },
            "reason_codes": list(invoice.get("reason_codes") or []),
        }
        return _ok("get_billing_info", data, sources, yen)

    def _account(self, verified_customer_id: str | None) -> dict:
        if not verified_customer_id:
            raise ToolDenied("plan lookup requires a verified customer")
        row = self.accounts.get(verified_customer_id)
        data = {
            "plan_id": row["plan_id"],
            "name": row["name"],
            "monthly_yen": row["monthly_yen"],
            "data_allowance_gb": row.get("data_allowance_gb"),
            "usage_gb": row["usage_gb"],
            "options_yen": row["options_yen"],
            "source_id": row["source_id"],
        }
        sources = [{"source_id": row["source_id"], "title": row["name"]}]
        return _ok("get_current_plan", data, sources, [row["monthly_yen"], row["options_yen"]])
