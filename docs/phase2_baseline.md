# Phase 2 baseline (M0)

Recorded before Phase 2 code changes. Source of truth is the repository, not guessed paths.

## Git

- Branch created: `feature/phase2-hybrid-agent` (from `main`)
- Baseline commit: `53b3cef309e2118c78ce05a97b79cf421e8dd478`
- Subject: `update code and add tests`
- Working tree at branch creation: untracked `AI_Call_Center_Phase2_Hybrid_Agent_Design.md` only. No user source files were modified. No reset, no force-push.
- Rollback: Phase 1 stays the default via `WORKFLOW_PATH` (unset means `config/workflows/billing_v1.yaml`).

## Phase 1 behavior that must stay

Deterministic YAML workflow `telecom_billing_v1` (`schema_version: 1`). LangGraph `StateGraph` in `app/workflow/compiler.py` (`build_graph`) executes states declared in `config/workflows/billing_v1.yaml`. The model only classifies intent and phrases a billing explanation. Identity, billing reads, and handoff stay in Python services. PIN is accepted only by `POST /api/v1/conversations/{id}/verify`.

## Module map

| Area | Path | Role |
|---|---|---|
| App factory | `app/main.py` `create_app` | FastAPI routes, rate limit, conversation token, `/health`, `/readiness`, `/debug` |
| Schemas | `app/api/schemas.py` | `MessageIn`, `VerifyIn`, `ConversationOut` |
| Config | `app/core/config.py` | `ROOT`, `get_workflow_path`, `get_model_provider` |
| Workflow load | `app/workflow/loader.py` `load_workflow` | `yaml.safe_load`, Pydantic, schema_version 1 only |
| Graph | `app/workflow/compiler.py` `build_graph`, `Deps` | `InMemorySaver`, conditional edges from YAML transitions |
| Nodes | `app/workflow/nodes.py` `make_node` | One function per YAML `action` |
| State | `app/workflow/state.py` `CallState` | Checkpointed conversation state |
| Conversation | `app/services/conversation.py` `ConversationService` | Create/message/verify, token hash, trusted binding before verify success |
| Model | `app/services/model.py` | `FakeModel`, `OpenAIModel`, `build_model`, `is_operator_request` |
| Prompts | `app/services/prompts.py` | `load_prompt`, intent and billing templates |
| Billing | `app/services/billing.py` | `TrustedBindings`, `BillingService.get_billing` |
| FAQ | `app/services/faq.py` | Keyword FAQ for Phase 1 `answer_approved_faq` |
| Handoff | `app/services/handoff.py` | Idempotent demo ticket |
| Verification | `app/services/verification.py` | Salted PIN check |
| Guardrails | `app/services/guardrails.py` | Yen amounts must match billing facts |
| Workflow YAML | `config/workflows/billing_v1.yaml`, `billing_alt.yaml` | Phase 1 graphs |
| Prompts YAML | `config/prompts/intent.yaml`, `explain_billing.yaml`, `verify_identity.yaml` | Phase 1 prompts |
| Fixtures | `data/demo_customers.json`, `data/demo_invoices.json`, `data/approved_faq.yaml` | Synthetic only |
| UI | `frontend/src/app/page.tsx` | Next.js chat, dedicated PIN field, sanitized debug panel |
| Proxy | `nginx/nginx.conf`, `docker-compose.yml` | `:8080` to Next.js and FastAPI |
| Tests | `tests/test_scenarios.py`, `test_yaml_validation.py`, `test_intent_eval.py`, `test_prompts.py` | Offline Phase 1 |
| Live | `tests_live/test_openai_live.py` | Gated by `RUN_LIVE_LLM=1` |
| Decisions | `docs/decisions.md` | Phase 1 engineering choices |

## API preserved

- `POST /api/v1/conversations`
- `POST /api/v1/conversations/{id}/messages` header `X-Conversation-Token`
- `POST /api/v1/conversations/{id}/verify` body `customer_id`, `demo_pin`
- `GET /api/v1/conversations/{id}/debug` only when `DEBUG_ENDPOINT_ENABLED=true`
- `GET /health`, `GET /readiness`

Verify succeeds only when `workflow_state == collect_pin` and `required_input == demo_pin`, and `customer_id` matches `pending_customer_id` (`CustomerMismatch` -> HTTP 400). Phase 2 identity states keep those names so this check stays.

## Baseline commands

Environment: Python 3.14.3 (local `.venv`), `uv`  available. `LANGSMITH_TRACING=false` and `LANGCHAIN_TRACING_V2=false` were set for the clean rerun. Without that, pytest reported 40 passed and then blocked on LangSmith export.

| Command | Result |
|---|---|
| `LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false uv run pytest -q --tb=line` | **40 passed**, exit 0 |
| `uv run ruff check .` | **All checks passed**, exit 0 |
| `cd frontend && npm run build` | **exit 0** (Next.js 16.4.3, compiled successfully) |
| `python3 scripts/docker_smoke.py` | **NOT RUN** at M0 (stack was not started; no containers were changed) |

Docker smoke is deferred to M4 so this milestone does not mutate images or volumes.

## Files Phase 2 is expected to add or touch

Add:

- `config/workflows/support_agent_v2.yaml`
- `config/prompts/agent_planner_v2.yaml`, `grounded_answer_v2.yaml`, `clarify_v2.yaml`
- `data/plan_catalog.yaml`, `data/customer_plans.json`, `data/support_faq.yaml`
- `app/workflow/loader_v2.py`, `app/workflow/compiler_v2.py`, `app/workflow/agent_graph.py`
- `app/services/policy.py`, `app/services/tools_v2.py`, `app/services/grounding_v2.py`
- `tests/test_phase2_yaml.py`, `tests/test_phase2_agent.py`, `tests/test_phase2_api.py`
- `frontend/e2e/phase2.spec.ts`, `frontend/playwright.phase2.config.ts`
- `docs/phase2_architecture.md`, `docs/phase2_implementation_report.md`

Touch, without changing Phase 1 results when `schema_version` is 1:

- `app/workflow/loader.py` (dispatch version 2)
- `app/workflow/state.py` (optional keys)
- `app/workflow/compiler.py` (`Deps` optional fields only)
- `app/services/model.py` (scope + planner methods; `classify` unchanged)
- `app/services/conversation.py` (v2 graph, optional response fields, PIN redaction on v2)
- `app/main.py` (trace route, readiness flag, v2 services)
- `frontend/src/app/page.tsx`, `globals.css`
- `README.md`, `docker-compose.yml` (new env vars, default workflow unchanged)
- `tests/conftest.py` (disable LangSmith export during tests)

Phase 1 tests are not deleted or weakened.
