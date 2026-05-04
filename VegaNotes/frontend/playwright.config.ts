import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright config for VegaNotes editor regression suites.
 *
 * Defaults assume the dev stack is already up:
 *   * backend uvicorn on :8000
 *   * vite dev server on :5173 (proxies /api → :8000)
 *
 * Auth: HTTP Basic.  Override via env vars when the live admin password
 * has been rotated:
 *   VEGA_BASE_URL=http://localhost:5173
 *   VEGA_USER=admin
 *   VEGA_PASS=admin
 *
 * The suite seeds its own fixture notes under a dedicated project so it
 * never collides with the user's real notes.
 */
const baseURL = process.env.VEGA_BASE_URL ?? "http://localhost:5173";
const user = process.env.VEGA_USER ?? "admin";
const pass = process.env.VEGA_PASS ?? "admin";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,
  reporter: [["list"]],
  use: {
    baseURL,
    httpCredentials: { username: user, password: pass },
    trace: "retain-on-failure",
    viewport: { width: 1280, height: 800 },
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
});
