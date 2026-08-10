import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT ?? 3100);
// Use `localhost`, NOT 127.0.0.1 — Next dev normalizes redirect hosts to
// `localhost`, and the mock-signin POST 303-redirects. If the page origin is
// 127.0.0.1 but the redirect target is localhost, the browser treats them as
// different origins: `form-action 'self'` blocks the submission and the session
// cookie (set on 127.0.0.1) never reaches the localhost redirect → login bounces.
const BASE_URL = `http://localhost:${PORT}`;

// The real-api project runs on a separate port to avoid conflicting with the
// mock-mode server that may still be running in watch mode.
const REAL_API_PORT = Number(process.env.E2E_REAL_API_PORT ?? 3101);
const REAL_API_BASE_URL = `http://localhost:${REAL_API_PORT}`;

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI
    ? [["github"], ["html", { open: "never" }]]
    : [["list"], ["html", { open: "never" }]],

  use: {
    baseURL: BASE_URL,
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },

  projects: [
    // ── Fast mock project (default / every PR) ──────────────────────────
    // Runs with MSW mocks + streams disabled — self-contained, no backend needed.
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
      // Only run specs that don't require the real API.
      testIgnore: ["**/stream.spec.ts", "**/components-parity.spec.ts"],
    },

    // ── Real-API project (REQ-M4-02 parity gate) ────────────────────────
    // Requires:
    //   1. docker compose up -d postgres redis
    //   2. cd agentic_app && uvicorn process_api:app --port 8001
    //   3. python agentic_app/scripts/seed_e2e_fixtures.py
    // Then: pnpm e2e --project=real-api
    //
    // CRITICAL: NEXT_PUBLIC_DISABLE_STREAMS is "0" (NOT inherited from mock project's "1")
    // so StreamSubscriber opens the SSE bridge — this is the open question 3 fix.
    {
      name: "real-api",
      use: {
        ...devices["Desktop Chrome"],
        baseURL: REAL_API_BASE_URL,
      },
      testMatch: ["**/stream.spec.ts", "**/components-parity.spec.ts"],
    },
  ],

  // Auto-boot the dev server with MSW mock mode so tests are self-contained.
  webServer: [
    // ── Mock server (chromium project) ───────────────────────────────────
    {
      command: `next dev --turbo --port ${PORT}`,
      url: BASE_URL,
      reuseExistingServer: !process.env.CI,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_AUTH_MODE: "mock",
        NEXT_PUBLIC_MOCK_LATENCY_MS: "0",
        // Disabled so the test doesn't chase the workspace heartbeat
        NEXT_PUBLIC_DISABLE_STREAMS: "1",
      },
    },
    // ── Real-API server (real-api project) ───────────────────────────────
    // Streams ENABLED + mocks OFF — exercises the WS→SSE bridge end-to-end.
    // NEXT_PUBLIC_DISABLE_STREAMS must be "0" here (or absent).
    // JWT_SECRET_KEY must match the running FastAPI instance.
    {
      command: `next dev --turbo --port ${REAL_API_PORT}`,
      url: REAL_API_BASE_URL,
      reuseExistingServer: true,
      timeout: 120_000,
      stdout: "pipe",
      stderr: "pipe",
      env: {
        NEXT_PUBLIC_AUTH_MODE: "mock",
        NEXT_PUBLIC_API_MOCKS: "off",
        NEXT_PUBLIC_DISABLE_STREAMS: "0",
        NEXT_PUBLIC_MOCK_LATENCY_MS: "0",
        FASTAPI_INTERNAL_URL: process.env.FASTAPI_INTERNAL_URL ?? "http://localhost:8001",
        // JWT_SECRET_KEY is intentionally NOT set here — Next dev loads it from
        // apps/web/.env.local (which matches the FastAPI .env). Hard-coding a
        // "change-me-in-production" fallback would mint tokens FastAPI rejects (401).
      },
    },
  ],
});
