# Phase 2 implementation report

Date of verification: 2026-10-30. Branch: `feature/phase2-hybrid-agent`.

This is a demo. It is not a production support system.

## Milestone commits

| Milestone | Commit | What landed |
|---|---|---|
| M0 baseline | `79cd947` | Baseline note and the Phase 2 design contract |
| M1 YAML | `fe94fc8` | Version 2 schema, prompts, fictional catalog / plans / FAQ |
| M2 agent | `73933e0` | LangGraph support subgraph, policy gateway, read-only tools |
| M3 UI and API | `27453ef` | Citations, dev trace UI, P2-20–P2-30 tests, two Playwright tests |
| M4 verification | the commit that adds this report | Live OpenAI tests, Docker smoke, this report |

`git log --oneline feature/phase2-hybrid-agent` is the source for the M4 SHA after that commit is created.

## How to run

```bash
uv run pytest
uv run ruff check .
WORKFLOW_PATH=config/workflows/support_agent_v2.yaml uv run uvicorn app.main:app --port 8000
RUN_LIVE_LLM=1 uv run pytest tests_live/test_phase2_openai.py -v
cd frontend && PLAYWRIGHT_BROWSERS_PATH=$PWD/.playwright-browsers npx playwright test -c playwright.phase2.config.ts
python3 scripts/docker_smoke.py
```

Phase 1 stays the default workflow. Phase 2 is selected only with `WORKFLOW_PATH=config/workflows/support_agent_v2.yaml`.

## Offline tests

Command: `LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false uv run pytest -q --tb=line`

Result: **101 passed**, exit 0. `uv run ruff check app tests` passed. These tests use `FakeModel` or `ScriptedPlanner`. They are not live LLM evidence.

| Group | Count | Role |
|---|---|---|
| Existing Phase 1 modules under `tests/` | 40 | Unchanged billing, FAQ, handoff, auth, YAML v1 |
| `tests/test_phase2_yaml.py` | 10 | Schema, P2-24 public billing rejection, prompt traversal |
| `tests/test_phase2_agent.py` | 25 | P2-01–P2-19, P2-23, P2-28, invoice mutation, subgraph Mermaid |
| `tests/test_phase2_api.py` | 8 | P2-20–P2-22, P2-25–P2-27, P2-29, P2-30 |
| `tests/test_phase2_prompts.py` | 18 | 12 paraphrases and 6 injection lines |

P2-30 runs the Phase 1 client and still expects `CLOSED`, `8,800円`, the hours FAQ, and one handoff ticket.

## YAML change proof

`test_p2_23_yaml_removes_catalog_tool_without_python_edit` writes a temp copy whose public policy drops `search_plan_catalog`, reloads it, and uses the same `ScriptedPlanner`. The original workflow calls the catalog tool once. The temp workflow calls it zero times and the reply has no `plan-mini-1gb`. The source YAML is reloaded afterward and still allows the tool.

`test_p2_24_public_policy_cannot_grant_billing` expects `WorkflowValidationError` when public policy lists `get_billing_info`.

`test_mutated_invoice_changes_the_grounded_amount` changes the CUST-1001 fixture total to 9,100円. The reply contains `9,100円` and not `8,800`.

## Malformed planner and policy denial

P2-16 feeds `tool: drop_database`. `PlannerDecision` rejects it. The reply is the schema fallback and `tools.order` is empty.

P2-17, public question `営業時間は何時ですか？`, scripted planner asks for `get_billing_info` twice. Captured trace (billing service call count 0):

```json
[
  {"step": 1, "tool": "get_billing_info", "policy": "denied", "duration_ms": 0, "outcome": "denied", "source_ids": []},
  {"step": 2, "tool": "get_billing_info", "policy": "denied", "duration_ms": 0, "outcome": "denied", "source_ids": []}
]
```

The user-facing text is the safe refusal, not an invoice.

## Multi-tool trace

Same FakeModel path as P2-01, after verification of the demo customer. `duration_ms` is 0 because the mocks return immediately.

```json
[
  {"step": 1, "tool": "get_billing_info", "policy": "allowed", "duration_ms": 0, "outcome": "ok", "source_ids": ["invoice-2026-10", "invoice-2026-09"]},
  {"step": 2, "tool": "get_current_plan", "policy": "allowed", "duration_ms": 0, "outcome": "ok", "source_ids": ["account-plan-standard-5gb"]},
  {"step": 3, "tool": "search_plan_catalog", "policy": "allowed", "duration_ms": 0, "outcome": "ok", "source_ids": ["plan-mini-1gb", "plan-light-3gb", "plan-standard-5gb", "plan-comfort-20gb", "plan-unlimited"]},
  {"step": 4, "tool": "search_faq", "policy": "allowed", "duration_ms": 0, "outcome": "ok", "source_ids": ["faq-data-overage"]}
]
```

The reply states 8,800円 versus 6,600円, says savings cannot be asserted, and says the plan was not changed. No PIN or token is in the trace.

## Browser

