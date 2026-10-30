from app.services.prompts import billing_messages, intent_system_prompt, load_prompt


def test_intent_prompt_comes_from_yaml_with_json_contract():
    text = intent_system_prompt()
    assert "問い合わせ分類器" in text and '"intent"' in text and '"confidence"' in text

def test_billing_prompt_renders_template_from_yaml():
    system, user = billing_messages('{"total": 8800}', "なぜ高い？")
    assert "推測・創作しない" in system
    assert "なぜ高い？" in user and "8800" in user

def test_prompt_files_have_ids():
    assert load_prompt("intent")["id"] == "intent_classifier"
    assert load_prompt("explain_billing")["id"] == "explain_billing"


def test_scope_classifier_prompt_is_versioned_yaml():
    from pathlib import Path

    prompt = load_prompt("scope_classifier_v2")
    assert prompt["id"] == "scope_classifier_v2"
    assert prompt["version"] == 1
    assert "public" in prompt["system"] and "protected" in prompt["system"]
    source = Path("app/services/model.py").read_text(encoding="utf-8")
    assert "Classify the customer message as public" not in source
