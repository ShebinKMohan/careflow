import { randomBytes } from "node:crypto";
import { fileURLToPath } from "node:url";

import { defineConfig, devices } from "@playwright/test";

// Kiosk e2e: Prism serves the contract's examples on MOCK_PORT and the app runs a production build.
// E2E_SERVER=dev uses `next dev` instead (e.g. with `make mock` and `pnpm -F kiosk dev` running).
const PORT = 3000;
const MOCK_PORT = 4010;
const dev = process.env.E2E_SERVER === "dev";
// VOICE_E2E=1 adds the real voice service (M1-14, needs its models and the mlx-whisper extra) and
// a voice-enabled kiosk build for tests/e2e/voice.spec.ts. One JWT secret is shared by the voice
// service and the spec; test workers inherit it from the runner's environment.
const voice = process.env.VOICE_E2E === "1";
// Its own port (not the dev default 8090), so a voice service already running is never reused.
if (voice) {
  process.env.VOICE_E2E_JWT_SECRET ??= randomBytes(32).toString("hex");
  process.env.VOICE_E2E_PORT ??= "8095";
}
const VOICE_PORT = Number(process.env.VOICE_E2E_PORT ?? "8095");
const FAKE_MIC = fileURLToPath(new URL("../../services/voice/tests/fixtures/audio/chest_pain.wav", import.meta.url));
const channel = process.env.PLAYWRIGHT_CHANNEL ?? "chrome";

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
  projects: [
    { name: "chrome", testIgnore: /voice\.spec\.ts$/, use: { ...devices["Desktop Chrome"], channel } },
    {
      // The microphone is Chrome's fake device playing the synthetic fixture WAV (in a loop).
      name: "voice",
      testMatch: /voice\.spec\.ts$/,
      use: {
        ...devices["Desktop Chrome"],
        channel,
        permissions: ["microphone"],
        video: "on",
        launchOptions: {
          args: [
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            `--use-file-for-fake-audio-capture=${FAKE_MIC}`,
          ],
        },
      },
    },
  ],
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
      env: {
        NEXT_PUBLIC_API_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`,
        NEXT_TELEMETRY_DISABLED: "1",
        ...(voice
          ? { NEXT_PUBLIC_VOICE_ENABLED: "true", NEXT_PUBLIC_VOICE_WS_URL: `ws://127.0.0.1:${VOICE_PORT}/kiosk/audio` }
          : {}),
      },
    },
    ...(voice
      ? [
          {
            // The real voice service; its turns go to the Prism mock (the contract's example reply).
            command: ".venv/bin/careflow-voice", // not `uv run`: see the note on child processes above
            cwd: "../..",
            url: `http://127.0.0.1:${VOICE_PORT}/healthz`,
            reuseExistingServer: false,
            timeout: 240_000,
            gracefulShutdown: { signal: "SIGTERM" as const, timeout: 5_000 },
            env: {
              CAREFLOW_JWT_SECRET: process.env.VOICE_E2E_JWT_SECRET ?? "",
              CAREFLOW_API_BASE_URL: `http://127.0.0.1:${MOCK_PORT}`,
              VOICE_PORT: String(VOICE_PORT),
            },
          },
        ]
      : []),
  ],
});
