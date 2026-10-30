"""Answers may mention amounts, plans, usage, and restrictions only from successful tool results."""
from __future__ import annotations

import re

from .planner import is_mutation, mentioned_customers, wants_catalog, wants_faq

_PLAN_ID_RE = re.compile(r"plan-[a-z0-9]+(?:-[a-z0-9]+)*")
_GB_RE = re.compile(r"(\d+(?:\.\d+)?)\s*GB")
_PLAN_NAME_RE = re.compile(r"さくらの[^\s、。（）\n]{1,24}")
_RESTRICTION_RE = re.compile(r"[^。\n]*制限[^。\n]*")


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


def _fact_rows(evidence: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for item in evidence:
        if item.get("outcome") != "ok":
            continue
        data = item.get("data") or {}
        if item.get("tool") == "search_plan_catalog":
            rows.extend(dict(plan) for plan in data.get("plans") or [])
        elif item.get("tool") == "get_current_plan":
            rows.append(dict(data))
    return rows


def claims_grounded(text: str, evidence: list[dict]) -> bool:
    """Reject plan ids, names, allowances, usage, prices, and restrictions that no tool returned."""
    if not amounts_grounded(text, evidence):
        return False
    ids: set[str] = set()
    names: set[str] = set()
    gigabytes: set[float] = set()
    restrictions: set[str] = set()
    for item in evidence:
        if item.get("outcome") != "ok":
            continue
        for source_id in item.get("source_ids") or []:
            ids.add(str(source_id))
    for row in _fact_rows(evidence):
        for key in ("plan_id", "source_id"):
            if row.get(key):
                ids.add(str(row[key]))
        for key in ("name", "title"):
            if row.get(key):
                names.add(str(row[key]))
        for key in ("data_allowance_gb", "usage_gb"):
            if row.get(key) is not None:
                gigabytes.add(float(row[key]))
        if row.get("restrictions"):
            restrictions.add(str(row["restrictions"]))
    body = text or ""
    for plan_id in _PLAN_ID_RE.findall(body):
        if plan_id not in ids and not any(plan_id in source_id for source_id in ids):
            return False
    for raw in _GB_RE.findall(body):
        value = float(raw)
        if not any(abs(value - known) < 1e-6 for known in gigabytes):
            return False
    for name in _PLAN_NAME_RE.findall(body):
        if not any(name == allowed or name in allowed for allowed in names):
            return False
    for chunk in _RESTRICTION_RE.findall(body):
        piece = chunk.strip()
        if not any(piece in item or item in piece for item in restrictions):
            return False
    return _plan_attributes_stay_together(body, _fact_rows(evidence))


def _matching_rows(token: str, rows: list[dict]) -> list[dict]:
    found: list[dict] = []
    for row in rows:
        labels = [str(row.get(key) or "") for key in ("name", "title", "plan_id", "source_id")]
        if any(label and (token == label or token in label or label.startswith(token)) for label in labels):
            found.append(row)
    return found


def _plan_attributes_stay_together(text: str, rows: list[dict]) -> bool:
    """A price or allowance after a plan mention must belong to that plan, not a neighbor."""
    from .model import extract_amounts

    mentions = [
        (match.start(), match.end(), match.group())
        for pattern in (_PLAN_NAME_RE, _PLAN_ID_RE)
        for match in pattern.finditer(text or "")
    ]
    mentions.sort()
    for index, (_start, end, token) in enumerate(mentions):
        next_start = mentions[index + 1][0] if index + 1 < len(mentions) else len(text)
        window = text[end:next_start]
        matched = _matching_rows(token, rows)
        if not matched:
            continue
        yen = set()
        gigabytes: set[float] = set()
        for row in matched:
            for key in ("monthly_yen", "options_yen"):
                if row.get(key) is not None:
                    yen.add(int(row[key]))
            for key in ("data_allowance_gb", "usage_gb"):
                if row.get(key) is not None:
                    gigabytes.add(float(row[key]))
        if any(amount not in yen for amount in extract_amounts(window)):
            return False
        if any(not any(abs(float(raw) - known) < 1e-6 for known in gigabytes) for raw in _GB_RE.findall(window)):
            return False
    return True


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
