"""Phase 2 YAML contract. Temp copies only; source workflows stay untouched."""
import copy

import pytest
import yaml

from app.core.config import ROOT
from app.workflow.loader import WorkflowValidationError, load_workflow

V2 = ROOT / "config/workflows/support_agent_v2.yaml"
PROMPTS = ROOT / "config/prompts"


def _write(tmp_path, data):
    path = tmp_path / "support.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _base():
    return yaml.safe_load(V2.read_text(encoding="utf-8"))


def test_phase2_workflow_loads_and_hashes_prompts():
    cfg = load_workflow(V2, prompt_dir=PROMPTS)
    assert cfg.schema_version == 2
    assert cfg.workflow_id == "telecom_support_agent_v2"
    assert cfg.policies["public_support"].requires_verified_customer is False
    assert "get_billing_info" not in cfg.policies["public_support"].allowed_tools
    assert set(cfg.prompt_hashes) == {"planner", "answer", "clarify"}
    assert all(len(v) == 64 for v in cfg.prompt_hashes.values())


def test_phase1_workflow_still_version_1():
    cfg = load_workflow(ROOT / "config/workflows/billing_v1.yaml", prompt_dir=PROMPTS)
    assert cfg.schema_version == 1
    assert cfg.workflow_id == "telecom_billing_v1"


def test_p2_24_public_policy_cannot_grant_billing(tmp_path):
    data = _base()
    data["policies"]["public_support"]["allowed_tools"] = ["search_faq", "get_billing_info"]
    with pytest.raises(WorkflowValidationError, match="protected"):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_unknown_tool_rejected(tmp_path):
    data = _base()
    data["policies"]["public_support"]["allowed_tools"] = ["search_faq", "drop_database"]
    with pytest.raises(WorkflowValidationError):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_prompt_traversal_rejected(tmp_path):
    data = _base()
    data["agent"]["prompts"]["planner"] = "../billing_v1.yaml"
    with pytest.raises(WorkflowValidationError):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_missing_prompt_rejected(tmp_path):
    data = _base()
    data["agent"]["prompts"]["answer"] = "missing_answer_v2.yaml"
    with pytest.raises(WorkflowValidationError, match="missing"):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_protected_agent_without_verification_edge_rejected(tmp_path):
    data = _base()
    data["states"]["classify"]["transitions"]["public"] = "support_agent_protected"
    with pytest.raises(WorkflowValidationError, match="without verification"):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_unlimited_tool_budget_rejected(tmp_path):
    data = _base()
    data["agent"]["max_tool_calls_per_turn"] = 100
    with pytest.raises(WorkflowValidationError):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_unreachable_handoff_rejected(tmp_path):
    data = _base()
    for state in data["states"].values():
        transitions = state.get("transitions") or {}
        for key, target in list(transitions.items()):
            if target == "human_handoff":
                transitions[key] = "respond"
    with pytest.raises(WorkflowValidationError):
        load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)


def test_temp_copy_can_drop_public_catalog_tool(tmp_path):
    data = copy.deepcopy(_base())
    data["policies"]["public_support"]["allowed_tools"] = ["search_faq", "request_handoff"]
    cfg = load_workflow(_write(tmp_path, data), prompt_dir=PROMPTS)
    assert "search_plan_catalog" not in cfg.policies["public_support"].allowed_tools
    original = load_workflow(V2, prompt_dir=PROMPTS)
    assert "search_plan_catalog" in original.policies["public_support"].allowed_tools
