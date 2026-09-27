import { createHmac, randomUUID } from "node:crypto";
import { mkdirSync, readFileSync } from "node:fs";

import { expect, test } from "@playwright/test";

// M1-14: a spoken answer through the real voice service (Silero VAD, smart-turn, mlx-whisper,
// Kokoro). Chrome's fake microphone plays services/voice/tests/fixtures/audio/chest_pain.wav; the
// voice service posts the turn to the Prism mock and speaks the contract's example next question.
//
//   uv sync --all-packages --extra mlx-whisper && services/voice/scripts/download_models.sh
//   VOICE_E2E=1 pnpm -F kiosk test:e2e --project voice
test.skip(process.env.VOICE_E2E !== "1", "needs the voice service and its models: VOICE_E2E=1");

const SESSION = "ses_voice_e2e";
const DEVICE = { token: "example-kiosk-token", clinicId: "clinic_demo", deviceId: "kiosk_01" };
const EVIDENCE_DIR = new URL("../../../../docs/evidence/M1-14/", import.meta.url).pathname;
const CHECKIN = JSON.parse(readFileSync(new URL("../fixtures/api/checkin.json", import.meta.url), "utf8")) as Record<
  string,
  unknown
>;

/** A patient_session token as the API issues it (HS256, M0-07 claims), for the voice service. */
function patientToken(sessionId: string, secret: string): string {
  const encode = (value: object) => Buffer.from(JSON.stringify(value)).toString("base64url");
  const now = Math.floor(Date.now() / 1000);
  const header = encode({ alg: "HS256", typ: "JWT" });
  const payload = encode({
    sub: sessionId,
    role: "patient_session",
    clinic_id: "00000000-0000-0000-0000-000000000001",
    session_id: sessionId,
    iat: now,
    exp: now + 600,
    jti: randomUUID(),
  });
  const signature = createHmac("sha256", secret).update(`${header}.${payload}`).digest("base64url");
  return `${header}.${payload}.${signature}`;
}

test("a spoken answer shows what was heard, then the next question, and the question is spoken", async ({ page }) => {
  const token = patientToken(SESSION, process.env.VOICE_E2E_JWT_SECRET ?? "");
  await page.addInitScript((device) => {
    window.sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device));
  }, DEVICE);
  await page.route("**/v1/checkin/keypad", (route) =>
    route.fulfill({ json: { ...CHECKIN, session_id: SESSION, session_token: token } }),
  );

  const socket = { url: "", sent: [] as string[], received: [] as string[], audioFrames: 0 };
  page.on("websocket", (ws) => {
    if (!ws.url().includes("/kiosk/audio")) return;
    socket.url = ws.url();
    ws.on("framesent", (frame) => {
      if (typeof frame.payload === "string") socket.sent.push(frame.payload);
    });
    ws.on("framereceived", (frame) => {
      if (typeof frame.payload === "string") socket.received.push(frame.payload);
      else socket.audioFrames += 1;
    });
  });

  await page.goto("/checkin");
  for (const digit of "100001") await page.getByRole("button", { name: digit, exact: true }).click();
  await page.getByRole("button", { name: "Check in" }).click();
  await expect(page.getByRole("heading", { name: "What is the main reason for your visit today?" })).toBeVisible();

  await page.getByRole("button", { name: "Speak your answer" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Listening…" })).toBeVisible();
  await expect(page.getByTestId("heard")).toContainText("chest pain", { timeout: 60_000 });
  await expect(page.getByRole("heading", { name: /how bad is the pain/i })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("group", { name: "Tap a number from 0 to 10" })).toBeVisible();
  await expect.poll(() => socket.audioFrames, { timeout: 30_000 }).toBeGreaterThan(0);

  mkdirSync(EVIDENCE_DIR, { recursive: true });
  await page.evaluate(() => document.fonts.ready);
  await page.screenshot({ path: `${EVIDENCE_DIR}kiosk-voice-answer.png`, fullPage: true });
  await page.getByRole("button", { name: "Stop listening" }).click();

  // The token travels only in the first message, never in the socket URL.
  expect(socket.url).toBe(`ws://127.0.0.1:${process.env.VOICE_E2E_PORT ?? "8095"}/kiosk/audio`);
  expect(JSON.parse(socket.sent[0] ?? "{}")).toMatchObject({ type: "start", session_id: SESSION, sample_rate: 16000 });
  const messages = socket.received.map((text) => JSON.parse(text) as { type: string; text?: string });
  const final = messages.find((message) => message.type === "final");
  const question = messages.find((message) => message.type === "question");
  expect(final?.text?.toLowerCase()).toContain("chest pain");
  expect(question?.text).toMatch(/how bad is the pain/i);
  expect(messages.some((message) => message.type === "tts_start")).toBe(true);
  console.log(
    `voice: heard "${final?.text}", next question "${question?.text}", ${socket.audioFrames} TTS frames received`,
  );

  const video = page.video();
  await page.close();
  await video?.saveAs(`${EVIDENCE_DIR}kiosk-voice.webm`);
});
