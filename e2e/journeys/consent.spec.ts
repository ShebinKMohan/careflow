import { expect, test } from "@playwright/test";

import { answerIntake, kioskPage, measureVitals, RequestLog } from "../lib/actors";
import {
  chooseConsent,
  consentPosted,
  countMediaRequests,
  forMe,
  keypadCheckIn,
  recordedConsent,
  t,
  tapCard,
} from "../lib/consent";
import { Hapi } from "../lib/fhir";
import { ROUTINE_WALK_IN } from "../lib/scripts";
import { STACK } from "../lib/stack";

// The consent screen on the real stack (M2-05): what the patient chooses is saved by the API
// before the kiosk carries on, and nothing opens the camera or the microphone without it. The
// kiosk's `getUserMedia` calls are counted on every page. The ID card is the simulated reader's
// synthetic card, a mock by design. The journeys with the microphone itself need the voice
// service: consent-voice.spec.ts.

test("(b) without camera and voice: keypad check-in, an intake by tap and text, no getUserMedia", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const mediaRequests = await countMediaRequests(kiosk);
  await chooseConsent(kiosk, "withoutCameraVoice");
  await expect(kiosk).toHaveURL(/\/checkin$/);
  const saved = consentPosted(kiosk);
  const started = await keypadCheckIn(kiosk, "100005");
  expect((await saved).status(), "the choices are saved before the kiosk carries on").toBe(200);
  await forMe(kiosk);
  await expect(kiosk.getByRole("button", { name: t("intake.mic") })).toHaveCount(0);

  // The routine journey's answers, so every extraction replays: the intake runs to its end.
  await answerIntake(kiosk, ROUTINE_WALK_IN);
  await measureVitals(kiosk);
  expect(mediaRequests(), "the camera and the microphone were never asked for").toBe(0);
  const consent = await recordedConsent(started);
  expect(consent.status).toBe(200);
  expect(consent.record).toMatchObject({ camera: false, voice: false, data_use: true });
});

test("(c) declining data use sends the patient to reception: no check-in, no session, no new Encounter", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const log = new RequestLog();
  log.watch(kiosk.context());
  const mediaRequests = await countMediaRequests(kiosk);
  const hapi = await Hapi.open();
  try {
    const encounters = await hapi.count("Encounter");
    await chooseConsent(kiosk, "decline");
    await expect(kiosk.getByTestId("consent-declined")).toHaveText(t("safety.consent.reception"));
    await kiosk.getByRole("button", { name: t("safety.consent.startOver"), exact: true }).click();
    await expect(kiosk).toHaveURL(`${STACK.kiosk}/`);

    // Nothing reached a check-in or a session route: no visit was opened, nothing can be written.
    const visits = log.requests.filter((sent) => /\/v1\/(checkin|sessions)\//.test(new URL(sent.url).pathname));
    expect(visits, "no check-in and no session request").toEqual([]);
    expect(log.requests.filter((sent) => sent.url.startsWith(STACK.api) && sent.method !== "GET")).toEqual([]);
    expect(await hapi.count("Encounter"), "no new Encounter in HAPI").toBe(encounters);
    expect(mediaRequests()).toBe(0);
  } finally {
    await hapi.close();
  }
});

test("(d) consent the API did not save stops the kiosk until a retry saves it; the ID card checks in", async ({ browser }) => {
  const kiosk = await kioskPage(browser, "normal");
  const mediaRequests = await countMediaRequests(kiosk);
  await chooseConsent(kiosk, "agree");
  await expect(kiosk).toHaveURL(/\/checkin$/);

  // The first save never reaches the API: the network drops it.
  let dropped = 0;
  await kiosk.route(/\/v1\/sessions\/[^/]+\/consent$/, async (route) => {
    if (route.request().method() === "POST" && dropped === 0) {
      dropped += 1;
      await route.abort("connectionrefused");
      return;
    }
    await route.continue();
  });
  const started = await tapCard(kiosk, "routine");
  await expect(kiosk.getByTestId("consent-not-saved")).toHaveText(t("safety.consent.saveFailed"));
  await expect(kiosk).toHaveURL(/\/checkin$/); // not carried on as if saved
  expect((await recordedConsent(started)).status, "nothing is recorded, so nothing is consented").toBe(404);

  const saved = consentPosted(kiosk);
  await kiosk.getByRole("button", { name: t("safety.consent.retry"), exact: true }).click();
  expect((await saved).status()).toBe(200);
  await forMe(kiosk);
  const consent = await recordedConsent(started);
  expect(consent.status).toBe(200);
  expect(consent.record).toMatchObject({ camera: true, voice: true, data_use: true });
  expect(dropped).toBe(1);
  expect(mediaRequests(), "agreeing opens nothing by itself").toBe(0);
});
