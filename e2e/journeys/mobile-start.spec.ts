import { expect, test } from "@playwright/test";

import { answerOnPhone, closeToken, kioskDeviceToken, phonePage, staffPage } from "../lib/actors";
import { assertAlerts, assertBriefing, assertDecision, assertTokenAndRoom, assertWriteBack, type JourneyContext } from "../lib/journey";
import { ROUTINE_WALK_IN } from "../lib/scripts";
import { STACK } from "../lib/stack";

test("phone-first check-in: lobby QR, private consent and identity, intake, nurse, queue and FHIR", async ({ browser }) => {
  const kioskContext = await browser.newContext({ baseURL: STACK.kiosk });
  const kiosk = await kioskContext.newPage();
  // A fresh patient browser receives the kiosk's server-side device setup automatically.
  const issued = kiosk.waitForResponse((response) => response.url().endsWith("/v1/checkin/start/qr") && response.request().method() === "POST");
  await kiosk.goto("/");
  await expect(kiosk.getByRole("button", { name: "Start on this kiosk" })).toBeVisible();
  await expect(kiosk.getByTestId("entry-qr")).toBeVisible();
  const qr = await issued;
  expect(qr.status()).toBe(200);
  const code = String((await qr.json()).qr_token);

  const phone = await phonePage(browser);
  await phone.goto(`/start#t=${code}`);
  await expect(phone).toHaveURL(/\/start$/); // consumed QR is removed from browser history
  await expect(phone.getByRole("button", { name: "Continue", exact: true })).toBeDisabled();
  await phone.getByRole("checkbox", { name: "I agree to my record and answers being used for this visit." }).check();
  await phone.getByRole("button", { name: "Continue", exact: true }).click();
  await phone.getByLabel("Six-digit patient code").fill("100005");
  await phone.getByRole("button", { name: "Continue", exact: true }).click();
  const claimed = phone.waitForResponse((response) => response.url().endsWith("/v1/checkin/start/claim") && response.request().method() === "POST");
  await phone.getByRole("button", { name: "Start my check-in", exact: true }).click();
  const result = await claimed;
  expect(result.status()).toBe(200);
  // The app immediately navigates after storing its scoped credential. Read the resulting
  // application state instead of racing Chrome's response-body lifetime across navigation.
  await expect(phone).toHaveURL(`${STACK.companion}/`);
  const started = await phone.evaluate(() => ({
    session_id: (JSON.parse(window.sessionStorage.getItem("careflow.companion.session") ?? "{}") as { sessionId?: string }).sessionId,
    session_token: window.sessionStorage.getItem("careflow.companion.session-token"),
  }));
  expect(started.session_id).toBeTruthy();
  expect(started.session_token).toBeTruthy();
  await answerOnPhone(phone, ROUTINE_WALK_IN, 0);

  const nurse = await staffPage(browser, "nurse", "/nurse");
  const ctx: JourneyContext = {
    sessionId: String(started.session_id),
    nurse,
    patient: phone,
    patientToken: String(started.session_token),
    kioskToken: await kioskDeviceToken(kiosk),
    triggers: [],
    alertsSeen: new Map(),
  };
  await assertAlerts(ctx, []);
  await assertDecision(ctx, [], { set: 5 });
  await phone.getByRole("link", { name: "See my place in the queue" }).click();
  const called = await assertTokenAndRoom(ctx);
  await assertBriefing(ctx);
  await assertWriteBack(ctx, [], { observations: false });
  await closeToken(nurse, called.token_id);
});
