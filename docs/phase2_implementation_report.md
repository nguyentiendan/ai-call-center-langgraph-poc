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
| M4 verification | `dc46482` | Live OpenAI tests, Docker smoke, this report |
| Hardening | `c65eea6` `fe6af16` `1d11e2e` `02569e4` `7dd9efd` | Grounding, one handoff ticket, tool deadline, lexical retrieval, scope prompt, live multi-intent |
| Cleanup | `e254cab` | `selection_origin` on traces, mixed-plan grounding, timeout limitation |

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

Result on 2026-10-30 after the cleanup: **113 passed**, exit 0. `uv run ruff check app tests` passed. These tests use `FakeModel` or `ScriptedPlanner`. They are not live LLM evidence.

| Group | Count | Role |
|---|---|---|
| Existing Phase 1 modules under `tests/` | 41 | Billing, FAQ, handoff, auth, YAML v1, plus the scope-prompt file check |
| `tests/test_phase2_yaml.py` | 10 | Schema, P2-24 public billing rejection, prompt traversal |
| `tests/test_phase2_agent.py` | 25 | P2-01–P2-19, P2-23, P2-28, invoice mutation, subgraph Mermaid |
| `tests/test_phase2_api.py` | 8 | P2-20–P2-22, P2-25–P2-27, P2-29, P2-30 |
| `tests/test_phase2_prompts.py` | 18 | 12 paraphrases and 6 injection lines |
| `tests/test_phase2_hardening.py` | 11 | Plan-claim grounding, mixed plan attributes, one handoff ticket, tool deadline, paraphrases, trace origins, four negative security cases |

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

Result on 2026-10-30: **3 passed**, exit 0, wall clock **16.49s**. The client was `OpenAIModel` on **gpt-4o-mini** (`OPENAI_MODEL` was unset, so the code default was used). Token cost was not measured.

- Public fee question: HTTP 200, `required_input` is null, `get_billing_info` and `get_current_plan` call counts are 0, reply has no `8,800`.
- Verified multi-intent: customer id, PIN on `/verify`, then `get_billing_info`, `get_current_plan`, and `search_plan_catalog` each ran at least once. Workflow state was `respond` and status stayed `ACTIVE`. Sources included `invoice-2026-10`, `account-plan-standard-5gb`, and `plan-mini-1gb`. The reply contained 8800円. Before verification those protected call counts were 0.
- Japanese injection asking to skip verification: HTTP 200, no `8,800`, billing call count unchanged.

The live planner is still the model. If it returns a schema the server rejects, it is asked once more and the bad JSON is not executed. If it repeats a finished read or tries to answer before billing, the current plan, or the catalog, it is asked again and, on the last attempt, the missing read is selected. An empty FAQ or catalog `query` is filled from the customer text before the policy schema runs. Unknown tools are still rejected.

## Cleanup on 2026-10-30

PR #1 head before this cleanup was `783eadcf8fd4cb71060055e3b4f8de821f7dfd1d` (`783eadc`). That SHA already contained every hardening commit from `c65eea6` through `783eadc`. The cleanup implementation is `e254cab`. It is local until pushed; PR #1 stays at `783eadc` until then.

| Check | Result |
|---|---|
| Offline | `uv run pytest -q --tb=line` — 113 passed, exit 0 |
| Ruff | `uv run ruff check app tests` — passed, exit 0 |
| Live OpenAI | `RUN_LIVE_LLM=1 uv run pytest tests_live/test_phase2_openai.py -v` — 3 passed, exit 0, 15.73s, gpt-4o-mini |
| Docker | backend image rebuilt, then `python3 scripts/docker_smoke.py` — SMOKE OK, exit 0, `telecom_billing_v1` / `FakeModel`. An earlier attempt during container start returned 502 and is not a pass. |
| Playwright | `playwright.phase2.config.ts` — 2 passed, exit 0 |

`selection_origin` is one of `llm`, `workflow`, or `recovery`. The public trace still omits arguments, prompts, PIN, and customer id.

## Hardening evidence

| Check | Command and result |
|---|---|
| Offline regression | `uv run pytest -q --tb=line` — 110 passed, exit 0 |
| Ruff | `uv run ruff check app tests` — passed, exit 0 |
| Live OpenAI | command above — 3 passed, exit 0, 16.49s, gpt-4o-mini |
| Docker smoke | `docker compose up -d --build` exit 0, then `python3 scripts/docker_smoke.py` — SMOKE OK, exit 0, health `telecom_billing_v1` / `FakeModel` |
| Playwright | `PLAYWRIGHT_BROWSERS_PATH=frontend/.playwright-browsers npx playwright test -c playwright.phase2.config.ts` — 2 passed, exit 0 |

`request_handoff` and `human_handoff` share `handoff:{conversation_id}`. The tool call ends the agent turn, the handoff node sets `HANDOFF`, and a later message is HTTP 409. `HandoffService.created` stays 1. `timeout_seconds_per_tool` now bounds `future.result`. The worker thread is not killed. A 1 second plan lookup with a 0.05 second YAML deadline returns the timeout sentence in under 0.8 seconds and retries once. FAQ and catalog paraphrases stay lexical: one curated keyword hit, or the longest title fragment, with no vector index.

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
| Trace on is sanitized | P2-26; seven public keys, including `selection_origin` |
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
- Live coverage is three cases, not P2-01 through P2-30.
- `timeout_seconds_per_tool` does not cancel a running thread. `ThreadPoolExecutor.shutdown(cancel_futures=True)` only drops futures that have not started. The caller raises `ToolTimeout` and stops waiting. Cancellation is not implemented.
- The live planner can be nudged onto the next missing read tool. That nudge is not a substitute for the policy gateway. A tool the model named stays `selection_origin=llm` even if an empty FAQ or catalog query is filled. Substituting a missing workflow read is `workflow`. Substituting after the model repeats a finished tool is `recovery`. Offline `FakeModel` tool choices are `workflow`. The model cannot set `selection_origin` itself.
- Plan prices and allowances are checked against the plan named in that span. A sentence can still mention an invoice total next to a plan name; those invoice amounts are not plan attributes. Attribute pairing does not cover every Japanese paraphrase boundary.
- `nginx/nginx.prod.conf` was not the file mounted for the Docker smoke. The demo nginx proxies `/api/`. The trace route still returns 404 unless `ENABLE_DEV_TRACE=true`.

## Tests not run

- `tests_live/test_openai_live.py` (Phase 1 live suite)
- A live run of every P2 scenario
- Playwright `frontend/e2e/chat.spec.ts` against the Docker UI in this pass
- Token-cost or percentile latency measurement
