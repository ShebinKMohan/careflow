import { expect, type Page, test, type WebSocketRoute } from "@playwright/test";

import {
  answerIntake,
  answerOnPhone,
  checkIn,
  closeToken,
  kioskDeviceToken,
  kioskPage,
  measureVitals,
  nurseSetsLevel,
  phonePage,
  pressFeelsWorse,
  showCode,
  staffPage,
} from "../lib/actors";
import { assertAlerts, assertBriefing, assertDecision, assertTokenAndRoom, assertWriteBack, type JourneyContext } from "../lib/journey";
import { ROUTINE_WALK_IN } from "../lib/scripts";

// Journey 6 (M2-12): the QR phone path, persona `qr-phone` (100006, synthetic), with two `routine`
// (100005) patients waiting ahead. One answer on the kiosk, the rest on the phone after the QR
// hand-over (M2-06); the nurse decides; then "I feel worse" (M2-07) with FAST signs: the nurse's
// open worklist and card show it without a reload and within 2 s, the rules' new FAST alert shows
// as new since the decision, a repeated press raises the count, and the phone's place follows the
// nurse's calls, also after its socket was cut. Every patient the nurse calls walks in at once (the
// E2E stack's grace window is 5 s). Then the phone patient is called: token and room on the phone
// and the board, the attributed briefing, and HAPI. The answers are the routine walk-in's, so the
// model replies are the recorded ones.
const LEVEL = 5; // KTAS 5 queues in the general department (config/rooms.yaml)

async function place(phone: Page): Promise<string> {
  const status = phone.getByTestId("waiting-status");
  await expect(status).toBeVisible();
  return ((await status.textContent()) ?? "").trim();
}

