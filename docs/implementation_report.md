# Implementation report (Section 16.5) — actual evidence

Generated 2026-10-29 from real runs in this workspace. Test counts below were observed, not predicted.

## 1. File inventory

| Module | Responsibility |
|---|---|
| [app/workflow/loader.py](/Users/omori/vibe_project/call_center/app/workflow/loader.py) | Strict YAML load (`yaml.safe_load` + Pydantic + allowlists, reachability, security-invariant checks) |
| [app/workflow/compiler.py](/Users/omori/vibe_project/call_center/app/workflow/compiler.py) | YAML → real LangGraph `StateGraph`, `InMemorySaver` checkpointer, YAML-transition conditional edges, fail-closed to handoff |
| [app/workflow/nodes.py](/Users/omori/vibe_project/call_center/app/workflow/nodes.py) | One node per YAML state/action; pause/resume outcomes |
| [app/workflow/state.py](/Users/omori/vibe_project/call_center/app/workflow/state.py) | `CallState` TypedDict (no PIN field exists) |
| [app/services/conversation.py](/Users/omori/vibe_project/call_center/app/services/conversation.py) | API boundary, trusted binding, PIN discard, 404/409 mapping data |
| [app/services/verification.py](/Users/omori/vibe_project/call_center/app/services/verification.py) | Salted-hash demo PIN check, server-side only |
| [app/services/billing.py](/Users/omori/vibe_project/call_center/app/services/billing.py) | Read-only mock billing, trusted-binding check per call, invoice math validation, ≤1 retry |
| [app/services/model.py](/Users/omori/vibe_project/call_center/app/services/model.py) | `FakeModel`, `EvilFakeModel` (guardrail test double), real `OpenAIModel` adapter |
| [app/services/guardrails.py](/Users/omori/vibe_project/call_center/app/services/guardrails.py) | Amount-vs-facts validation, grounded fallback template |
| [app/services/faq.py](/Users/omori/vibe_project/call_center/app/services/faq.py) / [app/services/handoff.py](/Users/omori/vibe_project/call_center/app/services/handoff.py) | Approved FAQ allowlist; idempotent demo handoff queue |
| [app/main.py](/Users/omori/vibe_project/call_center/app/main.py) | FastAPI routes from Section 9 + sanitized `/debug` |
| [frontend/src/app/page.tsx](/Users/omori/vibe_project/call_center/frontend/src/app/page.tsx) | Next.js 16.4.3 Japanese chat UI, dedicated PIN field, sanitized state/trace panel |
| [nginx/nginx.conf](/Users/omori/vibe_project/call_center/nginx/nginx.conf), [docker-compose.yml](/Users/omori/vibe_project/call_center/docker-compose.yml) | nginx: `/`→frontend, `/api/`+`/health`→backend; 3-service compose stack |

Deviation from Section 3/12: Streamlit replaced by Next.js + nginx per explicit user instruction; recorded in [docs/decisions.md](/Users/omori/vibe_project/call_center/docs/decisions.md).

## 2. Workflow traces (from `uv run python scripts/run_demo.py`, actual output)

- Happy path trace: `classify_intent→billing_inquiry, collect_customer_id→awaiting_input, collect_customer_id→supplied, collect_pin→awaiting_input, verify_identity→verified, get_billing→found, explain_bill→completed, close→closed`; final message cites 8,800円 / 6,600円 / 差額2,200円.
- FAQ trace: `classify_intent→faq, answer_faq→completed, close→closed` (no verification/billing).
- Handoff trace: `handoff→created`, ticket e.g. `DEMO-DD453027`, status text states `queued_demo`, no live operator.
- Lockout trace: `verify_identity→retry` ×2 then `verify_identity→locked, handoff→created`; billing never called; further verify returns 409.
- Timeout trace: `get_billing→error, handoff→created`; script printed `billing attempts on timeout path … : 2` (initial + exactly one retry).

Full raw output is reproducible via the command above; see also [docs/example_conversations.md](/Users/omori/vibe_project/call_center/docs/example_conversations.md).

## 3. YAML-change demonstration (Section 16.1.3)

