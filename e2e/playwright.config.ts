import { defineConfig, devices } from "@playwright/test";

// The MVP journeys (M1-19) against a running stack: start it first with `E2E=1 make up` (replayed
// model replies) and run `make e2e`. One worker and no retries: the journeys share the stack's
// queue, and a failure is a finding, never re-run until green.
export default defineConfig({
  testDir: "./journeys",
  workers: 1,
  fullyParallel: false,
  retries: 0,
  timeout: 240_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["json", { outputFile: "test-results/journeys.json" }]],
  use: {
    ...devices["Desktop Chrome"],
    // The installed Google Chrome by default; PLAYWRIGHT_CHANNEL=chromium in CI.
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome",
    trace: "retain-on-failure",
    timezoneId: "Asia/Dubai",
  },
});
