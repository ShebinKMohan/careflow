import { expect, type Page, test, type WebSocketRoute } from "@playwright/test";

import {
  answerIntake,
  answerOnPhone,
  checkIn,
  closeToken,
  isClaim,
  kioskPage,
  nurseSetsLevel,
  type PatientScript,
  phonePage,
  showCode,
  staffPage,
} from "../lib/actors";
import { codes, Hapi } from "../lib/fhir";
import { msg } from "../lib/i18n";
import { callWithoutArriving } from "../lib/journey";
import { ROUTINE_WALK_IN_AR } from "../lib/scripts";

// @ar @rtl (M2-01): an Arabic visit on the real stack, persona `routine` (keypad 100005,
// synthetic). The patient picks Arabic on the kiosk's first screen, and the language then holds
// through a kiosk refresh, the intake, the QR hand-over, a phone refresh, a lost connection with a
// failing status, and the end of the visit. The nurse works in English on the same session. The
// answers are Arabic, so the replies to the Arabic extraction prompt are recorded in the cassette
// (E2E_RECORD=1) like the English ones.
const PATIENT: PatientScript = ROUTINE_WALK_IN_AR;
const kiosk = (key: string, values?: Record<string, string | number>) => msg("kiosk", "ar", key, values);
const phone = (key: string, values?: Record<string, string | number>) => msg("companion", "ar", key, values);

async function expectArabic(page: Page) {
  await expect(page.locator("html")).toHaveAttribute("lang", "ar");
  await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
}

const picker = (page: Page) => page.getByRole("button", { name: /^(English|العربية)$/ });

test("@ar @rtl an Arabic visit keeps its language: kiosk refresh, QR claim, phone refresh, a lost connection and the end of the visit", async ({ browser }) => {
  // The first screen: Arabic, then a refresh. The choice holds (the NEXT_LOCALE cookie).
  const desk = await kioskPage(browser, "normal");
  await desk.goto("/");
  await desk.getByRole("main").getByRole("button", { name: "العربية" }).click();
  await expect(desk).toHaveURL(/\/consent$/);
  await expectArabic(desk);
  await desk.reload();
  await expectArabic(desk);
  await expect(desk.getByRole("heading", { level: 1 })).toHaveText(kiosk("safety.consent.title"));
  await expect(picker(desk)).toHaveCount(0);

  // Check-in and two answers on the kiosk, in Arabic; the language can no longer change.
  const { sessionId } = await checkIn(desk, "100005", "ar");
  await expectArabic(desk);
  await expect(desk.getByRole("heading", { level: 2 })).toHaveText("ما السبب الرئيسي لزيارتك اليوم؟");
  await expect(picker(desk)).toHaveCount(0);
  const onKiosk = await answerIntake(desk, PATIENT, { limit: 2, locale: "ar" });
  expect(onKiosk).toHaveLength(2);
  const third = ((await desk.getByRole("heading", { level: 2 }).textContent()) ?? "").trim();
  expect(third, "the API asks an Arabic session in Arabic").toMatch(/[؀-ۿ]/);

  // The QR hand-over: a phone that has never seen CareFlow opens in the session's language.
  const code = await showCode(desk);
  const mobile = await phonePage(browser);
  const sockets: { page: WebSocketRoute; server: WebSocketRoute }[] = [];
  await mobile.routeWebSocket(/\/v1\/ws\/sessions\//, (ws) => {
    sockets.push({ page: ws, server: ws.connectToServer() });
  });
  const claimed = mobile.waitForResponse(isClaim);
  await mobile.goto(`/claim#t=${code}`);
  expect((await claimed).status(), "the phone claims the code").toBe(200);
  await expectArabic(mobile);
  await expect(mobile.getByRole("heading", { level: 2 })).toHaveText(third);

  // The kiosk forgets the patient and waits for the next one, still in Arabic, with both
  // languages on offer again.
  await expect(desk.getByTestId("handed-over")).toHaveText(kiosk("intake.phone.handedOver"));
  await expect(desk.getByRole("heading", { name: kiosk("home.title") })).toBeVisible({ timeout: 10_000 });
  await expectArabic(desk);
  await expect(desk.getByRole("main").getByRole("button", { name: "English" })).toBeVisible();

  // A phone refresh mid-intake: same question, same language.
  await mobile.reload();
  await expectArabic(mobile);
  await expect(mobile.getByRole("heading", { level: 2 })).toHaveText(third);
  await answerOnPhone(mobile, PATIENT, onKiosk.length, "ar");

  // The waiting room. The nurse (in English) sets the level; the phone shows the token, which
  // reads left to right inside the Arabic page.
  await mobile.getByRole("link", { name: phone("intake.status") }).click();
  await expect(mobile).toHaveURL(/\/status$/);
  await expectArabic(mobile);
  const card = await staffPage(browser, "nurse", `/nurse/${sessionId}`);
  await nurseSetsLevel(card, 5);
  // The decision reaches the EHR for a visit finished on the phone. Until M2-01 its Bundle failed:
  // a NEWS2 derived from no vital sign has no provenance (safety rule 4).
  const hapi = await Hapi.open();
  try {
    expect(codes(await hapi.encounter(sessionId), "priority")).toContain("KTAS-5");
  } finally {
    await hapi.close();
  }
  const status = mobile.getByTestId("waiting-status");
  const token = status.locator("p").first();
  await expect(token).toHaveText(/^[A-Z]-\d{3}$/, { timeout: 30_000 });
  await expect(token).toHaveCSS("direction", "ltr");
  const code2 = ((await token.textContent()) ?? "").trim();
  await expect(status.getByText((phone("status.token", { code: code2 })))).toBeVisible();
  await expect(status.locator("bdi")).toHaveText(code2);

  // A lost connection while the status cannot load: the last status stays, marked stale, in
  // Arabic, and "I feel worse" stays. Once the API answers again, the reconnection recovers.
  const cut = async () => {
    const open = sockets.splice(0);
    await Promise.all(open.map(({ page, server }) => Promise.all([page.close({ code: 1001, reason: "connection lost" }), server.close()])));
    await expect.poll(() => sockets.length, { message: "the phone reconnected", timeout: 20_000 }).toBeGreaterThan(0);
  };
  await mobile.route("**/v1/companion/status", (route) => route.abort("internetdisconnected"));
  await cut();
  await expect(mobile.getByTestId("status-stale")).toHaveText(phone("status.stale"));
  await expect(token).toHaveText(code2);
  await expect(mobile.getByTestId("feels-worse")).toHaveText(phone("safety.feelsWorse.button"));
  await mobile.unroute("**/v1/companion/status");
  await cut();
  await expect(mobile.getByTestId("status-stale")).toHaveCount(0);
  await expect(token).toHaveText(code2);

  // The end of the visit: called in (the room reads left to right), then seen. The phone shows the
  // room only while the token is called, so the patient is not marked arrived before it is read.
  // The phone says the visit has ended, in Arabic, and still does after a refresh.
  const called = await callWithoutArriving(card, "general", sessionId);
  expect(called.token_code).toBe(code2);
  const room = mobile.getByText((phone("status.room", { room: called.room ?? "" })));
  await expect(room).toBeVisible();
  await expect(room.locator("bdi")).toHaveCSS("direction", "ltr");
  await closeToken(card, called.token_id);
  await expect(mobile.getByTestId("visit-ended")).toHaveText(phone("safety.status.ended"));
  await mobile.reload();
  await expectArabic(mobile);
  await expect(mobile.getByTestId("visit-ended")).toHaveText(phone("safety.status.ended"));
});
