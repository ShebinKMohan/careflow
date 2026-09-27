import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

import { answerIntake, answerOnPhone, checkIn, isClaim, kioskPage, type PatientScript, phonePage, RequestLog, showCode } from "../lib/actors";
import { STACK } from "../lib/stack";

// The QR hand-over (M2-06), persona `qr-phone` (keypad 100006, synthetic): the kiosk and a phone
// (a second browser context with the Pixel 7 profile) on the real API, Postgres, Valkey and HAPI.
// The answers are the routine walk-in's, so the extraction requests are the recorded ones: the
// prompt carries only the conversation, never the persona.
const PATIENT: PatientScript = {
  complaint: "I twisted my ankle playing football yesterday",
  yes: [],
  scale: "4",
  typed: [
    [/when did (this|the pain) start/i, "Yesterday afternoon"],
    [/where is the pain/i, "My left ankle"],
  ],
};
const WELCOME = /^Welcome$/;

test("QR hand-over: two answers on the kiosk, the phone claims once and finishes, the kiosk forgets the patient", async ({ browser }) => {
  const log = new RequestLog();
  const kiosk = await kioskPage(browser, "normal");
  log.watch(kiosk.context());
  const { sessionId, patientToken } = await checkIn(kiosk, "100006");
  const onKiosk = await answerIntake(kiosk, PATIENT, { limit: 2 });
  expect(onKiosk).toHaveLength(2);
  const third = ((await kiosk.getByRole("heading", { level: 2 }).textContent()) ?? "").trim();
  expect(third.length).toBeGreaterThan(0);

  const code = await showCode(kiosk);
  const phone = await phonePage(browser, log);
  const claimed = phone.waitForResponse(isClaim);
  await phone.goto(`/claim#t=${code}`);
  expect((await claimed).status(), "the phone claims the code once").toBe(200);
  const claimedAt = Date.now();

  // The phone continues where the kiosk stopped: question three.
  await expect(phone.getByRole("heading", { level: 2 })).toHaveText(third);
  expect(new URL(phone.url()).hash, "the code leaves the address bar").toBe("");

  // The kiosk says so, then forgets the patient and is back on the welcome screen within 6 s.
  await expect(kiosk.getByTestId("handed-over")).toBeVisible();
  await expect(kiosk.getByRole("heading", { name: WELCOME })).toBeVisible({ timeout: 6_000 });
  const cleared = Date.now() - claimedAt;
  test.info().annotations.push({ type: "kiosk cleared", description: `${cleared} ms after the claim answered` });
  expect(cleared, "the kiosk is back on the welcome screen within 6 s").toBeLessThanOrEqual(6_000);
  const stored = await kiosk.evaluate(() => Object.fromEntries(Object.entries(window.sessionStorage)));
  expect(Object.keys(stored).sort(), "only the kiosk's own sign-in and simulator setting").toEqual(
    ["careflow.kiosk.device", "careflow.kiosk.simulatorScenario"].filter((key) => key in stored),
  );
  expect(JSON.stringify(stored)).not.toContain(sessionId);
  expect(JSON.stringify(stored)).not.toContain(patientToken);

  // The kiosk's patient token no longer works.
  const revoked = await kiosk.request.get(`${STACK.api}/v1/sessions/${sessionId}/status`, {
    headers: { Authorization: `Bearer ${patientToken}` },
  });
  expect(revoked.status(), "the kiosk's token is revoked").toBe(401);

  // A refresh on the phone keeps its place; then it finishes the intake.
  await phone.reload();
  await expect(phone.getByRole("heading", { level: 2 })).toHaveText(third);
  const onPhone = await answerOnPhone(phone, PATIENT, onKiosk.length);
  expect(onPhone.length).toBeGreaterThan(0);
  await expect(phone.getByRole("link", { name: "See my place in the queue" })).toBeVisible();

  // The capture: the code travelled once, in the claim's body, never in an address.
  const carrying = log.carrying(code).map((sent) => `${sent.method} ${new URL(sent.url).pathname}`);
  expect(carrying, "the code is only in the claim body").toEqual(["POST /v1/checkin/qr/claim"]);
  expect(log.requests.filter((sent) => sent.url.includes(code))).toEqual([]);
  expect(log.requests.length).toBeGreaterThan(10);
});

