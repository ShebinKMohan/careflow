import { defineConfig, devices } from "@playwright/test";

// Kiosk e2e: Prism serves the contract's examples on MOCK_PORT and the app runs a production build.
// E2E_SERVER=dev uses `next dev` instead (e.g. with `make mock` and `pnpm -F kiosk dev` running).
const PORT = 3000;
const MOCK_PORT = 4010;
const dev = process.env.E2E_SERVER === "dev";

// Binaries are called directly, not through pnpm: pnpm starts children in their own process group,
// and Playwright's shutdown would leave them running.
const NEXT = "./node_modules/.bin/next";

export default defineConfig({
  testDir: "./tests/e2e",
  retries: 0,
  reporter: [["list"]],
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure" },
  // The installed Google Chrome by default (cdn.playwright.dev is unreachable from the dev machine);
  // PLAYWRIGHT_CHANNEL=chromium uses the browser from `playwright install chromium`.
  projects: [{ name: "chrome", use: { ...devices["Desktop Chrome"], channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome" } }],
  webServer: [
    {
      command: `../../node_modules/.bin/prism mock ../../packages/contracts/openapi.yaml --port ${MOCK_PORT}`,
      url: `http://127.0.0.1:${MOCK_PORT}/health`,
      reuseExistingServer: !process.env.CI,
      timeout: 60_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
    },
    {
      command: dev ? `${NEXT} dev --port ${PORT}` : `${NEXT} build && ${NEXT} start --port ${PORT}`,
      url: `http://localhost:${PORT}`,
      reuseExistingServer: dev,
      timeout: 240_000,
      gracefulShutdown: { signal: "SIGTERM", timeout: 5_000 },
      env: { NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`, NEXT_TELEMETRY_DISABLED: "1" },
    },
  ],
});
