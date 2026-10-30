"""Strict YAML workflow loader/validator. YAML is declarative only (safe_load, allowlists)."""
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, field_validator

ALLOWED_ACTIONS = {"classify_intent", "ask_clarification", "collect_field", "collect_secret",
                   "verify_demo_customer", "get_billing", "generate_grounded_response",
                   "answer_approved_faq", "create_handoff", "close_conversation"}
ALLOWED_TOOLS = {"verify_demo_customer", "get_billing", "create_handoff"}
ALLOWED_INTENTS = {"billing_inquiry", "faq", "human_handoff", "unknown"}
HARD_MAX_ATTEMPTS = 3
HARD_MAX_CLARIFICATIONS = 2
HARD_MAX_RETRIES = 2

class Settings(BaseModel):
    max_verification_attempts: int
    max_clarifications: int
    billing_read_retries: int
    confidence_threshold: float = 0.75
    verification_required_for: list[str] = Field(default_factory=list)
    allowed_tools: list[str]

    @field_validator("max_verification_attempts")
    @classmethod
    def _cap_attempts(cls, v: int) -> int:
        if v < 1 or v > HARD_MAX_ATTEMPTS:
            raise ValueError(f"max_verification_attempts must be 1..{HARD_MAX_ATTEMPTS}")
        return v

    @field_validator("max_clarifications")
    @classmethod
    def _cap_clar(cls, v: int) -> int:
        if v < 0 or v > HARD_MAX_CLARIFICATIONS:
            raise ValueError("max_clarifications out of bounds")
        return v

    @field_validator("billing_read_retries")
    @classmethod
    def _cap_retry(cls, v: int) -> int:
        if v < 0 or v > HARD_MAX_RETRIES:
            raise ValueError("billing_read_retries out of bounds")
        return v

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        bad = set(v) - ALLOWED_TOOLS
        if bad:
            raise ValueError(f"unknown tools: {sorted(bad)}")
        return v

class StateDef(BaseModel):
    action: str
    field: str | None = None
    prompt_ref: str | None = None
    requires_verified_identity: bool = False
    terminal: bool = False
    transitions: dict[str, str] = Field(default_factory=dict)

    @field_validator("action")
    @classmethod
    def _action(cls, v: str) -> str:
        if v not in ALLOWED_ACTIONS:
            raise ValueError(f"unknown action: {v}")
        return v

class WorkflowConfig(BaseModel):
    schema_version: int
    workflow_id: str
    locale: str = "ja-JP"
    initial_state: str
    settings: Settings
    intents: dict[str, str]
    states: dict[str, StateDef]

class WorkflowValidationError(ValueError):
    pass

def load_workflow(path: str | Path, prompt_dir: str | Path | None = None) -> WorkflowConfig:
    p = Path(path)
    if not p.exists():
        raise WorkflowValidationError(f"workflow file not found: {p}")
    raw: Any = yaml.safe_load(p.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise WorkflowValidationError("workflow YAML must be a mapping")
    version = raw.get("schema_version", raw.get("version"))
    if version == 2:
        from .loader_v2 import WorkflowValidationError as V2Error
        from .loader_v2 import load_workflow_v2

        prompts = Path(prompt_dir) if prompt_dir else (p.parent.parent / "prompts")
        try:
            return load_workflow_v2(raw, p, prompts)
        except V2Error as exc:
            raise WorkflowValidationError(str(exc)) from exc
    if version != 1:
        raise WorkflowValidationError(f"unsupported schema_version: {raw.get('schema_version', raw.get('version'))!r}")
    try:
        cfg = WorkflowConfig.model_validate(raw)
    except Exception as e:  # pydantic validation -> clear startup error
        raise WorkflowValidationError(str(e)) from e
    _validate_graph(cfg, prompt_dir or (p.parent.parent / "prompts"))
    return cfg

def _validate_graph(cfg: WorkflowConfig, prompt_dir: Path) -> None:
    states = cfg.states
    if cfg.initial_state not in states:
        raise WorkflowValidationError(f"initial_state '{cfg.initial_state}' not in states")
    if set(cfg.intents) - ALLOWED_INTENTS:
        raise WorkflowValidationError(f"unknown intents: {sorted(set(cfg.intents) - ALLOWED_INTENTS)}")
    for name, target in cfg.intents.items():
        if target not in states:
            raise WorkflowValidationError(f"intent '{name}' maps to missing state '{target}'")
    for name, st in states.items():
        if st.terminal:
            continue
        if not st.transitions:
            raise WorkflowValidationError(f"non-terminal state '{name}' has no transitions")
        for outcome, target in st.transitions.items():
            if target not in states:
                raise WorkflowValidationError(
                    f"state '{name}' transition '{outcome}' references missing state '{target}'")
        if st.prompt_ref and not (Path(prompt_dir) / f"{st.prompt_ref}.yaml").exists():
            raise WorkflowValidationError(f"prompt_ref '{st.prompt_ref}' file missing in {prompt_dir}")
    # Non-configurable security invariant: billing state must require verified identity.
    for name, st in states.items():
        if st.action == "get_billing" and not st.requires_verified_identity:
            raise WorkflowValidationError(f"state '{name}': get_billing must set requires_verified_identity: true")
    if "get_billing" not in cfg.settings.verification_required_for:
        raise WorkflowValidationError("settings.verification_required_for must include get_billing")
    # Reachability of required nodes from initial_state (BFS over transitions + intents).
    seen, stack = set(), [cfg.initial_state]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(states[cur].transitions.values())
    for required in ("handoff", "close"):
        if required in states and required not in seen:
            raise WorkflowValidationError(f"required state '{required}' unreachable from initial_state")