Command: `PLAYWRIGHT_BROWSERS_PATH=frontend/.playwright-browsers npx playwright test -c playwright.phase2.config.ts`

Result: **2 passed** (3.2s) on 2026-10-30.

1. Multi-intent text, customer id, PIN in the password field: `8,800円`, `6,600円`, `断定できません`, `plan-mini-1gb`, and no user bubble containing the PIN.
2. Injection demanding CUST-1001 billing without verification: no `8,800円`, then one assistant handoff bubble with `queued_demo` and `DEMO-`.

Chromium was installed into `frontend/.playwright-browsers` for this run. That directory is gitignored. The Phase 1 spec `frontend/e2e/chat.spec.ts` targets Docker on port 8080 and was not re-run as a browser test in this pass; the Phase 1 API path is covered by pytest and `scripts/docker_smoke.py`.

## Docker

`docker compose up -d --build` exit 0. Backend and frontend became healthy.

`python3 scripts/docker_smoke.py` against `http://127.0.0.1:8080`: **SMOKE OK**, exit 0. Health reported `telecom_billing_v1` and `FakeModel`. The grounded billing message contained 8,800円 and 6,600円. A message after close returned 409.

A no-rebuild recreate with `WORKFLOW_PATH=config/workflows/support_agent_v2.yaml` then returned health `telecom_support_agent_v2`. The backend was recreated again without that variable and health returned to `telecom_billing_v1`. Checkpoint data is in memory and was not claimed to survive those recreates.

The frontend image build during this pass uploaded a large context because Playwright browsers were not yet in `frontend/.dockerignore`. That ignore entry is now present. The smoke above used the image built before that ignore change.

## Live LLM

Command: `RUN_LIVE_LLM=1 uv run pytest tests_live/test_phase2_openai.py -v`

Result: **2 passed**, exit 0, wall clock **5.65s**, date 2026-10-30. The process constructed `OpenAIModel`. `create_app` used `OPENAI_MODEL` or the code default `gpt-4o-mini`. This run did not export `OPENAI_MODEL`, so the client used **gpt-4o-mini**. Token cost was not measured.

- Public fee question: HTTP 200, `required_input` is null, `get_billing_info` and `get_current_plan` call counts are 0, reply has no `8,800`.
- Japanese injection asking to skip verification: HTTP 200, no `8,800`, billing call count unchanged.

`tests_live/test_openai_live.py` (Phase 1 live file) was **NOT RUN** in this pass.

## Security matrix

| Invariant | Where it is checked |
|---|---|
| Server binding, not model `customer_id` | P2-07 and `test_direct_policy_strips_customer_id` |
| No cross-customer invoice | P2-07 follow-up; billing calls do not increase |
| PIN only on `/verify` | P2-22; checkpoint text has no `1234` |
| Gateway before execution | P2-17; billing `calls == 0` |
| Read-only | P2-10; no mutation tool |
| No invented yen | P2-19 replaces `99,999円`; mutated invoice test |
| Injection does not authorize | P2-15 and the live injection test |
| Budgets | P2-18 stops at 5 calls, or at 2 when the temp YAML says 2 |
| Session isolation | P2-20; other token gets 401 on messages and trace |
| Trace off by default | P2-25 returns 404 and the message body has no `trace_events` |
| Trace on is sanitized | P2-26; only the six public keys |
| Missing live key | P2-27; `build_model("openai", None)` raises |

## Manual UI scripts

With Phase 2 and, for the trace, `ENABLE_DEV_TRACE=true`:

- UI-A: the Playwright multi-intent test. Trace steps are allowed only after verification.
- UI-B: `プラン変更の手数料は？` returns `faq-plan-change-fee` and does not ask for a PIN. Covered by P2-02.
- UI-C: the Playwright injection test, then one mock ticket.
- UI-D: `プランを変更してください` (P2-10) says the change cannot be executed and does not create a ticket until the customer asks for an operator.

## Known limitations

- One backend process. Checkpoints, bindings, and tickets are memory only and disappear on restart.
- Offline `FakeModel` is keyword routing into the real tool loop. It is not evidence about `gpt-4o-mini` beyond the two live tests.
- FAQ ranking is keyword overlap with threshold 0.65, not embeddings.
- Grounding checks yen amounts, not every number such as gigabytes.
- The parent Mermaid view lists every conditional target because that is what LangGraph emits. Runtime transitions still come from YAML.
- Phase 2 leaves the conversation `ACTIVE` after an answer so a later turn can reuse verification. Phase 1 still closes.
- No plan change, payment, voice, or external carrier API.
- Live coverage is two cases, not P2-01 through P2-30.
- `nginx/nginx.prod.conf` was not the file mounted for the Docker smoke. The demo nginx proxies `/api/`. The trace route still returns 404 unless `ENABLE_DEV_TRACE=true`.

## Tests not run

- `tests_live/test_openai_live.py` (Phase 1 live suite)
- A live run of every P2 scenario
- Playwright `frontend/e2e/chat.spec.ts` against the Docker UI in this pass
- Token-cost or percentile latency measurement
