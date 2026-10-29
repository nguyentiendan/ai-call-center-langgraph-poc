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

---

# Hardening addendum (2026-10-29, second pass) — actual evidence

## Changes made (no architecture rewrite)

1. **Reproducible backend install:** `Dockerfile.backend` now does `COPY pyproject.toml uv.lock ./` (lockfile mandatory, no `uv.lock*` glob) and `RUN uv sync --frozen --no-dev`. The previous `|| uv sync --no-dev` fallback was removed. The `uv` binary is pinned (`uv:0.10.12`). Frontend install switched to `npm ci`.
2. **Healthchecks/readiness:** backend (Python urllib `/health`), frontend (`wget /`), and nginx (`wget /health` via proxy) healthchecks in both Dockerfiles and compose; `depends_on: condition: service_healthy` (frontend waits for backend, nginx waits for both). A `GET /readiness` endpoint reports workflow/model/debug state. Observed failure during this work: the frontend healthcheck initially failed (`wget: can't connect to 127.0.0.1`) because Next standalone bound to the container hostname; fixed with `HOSTNAME=0.0.0.0 PORT=3000`, after which all three services reported `(healthy)`.
3. **Conversation authorization:** creation returns a random `conversation_token` once; only its SHA-256 hash is stored server-side. `/messages`, `/verify`, and `/debug` require header `X-Conversation-Token`; missing/invalid returns 401, unknown conversation 404. The Next.js UI stores the token in memory only and sends it on every call.
4. **Debug exposure:** `/debug` returns 404 unless `DEBUG_ENDPOINT_ENABLED=true`. Compose sets it `true` for the local demo explicitly; `.env.example` defaults it to `false`.
5. **Rate limiting:** in-process sliding window (default 120 req/min per client+conversation, `RATE_LIMIT_PER_MINUTE`), returns 429. Verification lockout (3 attempts/conversation) is unchanged and separate.
6. **Secret handling (reviewed, unchanged where already correct):** PIN only via `/verify`, hashed transiently and discarded, never in graph state/checkpoint/trace/logs; `.env` is git-ignored and was confirmed not tracked; no raw request bodies are logged; the OpenAI key is read from env only and never returned by any endpoint (`/health` reports only the model name).

## Test results — reported separately

```text
OFFLINE  $ uv run pytest -q                       -> 35 passed (FakeModel, no key, deterministic)
OFFLINE  $ uv run ruff check .                    -> All checks passed!
LIVE     $ RUN_LIVE_LLM=1 uv run pytest tests_live/ -q -> 5 passed (real OpenAI, gpt-4o-mini)
LIVE     $ uv run pytest tests_live/ -q (no flag) -> 5 skipped (must not be reported as passed)
DOCKER   $ python3 scripts/docker_smoke.py        -> 9/9 PASS, "SMOKE OK" (frontend, health, create+token,
           401 without token, billing -> customer_id -> demo_pin -> verified grounded 8,800/6,600 response,
           PIN not echoed, 409 after close)
DOCKER   $ docker compose ps                      -> backend/frontend/nginx all (healthy)
```

Offline count rose 32 → 35 (new: token-required, debug-disabled-404, rate-limit-429 tests).

## Failures encountered and fixed during the live pass (not hidden)

- First live run: **1 passed, 4 failed**. Root cause: the real model returned `{"category": "faq", ...}` instead of the contracted `{"intent": ...}` key, so strict parsing failed. Fixed in the adapter: the prompt now names the exact JSON keys, and a `category`→`intent` alias is tolerated before strict Pydantic validation. The offline FakeModel was unaffected.
- Second live run: **4 passed, 1 failed**. The grounded billing reply was correct but formatted `8800円` without a thousands comma; the live test now asserts grounded values (comma-normalized) plus `validate_grounded`, instead of exact formatting. Final live run: **5 passed**.
- Live wording remains non-deterministic; live assertions therefore check workflow states and grounded amounts only. This is an evaluation suite, not a determinism guarantee.

## Demo-only protections (not production security)

| Protection | Demo scope | Production gap |
|---|---|---|
| Conversation token | In-memory bearer capability, hashed at rest in process memory | No user identity, OAuth/session, token expiry/rotation, or TLS enforcement in compose |
| Rate limiter | Single-process in-memory window, resets on restart | No distributed limiting (no Redis, per scope), no IP allowlists |
| Debug endpoint | Env-gated, token-required, sanitized fields only | Should stay disabled outside local demos |
| CORS | localhost:3000/8080 origins only | No production origin policy or auth headers |
| Checkpointer/bindings/tickets | In-memory, single backend process | No Postgres/Redis, per scope; lost on restart |
| PIN verification | Salted demo hashes, 3-attempt lockout | Not real KYC; no MFA, no audit log persistence |

## Remaining limitations

- No browser-automated click-through of the Next.js PIN UI was run; the UI path is covered by the Docker HTTP smoke test (same endpoints/payloads) and the frontend production build.
- The live suite depends on an external provider: results above are from one run on 2026-10-29 with `gpt-4o-mini`; future runs may vary in wording and cost.
- `docker compose` state from this session is local only; see README for startup commands.
