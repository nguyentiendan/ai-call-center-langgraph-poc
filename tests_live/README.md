# Live LLM tests (separate from offline)

Offline suite (default): `uv run pytest` → `tests/` with `FakeModel`, no key, deterministic.
Live suite (this directory): real OpenAI calls, non-deterministic wording, costs money.

```bash
RUN_LIVE_LLM=1 uv run pytest tests_live/ -v
```

Requires `OPENAI_API_KEY` (environment or project `.env`, which is git-ignored).
Without `RUN_LIVE_LLM=1` + key, tests skip and must not be reported as passed.
Report live results separately from offline results (see docs/implementation_report.md).
