"""Answers may mention a yen amount only when a successful tool result listed it."""
from __future__ import annotations

from .planner import is_mutation, mentioned_customers, wants_catalog, wants_faq


def allowed_amounts(evidence: list[dict]) -> set[int]:
    allowed: set[int] = set()
    for item in evidence:
        if item.get("outcome") != "ok":
            continue
        for amount in item.get("yen_amounts") or []:
            allowed.add(int(amount))
    return allowed


def amounts_grounded(text: str, evidence: list[dict]) -> bool:
    from .model import extract_amounts

    return all(amount in allowed_amounts(evidence) for amount in extract_amounts(text))


def collect_sources(evidence: list[dict]) -> list[dict]:
    sources: list[dict] = []
    seen: set[str] = set()
    for item in evidence:
        if item.get("outcome") != "ok":
            continue
        for source in item.get("sources") or []:
            sid = source.get("source_id")
            if not sid or sid in seen:
                continue
            seen.add(sid)
            sources.append({"source_id": sid, "title": source.get("title") or sid})
    return sources


def _yen(amount: int) -> str:
    return f"{amount:,}円"


def _find(evidence: list[dict], tool: str) -> dict | None:
    for item in evidence:
        if item.get("tool") == tool and item.get("outcome") == "ok":
            return item
    return None


def fallback_answer(query: str, evidence: list[dict], verified_customer_id: str | None = None) -> str:
    """Deterministic grounded reply. Used by FakeModel and by the guardrail fallback."""
    mentioned = mentioned_customers(query)
    if mentioned and (verified_customer_id is None or any(cid != verified_customer_id for cid in mentioned)):
        return "他のお客様の契約・請求情報はご案内できません。"
    parts: list[str] = []
    if is_mutation(query):
        parts.append(
            "プランの変更・解約・返金・お支払いはこのデモでは実行できません。"
            "ご希望であれば担当者への引き継ぎもできます。プラン変更は実行していません。"
        )
    billing = _find(evidence, "get_billing_info")
    if billing:
        data = billing["data"]
        sources = "、".join(billing.get("source_ids") or [])
        itemized = data["itemized"]
        parts.append(
            f"{data['month']}のご請求は{_yen(data['amount'])}、{data['prior_month']}は{_yen(data['prior_amount'])}です。"
            f"差額は{_yen(data['delta'])}です。"
            f"内訳は基本料金{_yen(itemized['base_fee'])}、データ超過{_yen(itemized['data_overage'])}、"
            f"オプション{_yen(itemized['options'])}です。出典: {sources}"
        )
        if "DATA_OVERAGE" in (data.get("reason_codes") or []):
            parts.append("差額にはデータ超過が含まれます。")
    elif any(item.get("tool") == "get_billing_info" and item.get("error") == "not_found" for item in evidence):
        parts.append("請求情報を確認できませんでした。")
    account = _find(evidence, "get_current_plan")
    if account:
        data = account["data"]
        allowance = data.get("data_allowance_gb")
        allowance_text = "使い放題（条件あり）" if allowance is None else f"{allowance}GB"
        parts.append(
            f"現在のプランは{data['name']}（月額{_yen(data['monthly_yen'])}、データ上限{allowance_text}、"
            f"使用量{data['usage_gb']}GB）です。出典: {data['source_id']}"
        )
    catalog = _find(evidence, "search_plan_catalog")
    if catalog:
        parts.append(_catalog_text(catalog["data"]["plans"], account["data"] if account else None))
    faq = _find(evidence, "search_faq")
    snippets = (faq or {}).get("data", {}).get("snippets") or []
    if snippets:
        snippet = snippets[0]
        parts.append(f"{snippet['answer']} 出典: {snippet['source_id']}")
    elif wants_faq(query) and not is_mutation(query):
        parts.append("確認できる情報がありません。")
    if wants_catalog(query) and catalog is None and not is_mutation(query):
        parts.append("確認できるプラン情報がありません。")
    if not parts:
        parts.append("確認できる情報がありません。別の聞き方をするか、担当者への引き継ぎをご利用ください。")
    return "\n".join(parts)


def _catalog_text(plans: list[dict], account: dict | None) -> str:
    usage = account.get("usage_gb") if account else None
    current = account.get("monthly_yen") if account else None
    lines: list[str] = []
    any_comparable_saving = False
    for plan in plans[:3]:
        allowance = plan.get("data_allowance_gb")
        allowance_text = "使い放題（条件あり）" if allowance is None else f"{allowance}GB"
        fits = allowance is None or (usage is not None and allowance is not None and usage <= allowance)
        cheaper = current is not None and plan["monthly_yen"] < current and fits
        any_comparable_saving = any_comparable_saving or cheaper
        lines.append(
            f"{plan['title']}（月額{_yen(plan['monthly_yen'])}、データ{allowance_text}、{plan['restrictions']}）"
            f"出典: {plan['source_id']}"
        )
    if current is None:
        caveat = "個人の使用量は確認していないため、安くなるとは断定できません。プランの変更は行っていません。"
    elif any_comparable_saving:
        caveat = "使用量が上限内の候補は月額だけを比べると現在より低いです。プランの変更は行っていません。"
    else:
        caveat = "使用量や条件が揃わないため、安くなるとは断定できません。プランの変更は行っていません。"
    return "カタログ上の候補: " + " ".join(lines) + " " + caveat


def failure_text(tool_results: list[dict]) -> str:
    if any(row.get("tool") == "get_billing_info" and row.get("error") == "not_found" for row in tool_results):
        return "請求情報を確認できませんでした。"
    if any(row.get("error") == "timeout" for row in tool_results):
        return "一時的に情報を取得できませんでした。"
    return "担当者への引き継ぎが必要です。"


def denial_text(reason_code: str) -> str:
    if reason_code == "cross_customer":
        return "他のお客様の契約・請求情報はご案内できません。"
    return "そのご依頼には対応できません。別の聞き方をするか、担当者への引き継ぎをご利用ください。"
