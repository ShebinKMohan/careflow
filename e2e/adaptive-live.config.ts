import { defineConfig, devices } from "@playwright/test";
import { STACK } from "./lib/stack";

// Existing real stack only. No replay, API stubs, process restarts or shared queue cleanup.
export default defineConfig({
  testDir: "./journeys",
  testMatch: "adaptive-live.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 360_000,
  expect: { timeout: 25_000 },
  outputDir: "../.run/adaptive-live/browser-artifacts",
  reporter: [["list"], ["json", { outputFile: "../.run/adaptive-live/browser-results.json" }]],
  use: {
    ...devices["Desktop Chrome"],
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome",
    baseURL: STACK.kiosk,
    viewport: { width: 1440, height: 1000 },
    trace: "retain-on-failure",
    actionTimeout: 15_000,
    timezoneId: "Asia/Dubai",
  },
});
