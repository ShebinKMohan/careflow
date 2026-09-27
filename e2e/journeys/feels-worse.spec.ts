import { expect, type Page, test, type WebSocketRoute } from "@playwright/test";

import {
  answerIntake,
  answerOnPhone,
  checkIn,
  kioskPage,
  measureVitals,
  nurseSetsLevel,
  type PatientScript,
  phonePage,
  pressFeelsWorse,
  showCode,
  staffPage,
} from "../lib/actors";
import { STACK } from "../lib/stack";

// "I feel worse" (M2-07) on the real stack, persona `qr-phone` (100006, synthetic) on the phone
// and `routine` (100005) waiting ahead of it. After the nurse's decision the patient presses
// "I feel worse" with FAST signs: the nurse's open worklist and open session card show it without a
// reload, the rules' new FAST alert shows again as new since the decision, a repeated press raises
// the count, and the phone's place in the queue follows the nurse's calls, also after its socket
// was cut. The answers are the routine walk-in's, so the model replies are the recorded ones.
const PATIENT: PatientScript = {
  complaint: "I twisted my ankle playing football yesterday",
  yes: [],
  scale: "4",
  typed: [
    [/when did (this|the pain) start/i, "Yesterday afternoon"],
    [/where is the pain/i, "My left ankle"],
  ],
};
const LEVEL = 5; // KTAS 5 queues in the general department (config/rooms.yaml)

async function place(phone: Page): Promise<string> {
  const status = phone.getByTestId("waiting-status");
  await expect(status).toBeVisible();
  return ((await status.textContent()) ?? "").trim();
}

test("I feel worse after the decision: the nurse sees it live and in time, repeats count, the phone's place follows the queue", async ({ browser }) => {
  // The routine patient ahead in the general queue.
  const ahead = await kioskPage(browser, "normal");
  const first = await checkIn(ahead, "100005");
  await answerIntake(ahead, PATIENT);
  await measureVitals(ahead);

  // The phone patient: one answer on the kiosk, then the phone finishes the intake.
  const kiosk = await kioskPage(browser, "normal");
  const { sessionId } = await checkIn(kiosk, "100006");
  const onKiosk = await answerIntake(kiosk, PATIENT, { limit: 1 });
  const code = await showCode(kiosk);
  const phone = await phonePage(browser);
  // The phone's live channel runs through the test, which can cut it as a lost connection would.
  const sockets: { page: WebSocketRoute; server: WebSocketRoute }[] = [];
  await phone.routeWebSocket(/\/v1\/ws\/sessions\//, (ws) => {
    sockets.push({ page: ws, server: ws.connectToServer() });
  });
  await phone.goto(`/claim#t=${code}`);
  await answerOnPhone(phone, PATIENT, onKiosk.length);

  // The nurse decides both: KTAS 5, the general queue; the phone patient last.
  const card = await staffPage(browser, "nurse", `/nurse/${first.sessionId}`, true);
  await nurseSetsLevel(card, LEVEL);
  await card.goto(`/nurse/${sessionId}`);
  await nurseSetsLevel(card, LEVEL);
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
  expect(before, "the routine patient waits ahead").toBeGreaterThanOrEqual(2);

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

  // The nurse calls the next general patient: the phone's place moves up without a reload.
  const callNext = async () => {
    const called = worklist.waitForResponse((response) => response.url().endsWith("/v1/queue/general/call-next"));
    await worklist.getByRole("button", { name: "Call next: General" }).click();
    expect((await called).status(), "a general room was free").toBe(200);
  };
  await callNext();
  await expect(position).toHaveText(`Position: ${before - 1}`);

  // The phone's connection drops; the nurse calls again at once; the phone reconnects (a fresh
  // ticket) and reloads the status it may have missed while its socket was down.
  const before2 = sockets.length;
  await Promise.all(sockets.map(({ page, server }) => Promise.all([page.close({ code: 1001, reason: "connection lost" }), server.close()])));
  await callNext();
  await expect.poll(() => sockets.length, { message: "the phone reconnected", timeout: 20_000 }).toBeGreaterThan(before2);
  const headers = { Authorization: `Bearer ${await phone.evaluate(() => window.sessionStorage.getItem("careflow.companion.session-token"))}` };
  const server = (await (await phone.request.get(`${STACK.api}/v1/companion/status`, { headers })).json()) as {
    position: number | null;
    state: string;
    room: string | null;
  };
  if (server.state === "called") {
    await expect(phone.getByText(`Please go to ${server.room ?? ""} now.`)).toBeVisible({ timeout: 20_000 });
  } else {
    expect(server.position).toBe(before - 2);
    await expect(position).toHaveText(`Position: ${server.position ?? ""}`, { timeout: 20_000 });
  }
  test.info().annotations.push({ type: "phone after reconnect", description: await place(phone) });

  // Seen: the nurse frees every room this journey filled, from the queue controls.
  const seenButton = worklist.getByRole("button", { name: "Seen (free the room)" });
  for (let left = await seenButton.count(); left > 0; left -= 1) {
    const closed = worklist.waitForResponse((response) => /\/v1\/queue\/tokens\/[^/]+\/close$/.test(response.url()));
    await seenButton.first().click();
    expect((await closed).status()).toBe(200);
    await expect(seenButton).toHaveCount(left - 1);
  }
});
