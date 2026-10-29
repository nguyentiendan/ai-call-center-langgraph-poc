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