- Exact change: in [config/workflows/billing_alt.yaml](/Users/omori/vibe_project/call_center/config/workflows/billing_alt.yaml), `states.classify_intent.transitions.unknown: clarify → handoff` (and `intents.unknown: clarify → handoff`). No Python file differs between runs.
- Test: `test_yaml_change_proof_unknown_goes_straight_to_handoff` sends the identical input `あれをなんとかして` to two app instances.
- Observed: canonical YAML → `workflow_state=clarify, status=AWAITING_INPUT`; alt YAML → `status=HANDOFF` with a `DEMO-` ticket. Both asserted in the passing suite.

## 4. Test execution log (actual commands/results)

```text
$ uv run pytest -q            -> 32 passed, 14 warnings in 0.93s
$ uv run pytest tests/test_intent_eval.py -q -s -> Intent fixture: 29/30 accuracy=0.967, 1 passed
$ uv run ruff check .         -> All checks passed!
$ docker compose build && docker compose up -d   -> backend, frontend (Next.js 16.4.3 Ready), nginx all Up
$ curl http://localhost:8080/health -> {"status":"ok","workflow_id":"telecom_billing_v1","model":"FakeModel"}
$ curl http://localhost:8080/       -> HTTP 200 (frontend through nginx)
$ POST via nginx: create -> billing msg (collect_customer_id) -> CUST-1001 (collect_pin) -> verify 1234
  -> CLOSED with 8,800円/6,600円/2,200円 and the 8-step trace above
```

Warnings are upstream deprecation notices (langgraph/langsmith), not failures. Coverage: T01–T20 all automated — mapping in [docs/test_scenarios.md](/Users/omori/vibe_project/call_center/docs/test_scenarios.md). No test is skipped or xfailed.

## 5. Safety proof

- Billing without verification: `test_t14_direct_billing_without_verification_denied` calls the billing service directly and asserts `BillingAccessDenied`; T05/T06/T07 additionally assert `billing.calls == 0` after lockout/injection/operator-switch.
- PIN confidentiality: `test_t18_pin_absent_from_checkpoint_transcript_logs` inspects the real LangGraph checkpoint values and the `/debug` payload and asserts the PIN string is absent; `CallState` has no PIN field by construction.
- Thread isolation: `test_t15_interleaved_conversations_isolated` interleaves two conversations and asserts independent state/traces.
- Grounding: `test_t11_…` uses `EvilFakeModel` inventing 99,999円; the reply is replaced by the grounded template.
- Handoff idempotency: single ticket per conversation (service key `handoff:{conversation_id}`; T16).

## 6. Known limitations

- In-memory checkpointer/bindings/tickets: single backend process, lost on restart. No Postgres/Redis (Phase 5, out of scope).
- `FakeModel` is keyword-based; the 0.967 fixture score describes that fixture only, not production intent quality.
- Real LLM path (`OpenAIModel`) is implemented but was not live-tested — no API key was available in this environment; it is gated by `MODEL_PROVIDER=openai` + key and `/health` reports `FakeModel` honestly in demo mode.
- No voice/STT/TTS/CTI/AWS, no real operator, no production auth — demo queue and synthetic data only. CORS is localhost-scoped.
- Local `uv` needed `UV_CACHE_DIR=/tmp/uv-cache` in this sandbox (home cache not writable); Docker runs do not.

## 7. Demo script

See README「Demo script」: start via `docker compose up --build`, open http://localhost:8080, send `今月の携帯料金が先月より高いのですが。`, then `CUST-1001`, then PIN `1234` in the dedicated field only.

## Phase gates

- Gate A: YAML loader + 8 negative validation tests pass; real `StateGraph` compiles at startup (invalid YAML fails app creation).
- Gate B: all multi-turn routes pass with `FakeModel`, including mid-flow operator switch and the YAML-change proof.
- Gate C: billing access control, PIN confidentiality, lockout, isolation, API errors (400/404/409/422), handoff idempotency — all in the green suite.
- Gate D: API + Next.js UI run through nginx (verified by curl above), real LLM gated by credentials, tests + ruff green, this report complete.
