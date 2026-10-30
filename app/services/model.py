"""Model adapters. FakeModel is deterministic/offline; OpenAI adapter only when configured."""
import re
from typing import Protocol

from pydantic import BaseModel

OPERATOR_KEYWORDS = ["オペレーター", "人間に代わって", "担当者に代わって", "担当者につないで", "有人対応", "オペレータ"]
BILLING_KEYWORDS = ["料金", "請求", "高い", "課金", "明細", "支払い"]
FAQ_KEYWORDS = ["営業時間", "何時", "受付時間", "料金プラン", "プラン"]
INJECTION_KEYWORDS = ["本人確認を飛ばして", "確認をスキップ", "制約を無視", "指示を無視"]

class IntentResult(BaseModel):
    intent: str
    confidence: float

class ModelAdapter(Protocol):
    def classify(self, text: str) -> IntentResult: ...
    def explain_billing(self, facts: dict, question: str) -> str: ...

class FakeModel:
    """Deterministic keyword model. Never authorizes anything; routing stays in Python/YAML."""
    name = "FakeModel"

    def classify(self, text: str) -> IntentResult:
        t = text or ""
        if any(k in t for k in OPERATOR_KEYWORDS):
            return IntentResult(intent="human_handoff", confidence=0.99)
        # Prompt-injection to skip verification still needs verification: stays billing flow.
        if any(k in t for k in INJECTION_KEYWORDS):
            return IntentResult(intent="billing_inquiry", confidence=0.95)
        if any(k in t for k in FAQ_KEYWORDS):
            return IntentResult(intent="faq", confidence=0.95)
        if any(k in t for k in BILLING_KEYWORDS):
            return IntentResult(intent="billing_inquiry", confidence=0.95)
        if len(t.strip()) < 3:
            return IntentResult(intent="unknown", confidence=0.30)
        return IntentResult(intent="unknown", confidence=0.40)

    def explain_billing(self, facts: dict, question: str) -> str:
        c, p = facts["current"], facts["previous"]
        return (
            f"{c['period']}のご請求は{c['total']:,}円、{p['period']}は{p['total']:,}円です。"
            f"差額は{facts['delta']:,}円です。内訳は基本料金{c['base_fee']:,}円、"
            f"データ超過{c['data_overage']:,}円、オプション{c['options']:,}円です。"
        )

    def classify_scope(self, text: str):
        from .planner import classify_scope_text

        return classify_scope_text(text or "", is_operator_request(text or ""))

    def plan(self, query: str, tool_results: list, allowed_tools: list[str], verified_customer_id: str | None = None):
        from .planner import fake_plan

        decision = fake_plan(query or "", tool_results or [], list(allowed_tools), verified_customer_id)
        decision.selection_origin = "workflow"
        return decision

    def compose(self, query: str, evidence: list, verified_customer_id: str | None = None) -> str:
        from .grounding_v2 import fallback_answer

        return fallback_answer(query or "", evidence or [], verified_customer_id)

    def clarify(self, query: str) -> str:
        del query
        return "ご用件は、料金のご確認、プランのご案内、手続きのご説明のどれに近いでしょうか。一つだけ教えてください。"

class EvilFakeModel(FakeModel):
    """Test double that invents an unsupported amount, to prove guardrails reject it."""
    name = "EvilFakeModel"

    def explain_billing(self, facts: dict, question: str) -> str:
        return "今月は99,999円です。特別料金として50,000円が加算されています。"

