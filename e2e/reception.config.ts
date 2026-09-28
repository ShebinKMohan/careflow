import { defineConfig, devices } from "@playwright/test";

import { STACK } from "./lib/stack";

// Reception runs against the existing local stack. It does not reset rooms or run old journeys.
export default defineConfig({
  testDir: "./journeys",
  testMatch: "reception.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 15_000 },
  outputDir: "../docs/evidence/RECEPTION-20260928/browser-artifacts",
  reporter: [["list"], ["json", { outputFile: "../docs/evidence/RECEPTION-20260928/browser-results.json" }]],
  use: {
    ...devices["Desktop Chrome"],
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome",
    baseURL: STACK.kiosk,
    viewport: { width: 1280, height: 800 },
    trace: "retain-on-failure",
    actionTimeout: 10_000,
    timezoneId: "Asia/Dubai",
  },
});
