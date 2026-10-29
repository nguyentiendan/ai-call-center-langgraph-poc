import { defineConfig } from "@playwright/test";

// E2E runs against the Docker stack (nginx at :8080) by default.
// Start it first: docker compose up -d --build
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  retries: 0,
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:8080", locale: "ja-JP" },
});