test("QR hand-over: a claim whose answer was lost is recovered by the same phone, and by no other", async ({ browser }) => {
  const log = new RequestLog();
  const kiosk = await kioskPage(browser, "normal");
  log.watch(kiosk.context());
  await checkIn(kiosk, "100006");
  await answerIntake(kiosk, PATIENT, { limit: 1 });
  const second = ((await kiosk.getByRole("heading", { level: 2 }).textContent()) ?? "").trim();
  const code = await showCode(kiosk);

  // The claim reaches the API and succeeds, but its answer never reaches the phone.
  const phone = await phonePage(browser, log);
  let lose = true;
  await phone.route("**/v1/checkin/qr/claim", async (route) => {
    if (!lose) return route.continue();
    lose = false;
    const answered = await route.fetch();
    expect(answered.status(), "the API committed the claim").toBe(200);
    await route.abort("connectionreset");
  });
  await phone.goto(`/claim#t=${code}`);
  await expect(phone.getByRole("alert").filter({ hasText: /could not reach the clinic/ })).toBeVisible();

  // The kiosk was told and forgot the patient; a second phone with the code gets nothing.
  await expect(kiosk.getByRole("heading", { name: WELCOME })).toBeVisible({ timeout: 10_000 });
  const other = await phone.request.post(`${STACK.api}/v1/checkin/qr/claim`, {
    data: { qr_token: code, claim_key: randomBytes(32).toString("base64url") },
  });
  expect(other.status(), "another phone cannot claim the spent code").toBe(409);

  // "Try again": the same code with the same key; the phone gets its credential and question two.
  const retried = phone.waitForResponse(isClaim);
  await phone.getByRole("button", { name: "Try again" }).click();
  expect((await retried).status(), "the claiming phone recovers").toBe(200);
  await expect(phone.getByRole("heading", { level: 2 })).toHaveText(second);
  const claims = log.requests.filter((sent) => new URL(sent.url).pathname === "/v1/checkin/qr/claim");
  expect(claims).toHaveLength(2);
  const keys = claims.map((sent) => (JSON.parse(sent.body ?? "{}") as { claim_key?: string }).claim_key);
  expect(keys[0], "the retry proves it is the same phone").toBe(keys[1]);
  expect(log.requests.filter((sent) => sent.url.includes(code))).toEqual([]);
});

test("QR hand-over: a phone answer whose response was lost is applied once, and a stale answer is refused", async ({ browser }) => {
  const log = new RequestLog();
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId } = await checkIn(kiosk, "100006");
  await answerIntake(kiosk, PATIENT, { limit: 1 });
  const code = await showCode(kiosk);
  const phone = await phonePage(browser, log);
  await phone.goto(`/claim#t=${code}`);
  const heading = phone.getByRole("heading", { level: 2 });
  await expect(heading).toHaveText(/when did (this|the pain) start/i);
  const token = await phone.evaluate(() => window.sessionStorage.getItem("careflow.companion.session-token"));
  const headers = { Authorization: `Bearer ${token ?? ""}` };
  const status = async () => (await phone.request.get(`${STACK.api}/v1/sessions/${sessionId}/status`, { headers })).json();
  const before = (await status()) as { intake_revision: number; current_question: { question_id: string } };

  // The answer reaches the API; its response never reaches the phone.
  let lose = true;
  await phone.route("**/v1/sessions/*/turns", async (route) => {
    if (!lose) return route.continue();
    lose = false;
    const answered = await route.fetch();
    expect(answered.status(), "the API took the answer").toBe(200);
    await route.abort("connectionreset");
  });
  await phone.getByLabel("Or type your answer").fill("Yesterday afternoon");
  await phone.getByRole("button", { name: "Send" }).click();

  // The phone reads the server's checkpoint instead of sending again: the next question shows.
  await expect(heading).not.toHaveText(/when did (this|the pain) start/i);
  const after = (await status()) as { intake_revision: number; current_question: { question_id: string; text: string } };
  expect(after.intake_revision).toBeGreaterThan(before.intake_revision);
  await expect(heading).toHaveText(after.current_question.text);
  const sent = log.requests.filter(
    (request) => request.method === "POST" && /\/v1\/sessions\/[^/]+\/turns$/.test(new URL(request.url).pathname),
  );
  expect(sent, "the phone sent the answer once").toHaveLength(1);

  // An answer for the old question (a second tab, a late retry) is refused and changes nothing.
  const stale = await phone.request.post(`${STACK.api}/v1/sessions/${sessionId}/turns`, {
    headers,
    data: { input_mode: "text", text: "Yesterday afternoon", language: "en", expected_intake_revision: before.intake_revision },
  });
  expect(stale.status(), "a stale answer is a conflict").toBe(409);
  expect(((await status()) as { intake_revision: number }).intake_revision).toBe(after.intake_revision);
});
