# AI Call Center PoC — LangGraph + YAML (text-first)

Deterministic YAML-driven telecom billing support in Japanese. LangGraph `StateGraph` executes transitions declared in `config/workflows/billing_v1.yaml`; the LLM (or deterministic `FakeModel` offline) only classifies intent and phrases grounded answers. No voice, AWS, real payments, or real customer data.

## Run with Docker (recommended)

```bash
docker compose up --build
# Open http://localhost:8080 in your browser (nginx -> Next.js frontend, /api -> FastAPI backend)
curl http://localhost:8080/health
```

## Run locally without Docker

Requires Python 3.12+ and Node 22+.

```bash
uv sync
UV_CACHE_DIR=/tmp/uv-cache uv run uvicorn app.main:app --port 8000   # backend (omit UV_CACHE_DIR if your cache is writable)
cd frontend && npm install && npm run dev                            # frontend on http://localhost:3000
```

## Tests and lint (offline, no API key needed)

```bash
uv run pytest      # OFFLINE: 35 tests (FakeModel): T01-T20, YAML validation, YAML-change proof, intent fixture, token/rate-limit
uv run ruff check .
uv run python scripts/run_demo.py   # sanitized traces for 5 paths
RUN_LIVE_LLM=1 uv run pytest tests_live/ -v   # LIVE (separate): real OpenAI, needs OPENAI_API_KEY; skips otherwise
python3 scripts/docker_smoke.py                # DOCKER SMOKE: needs `docker compose up -d --build` first
```

Offline and live results are reported separately in docs/implementation_report.md; live tests never run by default.

## Demo script

1. Start the app, click 「新しい問い合わせ」.
2. Send `今月の携帯料金が先月より高いのですが。`
3. Send `CUST-1001` as the customer ID.
4. In the dedicated PIN field only, enter `1234` and click 確認する. (Demo fixtures: `CUST-1001/1234`, `CUST-1002/5678`, `CUST-1003/1234` has no invoice.)
5. The reply shows 2026-10: 8,800円 vs 2026-09: 6,600円 (delta 2,200円, data overage).
6. Try also: `営業時間は何時ですか？` (FAQ), `オペレーターに代わってください` (handoff), a wrong PIN three times (lockout).

## Optional real LLM

```bash
MODEL_PROVIDER=openai OPENAI_API_KEY=... OPENAI_MODEL=gpt-4o-mini docker compose up --build
```

Without a key the app honestly runs `FakeModel` (see `GET /health`). Structured classifier output is validated; low confidence (<0.75) routes to clarification. Generated amounts are re-validated against billing facts.

## Phase 2 hybrid agent

Phase 1 remains the default. Phase 2 is `config/workflows/support_agent_v2.yaml`. Roll back by unsetting `WORKFLOW_PATH` or pointing it at `config/workflows/billing_v1.yaml`.

```bash
WORKFLOW_PATH=config/workflows/support_agent_v2.yaml ENABLE_DEV_TRACE=true docker compose up --build
```

`ENABLE_DEV_TRACE` defaults to false. When it is true, `GET /api/v1/conversations/{id}/trace` returns only step, tool, policy, duration, outcome, and source ids, and still requires the conversation token. `nginx/nginx.prod.conf` does not publish that route.

Offline tests use `FakeModel`, which chooses tools through the same LangGraph loop as OpenAI. It is not a live-model result. Live tool-calling is separate:

```bash
RUN_LIVE_LLM=1 uv run pytest tests_live/test_phase2_openai.py -v
```

Demo prompts: `今月の料金が高くなった理由と安いプランを教えて` (verify with `CUST-1001` / PIN `1234` in the dedicated field), `プラン変更の手数料は？` (no verification), `オペレーターに代わってください` (one mock ticket). A failed tool ends in a safe sentence or handoff; the chat does not invent an amount. Details, traces, and limits are in `docs/phase2_implementation_report.md`.

## Layout

`app/` FastAPI + workflow + services · `config/` YAML workflows/prompts · `data/` synthetic fixtures (salted PIN hashes only) · `frontend/` Next.js 16 · `nginx/` reverse proxy · `tests/` pytest suite · `docs/` decisions, scenarios, transcripts, implementation report.

## Limitations

Demo only: in-memory checkpoint/bindings/tickets (single backend process), fictional data, no live operator, no production auth/CORS hardening, no voice/AWS. See `docs/implementation_report.md`.
