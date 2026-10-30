import { defineConfig } from "@playwright/test";

// Phase 2 UI against a local FastAPI workflow, separate from the Phase 1 Docker spec.
export default defineConfig({
  testDir: "./e2e",
  testMatch: "phase2.spec.ts",
  timeout: 60_000,
  retries: 0,
  use: { baseURL: "http://127.0.0.1:3010", locale: "ja-JP" },
  webServer: [
    {
      command: "cd .. && LANGSMITH_TRACING=false LANGCHAIN_TRACING_V2=false WORKFLOW_PATH=config/workflows/support_agent_v2.yaml ENABLE_DEV_TRACE=true uv run uvicorn app.main:app --port 8010",
      url: "http://127.0.0.1:8010/health",
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "API_PROXY_TARGET=http://127.0.0.1:8010 npx next dev -p 3010",
      url: "http://127.0.0.1:3010",
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});
