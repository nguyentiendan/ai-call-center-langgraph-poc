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
uv run pytest      # 32 tests: T01-T20, YAML validation, YAML-change proof, intent fixture
uv run ruff check .
uv run python scripts/run_demo.py   # sanitized traces for 5 paths
```

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

## Layout

`app/` FastAPI + workflow + services · `config/` YAML workflows/prompts · `data/` synthetic fixtures (salted PIN hashes only) · `frontend/` Next.js 16 · `nginx/` reverse proxy · `tests/` pytest suite · `docs/` decisions, scenarios, transcripts, implementation report.

## Limitations

Demo only: in-memory checkpoint/bindings/tickets (single backend process), fictional data, no live operator, no production auth/CORS hardening, no voice/AWS. See `docs/implementation_report.md`.