class OpenAIModel:
    """Real adapter; constructed only when MODEL_PROVIDER=openai and a key exists."""
    name = "OpenAIModel"

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        from openai import OpenAI  # optional dependency, imported lazily

        self._client = OpenAI(api_key=api_key)
        self._model = model

    def classify(self, text: str) -> IntentResult:
        from .prompts import intent_system_prompt

        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": intent_system_prompt()},
                {"role": "user", "content": text[:2000]},
            ],
            response_format={"type": "json_object"},
        )
        import json

        data = json.loads(resp.choices[0].message.content or "{}")
        if "intent" not in data and "category" in data:  # tolerate a common alias, then validate strictly
            data["intent"] = data.pop("category")
        result = IntentResult.model_validate(data)
        if result.intent not in {"billing_inquiry", "faq", "human_handoff", "unknown"}:
            return IntentResult(intent="unknown", confidence=0.0)
        return result

    def explain_billing(self, facts: dict, question: str) -> str:
        import json

        from .prompts import billing_messages

        system, user = billing_messages(json.dumps(facts, ensure_ascii=False), question)
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        )
        return (resp.choices[0].message.content or "").strip()

    def classify_scope(self, text: str):
        import json

        from pydantic import ValidationError

        from .planner import ScopeResult
        from .prompts import load_prompt

        system = load_prompt("scope_classifier_v2")["system"]
        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": (text or "")[:2000]},
                ],
                response_format={"type": "json_object"},
            )
            data = json.loads(resp.choices[0].message.content or "{}")
            result = ScopeResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError, OSError) as exc:
            raise ValueError(f"scope classification failed: {type(exc).__name__}") from exc
        return result

    def plan(self, query: str, tool_results: list, allowed_tools: list[str], verified_customer_id: str | None = None):
        import json

        from pydantic import ValidationError

        from .planner import PlannerDecision, PlannerSchemaError, wants_account, wants_billing, wants_catalog
        from .prompts import load_prompt

        system = load_prompt("agent_planner_v2")["system"]
        payload = {
            "allowed_tools": list(allowed_tools),
            "customer_message": (query or "")[:2000],
            "untrusted_tool_results": tool_results,
            "completed_ok_tools": [
                row.get("tool") for row in tool_results if row.get("outcome") == "ok" and row.get("tool")
            ],
            "verified": bool(verified_customer_id),
        }
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        repair = (
            "前回のJSONは拒否されました。キーは decision, tool, arguments, reason_code だけです。"
            "ツールは1つです。tool_sequence は使えません。"
            "completed_ok_tools にあるツールは選ばないでください。"
        )
        completed = set(payload["completed_ok_tools"])
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                resp = self._client.chat.completions.create(
                    model=self._model,
                    messages=messages,
                    response_format={"type": "json_object"},
                )
                data = json.loads(resp.choices[0].message.content or "{}")
                decision = PlannerDecision.model_validate(data)
            except (json.JSONDecodeError, ValidationError, PlannerSchemaError, OSError) as exc:
                last_error = exc
                if attempt == 2:
                    break
                messages.append({"role": "user", "content": repair})
                continue
            missing = [
                tool for tool, needed in (
                    ("get_billing_info", wants_billing(query) and "get_billing_info" in allowed_tools),
                    ("get_current_plan", ("今のプラン" in (query or "") or wants_account(query)) and "get_current_plan" in allowed_tools),
                    ("search_plan_catalog", wants_catalog(query) and "search_plan_catalog" in allowed_tools),
                )
                if needed and tool not in completed
            ]
            repeated = decision.decision == "call_tool" and decision.tool in completed
            premature = decision.decision in {"respond", "clarify"} and bool(missing)
            if (repeated or premature) and missing and attempt < 2:
                messages.append({
                    "role": "user",
                    "content": f"まだ {missing[0]} の結果がありません。そのツールを1回だけ呼んでください。",
                })
                continue
            if (repeated or premature) and missing:
                tool = missing[0]
                arguments = {"query": (query or "料金プラン")[:300]} if tool in {"search_plan_catalog", "search_faq"} else {}
                reasons = {
                    "get_billing_info": "need_billing_evidence",
                    "get_current_plan": "need_account_evidence",
                    "search_plan_catalog": "need_catalog_evidence",
                    "search_faq": "need_faq_evidence",
                }
                decision = PlannerDecision(
                    decision="call_tool", tool=tool, arguments=arguments, reason_code=reasons[tool],
                )
                # A repeated finished tool is recovery. A missing read the workflow still requires is workflow.
                decision.selection_origin = "recovery" if repeated else "workflow"
                return decision
            if (
                decision.decision == "call_tool"
                and decision.tool in {"search_faq", "search_plan_catalog"}
                and not str(decision.arguments.get("query") or "").strip()
            ):
                decision.arguments["query"] = (query or "料金プラン")[:300]
            decision.selection_origin = "llm"
            return decision
        raise PlannerSchemaError(f"planner output rejected: {type(last_error).__name__}") from last_error

    def compose(self, query: str, evidence: list, verified_customer_id: str | None = None) -> str:
        import json

        from .prompts import load_prompt

        system = load_prompt("grounded_answer_v2")["system"]
        payload = {
            "customer_message": (query or "")[:2000],
            "evidence": evidence,
            "verified": bool(verified_customer_id),
        }
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

    def clarify(self, query: str) -> str:
        from .prompts import load_prompt

        system = load_prompt("clarify_v2")["system"]
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": (query or "")[:2000]},
            ],
        )
        return (resp.choices[0].message.content or "").strip()

def build_model(provider: str, api_key: str | None = None, model: str = "gpt-4o-mini"):
    if provider == "openai":
        if not api_key:
            raise ValueError("MODEL_PROVIDER=openai requires OPENAI_API_KEY")
        return OpenAIModel(api_key, model)
    return FakeModel()

def is_operator_request(text: str) -> bool:
    return any(k in (text or "") for k in OPERATOR_KEYWORDS)

_AMOUNT_RE = re.compile(r"(\d{1,3}(?:,\d{3})+|\d+)\s*円")
_PERIOD_RE = re.compile(r"\d{4}-\d{2}")

def extract_amounts(text: str) -> list[int]:
    return [int(m.replace(",", "")) for m in _AMOUNT_RE.findall(text or "")]