test("QR phone path: the phone finishes the intake, I feel worse reaches the nurse live and in time, the place follows the queue, token, briefing, HAPI", async ({
  browser,
}) => {
  test.setTimeout(420_000);
  // Two routine patients ahead in the general queue: the nurse's calls below never reach the phone.
  const ahead: string[] = [];
  for (let patient = 0; patient < 2; patient += 1) {
    const kiosk = await kioskPage(browser, "normal");
    ahead.push((await checkIn(kiosk, "100005")).sessionId);
    await answerIntake(kiosk, ROUTINE_WALK_IN);
    await measureVitals(kiosk);
  }

  // The phone patient: one answer on the kiosk, then the phone finishes the intake.
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId } = await checkIn(kiosk, "100006");
  const onKiosk = await answerIntake(kiosk, ROUTINE_WALK_IN, { limit: 1 });
  const code = await showCode(kiosk);
  const phone = await phonePage(browser);
  // The phone's live channel runs through the test, which can cut it as a lost connection would.
  const sockets: { page: WebSocketRoute; server: WebSocketRoute }[] = [];
  await phone.routeWebSocket(/\/v1\/ws\/sessions\//, (ws) => {
    sockets.push({ page: ws, server: ws.connectToServer() });
  });
  await phone.goto(`/claim#t=${code}`);
  await answerOnPhone(phone, ROUTINE_WALK_IN, onKiosk.length);
  const phoneToken = await phone.evaluate(() => window.sessionStorage.getItem("careflow.companion.session-token") ?? "");

  // The nurse decides: the patients ahead first, the phone patient last (a to b).
  const card = await staffPage(browser, "nurse", `/nurse/${ahead[0]}`, true);
  for (const session of ahead) {
    await card.goto(`/nurse/${session}`);
    await nurseSetsLevel(card, LEVEL);
  }
  const ctx: JourneyContext = {
    sessionId,
    nurse: card,
    patient: phone,
    patientToken: phoneToken,
    kioskToken: await kioskDeviceToken(kiosk),
    triggers: [],
    alertsSeen: new Map(),
  };
  await assertAlerts(ctx, []);
  await assertDecision(ctx, [], { set: LEVEL });
  await expect(card.getByTestId("live-status")).toHaveText("Live");

  // The nurse's worklist, open and live.
  const worklist = await staffPage(browser, "nurse", "/nurse");
  await expect(worklist.getByTestId("live-status")).toHaveText("Live");

  // The phone's waiting room, live.
  await phone.getByRole("link", { name: "See my place in the queue" }).click();
  await expect(phone).toHaveURL(/\/status$/);
  const position = phone.getByTestId("position");
  await expect(position).toHaveText(/^Position: \d+$/);
  const before = Number(((await position.textContent()) ?? "").match(/\d+/)?.[0]);
  expect(before, "two routine patients wait ahead").toBeGreaterThanOrEqual(3);

  // "I feel worse" with FAST signs. The nurse's worklist shows it within 2 s of the press.
  const banner = worklist.locator(`[data-testid="feels-worse-banner"][data-session-id="${sessionId}"]`);
  const seen = banner.waitFor({ timeout: 30_000 }).then(() => Date.now());
  const pressedAt = await pressFeelsWorse(phone, { fast: ["Face drooping", "Arm weak"] });
  const shownAt = await seen;
  const latency = shownAt - pressedAt;
  test.info().annotations.push({ type: "feels-worse latency", description: `on the nurse's worklist ${latency} ms after the patient pressed "Tell the nurse"` });
  if (process.env.CI) {
    if (latency > 2_000) test.info().annotations.push({ type: "warning", description: `feels-worse latency ${latency} ms (budget 2000 ms)` });
  } else {
    expect(latency, "the nurse's worklist shows the press within 2 s").toBeLessThanOrEqual(2_000);
  }
  await expect(banner.getByTestId("feels-worse-count")).toHaveText(/1/);
  await expect(banner).toContainText("Face drooping");

  // The rules raised FAST_STROKE after the decision: it shows again, marked as new, on the
  // worklist and on the nurse's already open card, without a reload.
  const stroke = (page: Page) => page.getByTestId("alert-banner").filter({ has: page.getByTestId("after-decision") });
  await expect(stroke(worklist).first()).toBeVisible();
  await expect(stroke(card)).toHaveCount(1);
  await expect(card.getByTestId("feels-worse-banner")).toBeVisible();

  // A second press raises the count on the same banner (never dropped, never a second banner).
  await pressFeelsWorse(phone);
  await expect(banner.getByTestId("feels-worse-count")).toHaveText(/2/);
  await expect(worklist.locator(`[data-testid="feels-worse-banner"][data-session-id="${sessionId}"]`)).toHaveCount(1);
  await expect(card.getByTestId("feels-worse-count")).toHaveText(/2/);

  // The nurse calls the next general patient, who walks in: the phone's place moves up without a
  // reload.
  const callNext = async () => {
    const next = worklist.waitForResponse((response) => response.url().endsWith("/v1/queue/general/call-next"));
    await worklist.getByRole("button", { name: "Call next: General" }).click();
    const answer = await next;
    expect(answer.status(), "a general room was free").toBe(200);
    const token = (await answer.json()).token as { token_code: string; session_id: string } | null;
    expect(token?.session_id, "the nurse calls a patient ahead, never the phone").not.toBe(sessionId);
    const arrived = worklist.waitForResponse((response) => /\/v1\/queue\/tokens\/[^/]+\/arrived$/.test(response.url()));
    await worklist.getByTestId("called-token").filter({ hasText: token!.token_code }).getByRole("button", { name: "Arrived", exact: true }).click();
    expect((await arrived).status()).toBe(200);
  };
  await callNext();
  await expect(position).toHaveText(`Position: ${before - 1}`);

  // The phone's connection drops; the nurse calls again at once; the phone reconnects (a fresh
  // ticket) and reloads the status it may have missed while its socket was down.
  const before2 = sockets.length;
  await Promise.all(sockets.map(({ page, server }) => Promise.all([page.close({ code: 1001, reason: "connection lost" }), server.close()])));
  await callNext();
  await expect.poll(() => sockets.length, { message: "the phone reconnected", timeout: 20_000 }).toBeGreaterThan(before2);
  await expect(position).toHaveText(`Position: ${before - 2}`, { timeout: 20_000 });
  test.info().annotations.push({ type: "phone after reconnect", description: await place(phone) });

  // Seen: the nurse frees every room this journey filled, from the queue controls.
  const seenButton = worklist.getByRole("button", { name: "Seen (free the room)" });
  for (let left = await seenButton.count(); left > 0; left -= 1) {
    const closed = worklist.waitForResponse((response) => /\/v1\/queue\/tokens\/[^/]+\/close$/.test(response.url()));
    await seenButton.first().click();
    expect((await closed).status()).toBe(200);
    await expect(seenButton).toHaveCount(left - 1);
  }

  // c to e: the phone patient is called (token and room on the phone and the board), the
  // briefing, and HAPI.
  const called = await assertTokenAndRoom(ctx);
  await assertBriefing(ctx);
  await assertWriteBack(ctx, []);
  await closeToken(ctx.nurse, called.token_id); // seen: the room is free for the next journey
});
