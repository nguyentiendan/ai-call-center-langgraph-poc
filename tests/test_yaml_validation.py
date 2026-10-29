import copy

import pytest
import yaml

from app.core.config import ROOT
from app.workflow.loader import WorkflowValidationError, load_workflow

BASE = yaml.safe_load((ROOT / "config/workflows/billing_v1.yaml").read_text())

def write_tmp(tmp_path, data):
    p = tmp_path / "w.yaml"
    p.write_text(yaml.safe_dump(data, allow_unicode=True))
    return p

def test_valid_canonical_loads():
    cfg = load_workflow(ROOT / "config/workflows/billing_v1.yaml")
    assert cfg.workflow_id == "telecom_billing_v1"
    assert cfg.states["get_billing"].requires_verified_identity is True

def test_t12_unknown_action_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["states"]["classify_intent"]["action"] = "run_shell"
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_t12_unknown_tool_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["settings"]["allowed_tools"] = ["drop_database"]
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_t13_missing_transition_state_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["states"]["classify_intent"]["transitions"]["faq"] = "nope"
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_unknown_schema_version_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["schema_version"] = 99
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_billing_without_verification_flag_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["states"]["get_billing"]["requires_verified_identity"] = False
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_attempts_above_hard_max_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["settings"]["max_verification_attempts"] = 99
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")

def test_missing_prompt_ref_rejected(tmp_path):
    d = copy.deepcopy(BASE); d["states"]["explain_bill"]["prompt_ref"] = "missing_prompt"
    with pytest.raises(WorkflowValidationError):
        load_workflow(write_tmp(tmp_path, d), prompt_dir=ROOT / "config/prompts")
