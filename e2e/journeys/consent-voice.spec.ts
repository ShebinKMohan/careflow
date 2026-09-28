import { expect, request, test } from "@playwright/test";

import { kioskPage, staffTokenFor } from "../lib/actors";
import { chooseConsent, consentPosted, countMediaRequests, forMe, keypadCheckIn, recordedConsent, t, tapCard } from "../lib/consent";
import { STACK } from "../lib/stack";

// Consent and the microphone on the real stack (M2-05), with the real voice service: its local
// Whisper hears Chrome's fake microphone, which plays the synthetic Kokoro recording
// services/voice/tests/fixtures/audio/chest_pain.wav ("I have chest pain since this morning.",
// never a real voice). The voice service needs Apple silicon and its models, so these journeys
// are skipped on a stack without one (CI runs VOICE=0).

const SPOKEN = new URL("../../services/voice/tests/fixtures/audio/chest_pain.wav", import.meta.url).pathname;

test.use({
  launchOptions: {
    args: ["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream", `--use-file-for-fake-audio-capture=${SPOKEN}%noloop`],
  },
});

test.beforeAll(async () => {
  const answers = await fetch(`${STACK.voice}/healthz`).then(
    (response) => response.ok,
    () => false,
  );
  test.skip(!answers, `no voice service answers at ${STACK.voice} (VOICE=0, no models, or not Apple silicon)`);
});

test("(a) agree, tap the ID card, answer by voice: the microphone opens only after the consent is saved", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  await kiosk.context().grantPermissions(["microphone"], { origin: STACK.kiosk });
  const mediaRequests = await countMediaRequests(kiosk);
  await chooseConsent(kiosk, "agree");
  await expect(kiosk).toHaveURL(/\/checkin$/);
  const saved = consentPosted(kiosk);
  const started = await tapCard(kiosk, "chest-pain");
  expect((await saved).status()).toBe(200);
  await forMe(kiosk);
  expect(mediaRequests(), "nothing opens the microphone before the patient asks").toBe(0);

  const heading = kiosk.getByRole("heading", { level: 2 });
  const first = (await heading.textContent())?.trim() ?? "";
  await kiosk.getByRole("button", { name: t("intake.mic"), exact: true }).click();
  await expect(kiosk.getByRole("status").filter({ hasText: t("intake.listening") })).toBeVisible();
  expect(mediaRequests()).toBe(1);
  // Whisper's transcript comes back, and the voice service's turn moved the intake on.
  await expect(kiosk.getByTestId("heard")).toContainText(/chest pain since this morning/i, { timeout: 90_000 });
  await expect(heading).not.toHaveText(first, { timeout: 30_000 });
  await kiosk.getByRole("button", { name: t("intake.stopMic"), exact: true }).click();

  // The spoken answer is in the facts, quoted from the transcript, as the patient said it.
  const api = await request.newContext({ baseURL: STACK.api });
  try {
    const nurse = { Authorization: `Bearer ${await staffTokenFor("nurse")}` };
    const facts = await (await api.get(`/v1/sessions/${started.session_id}/facts`, { headers: nurse })).json();
    expect(facts.chief_complaint).toMatchObject({ provenance: "PATIENT_STATED", source: { kind: "transcript_span" } });
    expect("I have chest pain since this morning.".toLowerCase()).toContain(String(facts.chief_complaint.source.quote).toLowerCase());
  } finally {
    await api.dispose();
  }
  const consent = await recordedConsent(started);
  expect(consent.record).toMatchObject({ voice: true, data_use: true });
});

test("a reload forgets the patient and the consent: no microphone until consent is given again", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const mediaRequests = await countMediaRequests(kiosk);
  await chooseConsent(kiosk, "agree");
  await keypadCheckIn(kiosk, "100005");
  await forMe(kiosk);
  const mic = kiosk.getByRole("button", { name: t("intake.mic"), exact: true });
  await expect(mic, "consented and saved: the patient may speak").toBeVisible();

  await kiosk.reload();
  await expect(kiosk.getByText(t("intake.needsCheckin"))).toBeVisible();
  await expect(mic).toHaveCount(0);
  await kiosk.getByRole("link", { name: t("intake.checkinLink") }).click();
  await expect(kiosk, "the choices were forgotten with the patient: asked again").toHaveURL(/\/consent$/);
  await kiosk.getByRole("button", { name: t("safety.consent.withoutCameraVoice"), exact: true }).click();
  const saved = consentPosted(kiosk);
  await keypadCheckIn(kiosk, "100005");
  expect((await saved).status()).toBe(200);
  await forMe(kiosk);
  await expect(mic).toHaveCount(0);
  expect(mediaRequests()).toBe(0);
});
