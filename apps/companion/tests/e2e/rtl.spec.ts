import { readFileSync } from "node:fs";

import type { Schema } from "@careflow/contracts";
import { devices, expect, type Locator, type Page, test } from "@playwright/test";

// @rtl (M2-01): the phone in English and in Arabic, from the QR claim to the waiting room (queue
// status, "ask the clinic" with its citation and its medical refusal), against stubbed HTTP and a
// quiet stubbed session WebSocket. The phone takes the session's language from the claim. Direction
// and language are asserted everywhere; the screenshots are compared only when the browser runs in
// the pinned Playwright image (`make rtl`), because baselines differ by OS.
const json = <T,>(path: string): T => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8")) as T;

type Locale = "en" | "ar";
type Messages = { [key: string]: string | Messages };
const MESSAGES: Record<Locale, Messages> = { en: json("../../messages/en.json"), ar: json("../../messages/ar.json") };
/** The app's own message for `key` (dotted), so the test follows the shipped strings; the `<ltr>`
 * wrapper renders as an isolated span, so the screen shows the text without it. */
function msg(locale: Locale, key: string): string {
  const value = key.split(".").reduce<string | Messages>((node, part) => (node as Messages)[part]!, MESSAGES[locale]);
  if (typeof value !== "string") throw new Error(`${key} is not a string`);
  return value.replace(/<\/?ltr>/g, "");
}

// A phone's viewport, touch and user agent (the browser type is the project's).
const { viewport, userAgent, deviceScaleFactor, isMobile, hasTouch } = devices["Pixel 7"];
test.use({ viewport, userAgent, deviceScaleFactor, isMobile, hasTouch, timezoneId: "UTC" });

const NOW = new Date("2026-09-27T09:20:00Z");
const TOKEN = {
  token_id: "tok_phone",
  session_id: "ses_phone",
  token_code: "G-031",
  department: "general",
  status: "waiting",
  issued_at: "2026-09-27T09:05:00Z",
} satisfies Schema<"QueueToken">;
const STATUS = { state: "queued", position: 3, wait_estimate_minutes: 25, token: TOKEN, room: null, patient_type: "adult" } satisfies Schema<"CompanionStatus">;
const QUESTION: Record<Locale, string> = { en: "Is there parking at the clinic?", ar: "هل يوجد موقف سيارات في العيادة؟" };
const ANSWER: Record<Locale, string> = {
  en: "Yes. Free parking is behind the main building.",
  ar: "نعم. يوجد موقف مجاني خلف المبنى الرئيسي.",
};
const CITATION = { doc_id: "clinic_parking", title: "Parking and access", chunk_id: "clinic_parking_01" } satisfies Schema<"Citation">;

