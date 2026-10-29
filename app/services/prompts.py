"""Loads version-controlled YAML prompt templates. Prompts are data, never code."""
from pathlib import Path

import yaml

from ..core.config import ROOT

PROMPT_DIR = ROOT / "config/prompts"

def load_prompt(name: str, prompt_dir: Path | None = None) -> dict:
    path = (prompt_dir or PROMPT_DIR) / f"{name}.yaml"
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError(f"invalid prompt template: {path}")
    return data

def intent_system_prompt(prompt_dir: Path | None = None) -> str:
    """Classifier system prompt from intent.yaml plus the strict JSON key contract."""
    base = load_prompt("intent", prompt_dir)["system"].rstrip()
    contract = ('必ず次のキーを持つJSONだけを返してください: '
                '{"intent": "billing_inquiry|faq|human_handoff|unknown のいずれか", '
                '"confidence": 0から1の数値}。他のキーは使わないでください。')
    return f"{base}\n{contract}"

def billing_messages(facts_json: str, question: str, prompt_dir: Path | None = None) -> tuple[str, str]:
    tpl = load_prompt("explain_billing", prompt_dir)
    user = tpl["user_template"].format(user_question=question, billing_facts_json=facts_json)
    return tpl["system"], user
