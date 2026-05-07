import { defineConfig } from "@playwright/test";

/**
 * Playwright config for gsfluent E2E.
 *
 * The webServer config tries to spin up the FastAPI backend AND the Vite
 * dev server. If they're already running on those ports, reuseExistingServer
 * keeps them alive; otherwise Playwright starts them.
 *
 * Run from frontend/ with: npm run e2e
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  expect: { timeout: 5_000 },
  retries: 0,
  use: {
    baseURL: "http://localhost:5173",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "cd ../server && python -m gsfluent serve --no-browser --port 8080",
      port: 8080,
      reuseExistingServer: true,
      timeout: 30_000,
      stdout: "ignore",
      stderr: "pipe",
    },
    {
      command: "npm run dev",
      port: 5173,
      reuseExistingServer: true,
      timeout: 30_000,
      stdout: "ignore",
      stderr: "pipe",
    },
  ],
  projects: [
    {
      name: "chromium",
      use: { browserName: "chromium" },
    },
  ],
});