async function stubApi(page: Page, locale: Locale) {
  const asked: unknown[] = [];
  const claims: unknown[] = [];
  // Like the API, the code works once: a second claim of it is refused.
  await page.route("**/v1/checkin/qr/claim", (route) => {
    claims.push(route.request().postDataJSON());
    return claims.length > 1
      ? route.fulfill({ status: 409, contentType: "application/problem+json", json: { title: "Already used", status: 409 } })
      : route.fulfill({
          json: { session_id: "ses_phone", session_token: "phone-test-token", language: locale, patient_display: "L. K." } satisfies Schema<"CheckinResult">,
        });
  });
  await page.route("**/v1/sessions/ses_phone/status", (route) =>
    route.fulfill({ json: { state: "queued", intake_revision: 6 } satisfies Schema<"SessionStatus"> }),
  );
  await page.route("**/v1/companion/status", (route) => route.fulfill({ json: STATUS }));
  await page.route("**/v1/ws-ticket", (route) =>
    route.fulfill({ status: 201, json: { ticket: "tkt_phone_0123456789abcdef", channel: "session", session_id: "ses_phone", expires_at: "2026-09-27T09:20:30Z" } }),
  );
  await page.routeWebSocket(/\/v1\/ws\/sessions\//, () => {}); // connected, and quiet
  await page.route("**/v1/companion/ask", (route) => {
    const body = route.request().postDataJSON() as Schema<"AskRequest">;
    asked.push(body);
    const medical = asked.length > 1; // the second question is a medical one
    return route.fulfill({
      json: medical
        ? { answer: "", citations: [], refused_medical: true, dont_know: false }
        : { answer: ANSWER[body.language], citations: [CITATION], refused_medical: false, dont_know: false },
    });
  });
  return { asked, claims };
}

async function expectLanguage(page: Page, locale: Locale) {
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
  await expect(page.locator("html")).toHaveAttribute("dir", locale === "ar" ? "rtl" : "ltr");
}

/** Freeze the clock `at` seconds after NOW ("updated hh:mm" is the same on every run), then
 * compare what the phone's screen shows with `focus` in the middle of it. Not a full-page capture:
 * the sticky "I feel worse" button would land mid-page there, over the content it never hides on
 * a phone. Each screenshot of a test takes a later moment. */
async function snap(page: Page, name: string, at: number, focus: Locator) {
  await focus.evaluate((element) => element.scrollIntoView({ block: "center" }));
  await page.clock.pauseAt(new Date(NOW.getTime() + at * 1_000));
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`);
  await page.clock.resume();
}

for (const locale of ["en", "ar"] as const) {
  test(`@rtl the phone in ${locale === "en" ? "English" : "Arabic"}: claim, queue status and ask the clinic`, async ({ page }) => {
    await page.clock.install({ time: NOW });
    const { asked, claims } = await stubApi(page, locale);
    await page.goto("/claim#t=browser-qr-code-0001");
    // The phone follows the session's language, whatever the phone was showing before.
    const welcome = page.getByRole("heading", { name: msg(locale, "claim.welcome").replace("{initials}", "L. K.") });
    await expect(welcome).toBeVisible();
    await expectLanguage(page, locale);
    // The one-time code has left the address bar, also after the switch to the session's language,
    // so a reload resumes the visit instead of claiming a used code.
    expect(new URL(page.url()).hash).toBe("");
    await page.reload();
    await expect(welcome).toBeVisible();
    await expectLanguage(page, locale);
    expect(claims).toHaveLength(1);
    await page.getByRole("link", { name: msg(locale, "intake.status") }).click();

    await expect(page).toHaveURL(/\/status$/);
    await expectLanguage(page, locale);
    const status = page.getByTestId("waiting-status");
    await expect(status.locator("p").first()).toHaveText("G-031");
    await expect(status.locator("p").first()).toHaveCSS("direction", "ltr");
    await expect(status.locator("bdi")).toHaveText("G-031");
    await expect(status.getByTestId("position")).toHaveText(msg(locale, "status.position").replace("{position}", "3"));
    await expect(page.getByRole("button", { name: msg(locale, "safety.feelsWorse.button") })).toBeVisible();
    await expect(page.getByTestId("prototype-footer")).toBeVisible();
    await expect(page.getByTestId("arabic-disclaimer")).toBeVisible();
    await snap(page, `companion-${locale}-queue`, 30, status);

    await page.getByLabel(msg(locale, "ask.label")).fill(QUESTION[locale]);
    await page.getByRole("button", { name: msg(locale, "ask.send"), exact: true }).click();
    const answer = page.getByTestId("ask-answer");
    await expect(answer).toContainText(ANSWER[locale]);
    // The clinic document keeps its own direction inside either layout; its id reads left to right.
    const citation = answer.getByRole("list", { name: msg(locale, "ask.sources") }).getByRole("listitem");
    await expect(citation).toContainText(CITATION.title);
    await expect(citation.locator("bdi")).toHaveCSS("direction", "ltr");
    await snap(page, `companion-${locale}-answer`, 60, answer);

    // A medical question gets the fixed refusal, in the patient's language (safety rule 2).
    await page.getByLabel(msg(locale, "ask.label")).fill(locale === "en" ? "Should I take aspirin?" : "هل آخذ الأسبرين؟");
    await page.getByRole("button", { name: msg(locale, "ask.send"), exact: true }).click();
    await expect(answer).toContainText(msg(locale, "safety.rag.refusal"));
    expect(asked.map((body) => (body as Schema<"AskRequest">).language)).toEqual([locale, locale]);

    // A reload keeps the language: it is the session's, not the phone's.
    await page.reload();
    await expectLanguage(page, locale);
    await expect(page.getByTestId("waiting-status").getByTestId("position")).toBeVisible();
  });
}
