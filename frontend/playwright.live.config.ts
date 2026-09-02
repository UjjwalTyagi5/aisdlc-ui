/**
 * Playwright against the ALREADY-RUNNING real-backend dev server.
 *
 * The main config owns ports 3100/3101 and boots its own `next dev` with
 * NEXT_PUBLIC_AUTH_MODE=mock. This one starts nothing and points at whatever is
 * already serving :3000 from .env.local (API mocks off, local auth), so the specs
 * sign in as the real seeded personas and every request reaches FastAPI.
 *
 * Prerequisites, none of which this config arranges:
 *   backend:   PYTHONPATH=. uvicorn process_api:app --port 8001
 *   personas:  python -m scripts.seed_dev_personas
 *   frontend:  npm run dev            (port 3000, .env.local present)
 *
 * Run:  npx playwright test -c playwright.live.config.ts
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testMatch: ["**/bu-admin-onboarding-and-projects-table.spec.ts"],
  // Serial: the specs sign in as different people against ONE backend, and the
  // onboarding test writes a real role_binding. Parallel workers racing the same
  // database is how a passing suite starts failing on Tuesdays.
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  use: {
    baseURL: process.env.LIVE_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    ...devices["Desktop Chrome"],
  },
});
