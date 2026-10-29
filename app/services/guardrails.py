import re

from .model import extract_amounts

SECRET_PATTERNS = [re.compile(r"\b\d{4}\b")]  # applied only with billing context check below

def allowed_amounts(facts: dict) -> set[int]:
    vals: set[int] = set()
    for k in ("current", "previous"):
        i = facts[k]
        vals.update([i["total"], i["base_fee"], i["data_overage"], i["options"]])
    vals.add(facts["delta"])
    return vals

def grounded_template(facts: dict) -> str:
    c, p = facts["current"], facts["previous"]
    return (
        f"{c['period']}のご請求は{c['total']:,}円、{p['period']}は{p['total']:,}円です。"
        f"差額は{facts['delta']:,}円です。内訳は基本料金{c['base_fee']:,}円、"
        f"データ超過{c['data_overage']:,}円、オプション{c['options']:,}円です。"
    )

def validate_grounded(text: str, facts: dict) -> bool:
    """Every monetary figure in the reply must exist in tool facts."""
    allowed = allowed_amounts(facts)
    return all(a in allowed for a in extract_amounts(text))

def redact(text: str, customer_id: str | None = None) -> str:
    out = text or ""
    if customer_id:
        out = out.replace(customer_id, "CUST-****")
    return out
