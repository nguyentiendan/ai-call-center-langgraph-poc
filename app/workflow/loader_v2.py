"""Phase 2 workflow schema. YAML is data: safe_load, allowlists, no code execution.

Equivalent to the design document's version-2 contract. Identity and agent
limits use `transitions` (same idea as Phase 1) instead of `on_verified` aliases:
`verified` == on_verified, `success` == on_success, `failure` == on_failure.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from .tool_specs import KNOWN_TOOLS, PROTECTED_TOOLS

HARD_MAX_ATTEMPTS = 3
HARD_MAX_CLARIFICATIONS = 2
HARD_MAX_TOOL_CALLS = 8
HARD_MAX_ITERATIONS = 8
HARD_MAX_RETRIES = 2
HARD_MAX_DENIALS = 3

class WorkflowValidationError(ValueError):
    pass


class IdentitySettings(BaseModel):
    max_verification_attempts: int = 3
    max_clarifications: int = 1
    billing_read_retries: int = 1
    confidence_threshold: float = 0.75
    verification_required_for: list[str] = Field(
        default_factory=lambda: ["get_billing_info", "get_current_plan"]
    )

    @field_validator("max_verification_attempts")
    @classmethod
    def _attempts(cls, v: int) -> int:
        if v < 1 or v > HARD_MAX_ATTEMPTS:
            raise ValueError(f"max_verification_attempts must be 1..{HARD_MAX_ATTEMPTS}")
        return v

    @field_validator("max_clarifications")
    @classmethod
    def _clar(cls, v: int) -> int:
        if v < 0 or v > HARD_MAX_CLARIFICATIONS:
            raise ValueError("max_clarifications out of bounds")
        return v

    @field_validator("billing_read_retries")
    @classmethod
    def _retry(cls, v: int) -> int:
        if v < 0 or v > HARD_MAX_RETRIES:
            raise ValueError("billing_read_retries out of bounds")
        return v

    @field_validator("confidence_threshold")
    @classmethod
    def _conf(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("confidence_threshold out of bounds")
        return v

    @field_validator("verification_required_for")
    @classmethod
    def _protected(cls, v: list[str]) -> list[str]:
        missing = PROTECTED_TOOLS - set(v)
        if missing:
            raise ValueError(f"verification_required_for must include {sorted(missing)}")
        return v


class PromptRefs(BaseModel):
    planner: str
    answer: str
    clarify: str


class AgentLimits(BaseModel):
    max_tool_calls_per_turn: int
    max_agent_iterations: int
    max_retry_per_tool: int
    max_consecutive_policy_denials: int
    timeout_seconds_per_tool: float
    faq_min_relevance: float
    prompts: PromptRefs

    @field_validator("max_tool_calls_per_turn")
    @classmethod
    def _calls(cls, v: int) -> int:
        if v < 1 or v > HARD_MAX_TOOL_CALLS:
            raise ValueError(f"max_tool_calls_per_turn must be 1..{HARD_MAX_TOOL_CALLS}")
        return v

    @field_validator("max_agent_iterations")
    @classmethod
    def _iters(cls, v: int) -> int:
        if v < 1 or v > HARD_MAX_ITERATIONS:
            raise ValueError(f"max_agent_iterations must be 1..{HARD_MAX_ITERATIONS}")
        return v

    @field_validator("max_retry_per_tool")
    @classmethod
    def _tool_retry(cls, v: int) -> int:
        if v < 0 or v > HARD_MAX_RETRIES:
            raise ValueError("max_retry_per_tool out of bounds")
        return v

    @field_validator("max_consecutive_policy_denials")
    @classmethod
    def _denials(cls, v: int) -> int:
        if v < 1 or v > HARD_MAX_DENIALS:
            raise ValueError("max_consecutive_policy_denials out of bounds")
        return v

    @field_validator("timeout_seconds_per_tool")
    @classmethod
    def _timeout(cls, v: float) -> float:
        if v <= 0 or v > 10:
            raise ValueError("timeout_seconds_per_tool must be in (0, 10]")
        return v

    @field_validator("faq_min_relevance")
    @classmethod
    def _faq(cls, v: float) -> float:
        if v < 0 or v > 1:
            raise ValueError("faq_min_relevance out of bounds")
        return v


class PolicyDef(BaseModel):
    allowed_tools: list[str]
    requires_verified_customer: bool

    @field_validator("allowed_tools")
    @classmethod
    def _tools(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("duplicate tool in policy")
        bad = set(v) - KNOWN_TOOLS
        if bad:
            raise ValueError(f"unknown tools: {sorted(bad)}")
        return v


class StateV2(BaseModel):
    kind: Literal[
        "router",
        "clarification",
        "collect_field",
        "collect_secret",
        "identity_verification",
        "agent_subgraph",
        "response_guardrail",
        "response",
        "handoff",
    ]
    policy: str | None = None
    transitions: dict[str, str] = Field(default_factory=dict)


class WorkflowV2Config(BaseModel):
    schema_version: int
    workflow_id: str
    locale: str = "ja-JP"
    initial_state: str
    settings: IdentitySettings
    agent: AgentLimits
    policies: dict[str, PolicyDef]
    states: dict[str, StateV2]
    prompt_hashes: dict[str, str] = Field(default_factory=dict)


def load_workflow_v2(raw: dict[str, Any], path: Path, prompt_dir: Path) -> WorkflowV2Config:
    version = raw.get("schema_version", raw.get("version"))
    if version != 2:
        raise WorkflowValidationError(f"unsupported schema_version: {version!r}")
    if raw.get("schema_version") not in (None, 2) or raw.get("version") not in (None, 2):
        raise WorkflowValidationError("schema_version and version disagree")
    body = dict(raw)
    body["schema_version"] = 2
    body.pop("version", None)
    try:
        cfg = WorkflowV2Config.model_validate(body)
    except WorkflowValidationError:
        raise
    except Exception as exc:
        raise WorkflowValidationError(str(exc)) from exc
    _validate(cfg, prompt_dir)
    cfg.prompt_hashes = _hash_prompts(cfg, prompt_dir)
    return cfg


def resolve_prompt(ref: str, prompt_dir: Path) -> Path:
    """Resolve a prompt ref to a file inside prompt_dir. Rejects traversal."""
    root = prompt_dir.resolve()
    if not ref or ref.strip() != ref:
        raise WorkflowValidationError(f"invalid prompt ref: {ref!r}")
    parts = Path(ref).parts
    if Path(ref).is_absolute() or any(part in {"..", ""} for part in parts):
        raise WorkflowValidationError(f"prompt path is not allowed: {ref}")
    name = Path(ref).name
    if not name.endswith(".yaml"):
        name = f"{name}.yaml"
    if name != Path(name).name or "/" in name or "\\" in name:
        raise WorkflowValidationError(f"prompt path is not allowed: {ref}")
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(root):
        raise WorkflowValidationError(f"prompt path escapes prompt dir: {ref}")
    if not candidate.is_file():
        raise WorkflowValidationError(f"prompt file missing: {name}")
    return candidate


def _hash_prompts(cfg: WorkflowV2Config, prompt_dir: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for label, ref in (
        ("planner", cfg.agent.prompts.planner),
        ("answer", cfg.agent.prompts.answer),
        ("clarify", cfg.agent.prompts.clarify),
    ):
        data = resolve_prompt(ref, prompt_dir).read_bytes()
        hashes[label] = hashlib.sha256(data).hexdigest()
    return hashes


def _validate(cfg: WorkflowV2Config, prompt_dir: Path) -> None:
    states = cfg.states
    if cfg.initial_state not in states:
        raise WorkflowValidationError(f"initial_state '{cfg.initial_state}' not in states")
    if len(states) != len(set(states)):
        raise WorkflowValidationError("duplicate state names")
    for name, policy in cfg.policies.items():
        if not policy.requires_verified_customer and (set(policy.allowed_tools) & PROTECTED_TOOLS):
            raise WorkflowValidationError(
                f"policy '{name}' is public but lists protected tools {sorted(set(policy.allowed_tools) & PROTECTED_TOOLS)}"
            )
        if policy.requires_verified_customer and not (set(policy.allowed_tools) & PROTECTED_TOOLS):
            raise WorkflowValidationError(f"policy '{name}' requires verification but grants no protected tool")
    handoff_states = [name for name, st in states.items() if st.kind == "handoff"]
    if len(handoff_states) != 1:
        raise WorkflowValidationError("workflow must declare exactly one handoff state")
    routers = [name for name, st in states.items() if st.kind == "router"]
    if cfg.initial_state not in routers:
        raise WorkflowValidationError("initial_state must be a router")
    for name, st in states.items():
        if st.kind == "agent_subgraph":
            if not st.policy or st.policy not in cfg.policies:
                raise WorkflowValidationError(f"state '{name}' requires a known policy")
            for outcome, target in (("success", "response_guardrail"), ("failure", "handoff"), ("handoff", "handoff")):
                dest = st.transitions.get(outcome)
                if dest is None or dest not in states:
                    raise WorkflowValidationError(f"state '{name}' missing '{outcome}' transition")
                if states[dest].kind != target:
                    raise WorkflowValidationError(
                        f"state '{name}' transition '{outcome}' must target kind {target}"
                    )
        elif st.kind == "handoff":
            if st.transitions:
                raise WorkflowValidationError("handoff state cannot declare transitions")
        elif st.kind == "response":
            if st.transitions:
                raise WorkflowValidationError("response state ends the turn and cannot transition further")
        elif st.kind != "router" and not st.transitions and st.kind not in {"handoff", "response"}:
            raise WorkflowValidationError(f"state '{name}' has no transitions")
        for outcome, target in st.transitions.items():
            if target not in states:
                raise WorkflowValidationError(f"state '{name}' transition '{outcome}' references missing '{target}'")
    _require_identity(cfg)
    _reject_public_edge_into_protected(cfg)
    _require_reachable_handoff(cfg)
    for ref in (cfg.agent.prompts.planner, cfg.agent.prompts.answer, cfg.agent.prompts.clarify):
        resolve_prompt(ref, prompt_dir)


def _require_identity(cfg: WorkflowV2Config) -> None:
    states = cfg.states
    verify = [name for name, st in states.items() if st.kind == "identity_verification"]
    if verify != ["verify_identity"]:
        raise WorkflowValidationError("identity state must be named verify_identity")
    node = states["verify_identity"]
    for outcome, kind in (("verified", "agent_subgraph"), ("retry", "collect_field"), ("locked", "handoff")):
        dest = node.transitions.get(outcome)
        if dest is None or states[dest].kind != kind:
            raise WorkflowValidationError(f"verify_identity transition '{outcome}' must target {kind}")
    if "collect_pin" not in states or states["collect_pin"].kind != "collect_secret":
        raise WorkflowValidationError("collect_pin collect_secret state is required")
    if "collect_customer_id" not in states or states["collect_customer_id"].kind != "collect_field":
        raise WorkflowValidationError("collect_customer_id collect_field state is required")
    protected_agents = [
        name for name, st in states.items()
        if st.kind == "agent_subgraph" and cfg.policies[st.policy].requires_verified_customer
    ]
    verified_target = node.transitions["verified"]
    if verified_target not in protected_agents:
        raise WorkflowValidationError("verified identity must enter a protected agent policy")


def _reject_public_edge_into_protected(cfg: WorkflowV2Config) -> None:
    """A protected agent is only reachable from verify_identity or an explicit already-verified router outcome."""
    states = cfg.states
    protected = {
        name for name, st in states.items()
        if st.kind == "agent_subgraph" and cfg.policies[st.policy].requires_verified_customer
    }
    allowed_sources = {"verify_identity"}
    for name, st in states.items():
        for outcome, target in st.transitions.items():
            if target not in protected:
                continue
            if name in allowed_sources and outcome == "verified":
                continue
            if st.kind == "router" and outcome in {"protected_verified", "mixed_verified"}:
                continue
            raise WorkflowValidationError(
                f"protected state '{target}' is reachable from '{name}' via '{outcome}' without verification"
            )
    for name, st in states.items():
        if st.kind != "router":
            continue
        for outcome in ("protected", "mixed"):
            dest = st.transitions.get(outcome)
            if dest is None:
                raise WorkflowValidationError(f"router '{name}' missing '{outcome}' transition")
            if states[dest].kind != "collect_field":
                raise WorkflowValidationError(f"router '{name}' outcome '{outcome}' must collect identity first")


def _require_reachable_handoff(cfg: WorkflowV2Config) -> None:
    states = cfg.states
    seen: set[str] = set()
    stack = [cfg.initial_state]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(states[cur].transitions.values())
    handoff = next(name for name, st in states.items() if st.kind == "handoff")
    if handoff not in seen:
        raise WorkflowValidationError("handoff state is unreachable")
    if any(st.kind == "response_guardrail" for st in states.values()) and not any(
        st.kind == "response_guardrail" and name in seen for name, st in states.items()
    ):
        raise WorkflowValidationError("response guardrail is unreachable")
