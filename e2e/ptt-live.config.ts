import { defineConfig, devices } from "@playwright/test";

// Isolated live speech proof. No queue cleanup and no network mocks; the microphone is synthetic.
export default defineConfig({
  testDir: "./journeys",
  testMatch: "hold-to-talk-live.spec.ts",
  workers: 1,
  retries: 0,
  timeout: 150_000,
  expect: { timeout: 20_000 },
  reporter: [["list"], ["json", { outputFile: "../docs/evidence/ADAPTIVE-20260929/voice-playwright.json" }]],
  use: {
    ...devices["Desktop Chrome"],
    viewport: { width: 1440, height: 1100 },
    channel: process.env.PLAYWRIGHT_CHANNEL ?? "chrome",
    trace: "retain-on-failure",
    // Traces are local and may contain synthetic session tokens; never attach them publicly.
    launchOptions: { args: [
      "--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
      `--use-file-for-fake-audio-capture=${process.env.PTT_AUDIO_FILE ?? new URL("../services/voice/tests/fixtures/audio/chest_pain.wav", import.meta.url).pathname}%noloop`,
    ] },
  },
});
