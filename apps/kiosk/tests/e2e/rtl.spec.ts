import { readFileSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

// @rtl (M2-01): the kiosk in English and in Arabic, from the language screen to the token, against
// stubbed HTTP. Direction, language and the picker lock are asserted everywhere; the screenshots
// are compared only when the browser runs in the pinned Playwright image (`make rtl`), because
// baselines differ by OS.
const json = <T,>(path: string): T => JSON.parse(readFileSync(new URL(path, import.meta.url), "utf8")) as T;
const fixture = <T,>(name: string): T => json<T>(`../fixtures/api/${name}.json`);

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

const DEVICE = { token: "example-kiosk-token", clinicId: "clinic_demo", deviceId: "kiosk_01" };
const DIGITS = "100001";
// The API asks an Arabic session in Arabic (services/intake/.../templates_ar.yaml, M2-03).
const QUESTIONS: Record<Locale, Record<string, string>> = {
  en: {},
  ar: {
    q_chief_complaint_1: "ما السبب الرئيسي لزيارتك اليوم؟",
    q_symptom_onset_at_1: "متى بدأ ذلك؟",
    q_pain_score_1: "من 0 إلى 10، ما شدة ألمك الآن؟",
  },
};
const NOTICE_AR = "شكراً لك. سيأتي إليك أحد أفراد فريق التمريض قريباً. يرجى البقاء هنا.";

interface Question {
  question_id: string;
  text: string;
  notice: string | null;
}
function localised(question: Question | null, locale: Locale): Question | null {
  if (!question || locale === "en") return question;
  return { ...question, text: QUESTIONS.ar[question.question_id] ?? question.text, notice: question.notice ? NOTICE_AR : null };
}

async function stubApi(page: Page, locale: Locale) {
  const sent: unknown[] = [];
  let checkin: unknown = null;
  await page.addInitScript((device) => window.sessionStorage.setItem("careflow.kiosk.device", JSON.stringify(device)), DEVICE);
  await page.route("**/v1/checkin/keypad", (route) => {
    checkin = route.request().postDataJSON();
    const result = fixture<{ first_question: Question }>("checkin");
    return route.fulfill({ json: { ...result, language: locale, first_question: localised(result.first_question, locale) } });
  });
  const turns = fixture<{ assistant_text: string; next_question: Question | null }[]>("turns");
  await page.route("**/v1/sessions/*/turns", (route) => {
    sent.push(route.request().postDataJSON());
    const turn = turns[sent.length - 1]!;
    const next = localised(turn.next_question, locale);
    return route.fulfill({ json: { ...turn, assistant_text: next?.text ?? turn.assistant_text, next_question: next } });
  });
  await page.route("**/v1/dev/sessions/*/vitals-sim", (route) =>
    route.fulfill({ status: 202, json: { session_id: "ses_e2e", scenario: "normal", frames: 1, simulated: true } }),
  );
  const done = fixture<unknown[]>("done-status");
  let polls = 0;
  await page.route("**/v1/sessions/*/status", (route) => {
    if (route.request().headers().authorization !== "Bearer example-session-token-e2e") {
      return route.fulfill({ json: fixture("vitals-status") });
    }
    polls += 1;
    return route.fulfill({ json: done[Math.min(polls - 1, done.length - 1)] });
  });
  return { sent, checkin: () => checkin };
}

async function expectLanguage(page: Page, locale: Locale) {
  await expect(page.locator("html")).toHaveAttribute("lang", locale);
  await expect(page.locator("html")).toHaveAttribute("dir", locale === "ar" ? "rtl" : "ltr");
}

/** Neither the first-screen choice nor the header toggle may be on screen. */
async function expectNoLanguagePicker(page: Page) {
  await expect(page.getByRole("button", { name: /^(English|العربية)$/ })).toHaveCount(0);
}

async function snap(page: Page, name: string) {
  await page.evaluate(() => document.fonts.ready);
  await expect(page).toHaveScreenshot(`${name}.png`, { fullPage: true });
}

for (const locale of ["en", "ar"] as const) {
  test(`@rtl the kiosk in ${locale === "en" ? "English" : "Arabic"}: language, check-in, intake and token`, async ({ page }) => {
    await page.clock.install({ time: new Date("2026-09-27T09:00:00Z") });
    const stubs = await stubApi(page, locale);
    await page.goto("/");
    await expectLanguage(page, "en"); // no choice yet
    if (locale === "en") await snap(page, "kiosk-language");
    await page.getByRole("main").getByRole("button", { name: msg("en", `home.${locale === "en" ? "english" : "arabic"}`) }).click();

    await expect(page).toHaveURL(/\/consent$/);
    await expectLanguage(page, locale);
    await expectNoLanguagePicker(page);
    await page.getByRole("button", { name: msg(locale, "safety.consent.agree"), exact: true }).click();

    await expect(page).toHaveURL(/\/checkin$/);
    await expectLanguage(page, locale);
    await expectNoLanguagePicker(page);
    // The keypad keeps 1 2 3 left to right in Arabic, like a phone's: its block is dir="ltr".
    const keys = await Promise.all(
      ["1", "2", "3"].map(async (digit) => (await page.getByRole("button", { name: digit, exact: true }).boundingBox())!.x),
    );
    expect(keys[0]).toBeLessThan(keys[1]!);
    expect(keys[1]).toBeLessThan(keys[2]!);
    // Controls work from the keyboard: focus a key, press Enter.
    await page.getByRole("button", { name: DIGITS[0], exact: true }).focus();
    await page.keyboard.press("Enter");
    for (const digit of DIGITS.slice(1)) await page.getByRole("button", { name: digit, exact: true }).click();
    await expect(page.getByLabel(msg(locale, "checkin.display"))).toHaveValue("••••••");
    await snap(page, `kiosk-${locale}-checkin`);
    await page.getByRole("button", { name: msg(locale, "checkin.submit"), exact: true }).click();

    await expect(page).toHaveURL(/\/patient$/);
    await expectNoLanguagePicker(page);
    await page.getByRole("button", { name: msg(locale, "safety.child.me"), exact: true }).click();

    await expect(page).toHaveURL(/\/intake$/);
    await expectLanguage(page, locale);
    await expectNoLanguagePicker(page); // the intake has started: the language is fixed
    const first = locale === "en" ? "What is the main reason for your visit today?" : QUESTIONS.ar.q_chief_complaint_1!;
    await expect(page.getByRole("heading", { name: first })).toBeVisible();
    await snap(page, `kiosk-${locale}-intake`);
    await page.getByRole("button", { name: msg(locale, "intake.answers.chestPain") }).click();
    await expect(page.getByRole("status")).toHaveText(locale === "en" ? "Thank you. A nurse will be with you shortly. Please stay here." : NOTICE_AR);
    await page.getByLabel(msg(locale, "intake.typeLabel")).fill(locale === "en" ? "About an hour ago" : "منذ ساعة تقريباً");
    await page.getByRole("button", { name: msg(locale, "intake.send"), exact: true }).click();
    const scale = page.getByRole("group", { name: msg(locale, "intake.scaleLabel") });
    await expect(scale).toBeVisible();
    await snap(page, `kiosk-${locale}-intake-scale`);
    await scale.getByRole("button", { name: "8", exact: true }).click();

    await expect(page).toHaveURL(/\/vitals$/);
    await expectNoLanguagePicker(page);
    await page.getByRole("button", { name: msg(locale, "vitals.measure") }).click();
    await expect(page.getByRole("region", { name: msg(locale, "vitals.heartRate") }).getByTestId("vital-value")).toHaveText("96");
    await page.getByRole("button", { name: msg(locale, "vitals.continue") }).click();

    await expect(page).toHaveURL(/\/done$/);
    await expectLanguage(page, locale);
    await expectNoLanguagePicker(page);
    // The token reads left to right in both languages, with Western digits (ADR-032).
    const token = page.getByTestId("token-code");
    await expect(token).toHaveText("A-014", { timeout: 10_000 });
    await expect(token).toHaveCSS("direction", "ltr");
    await expect(page.getByTestId("room").locator("bdi")).toHaveText("Resus 2");
    await expect(page.getByTestId("room").locator("bdi")).toHaveCSS("direction", "ltr");
    await expect(page.getByTestId("prototype-footer")).toBeVisible();
    await expect(page.getByTestId("arabic-disclaimer")).toBeVisible();
    await snap(page, `kiosk-${locale}-token`);

    // Every request and the check-in carried the language picked on the first screen.
    expect(stubs.checkin()).toMatchObject({ digits: DIGITS, language: locale });
    expect(stubs.sent.map((body) => (body as { language: string }).language)).toEqual([locale, locale, locale]);

    // The next patient starts on the language screen, which offers both languages again.
    await page.clock.fastForward("00:31");
    await expect(page).toHaveURL(/\/$/);
    await expectLanguage(page, locale); // the last choice stays until someone picks again
    await expect(page.getByRole("main").getByRole("button", { name: "English" })).toBeVisible();
    await expect(page.getByRole("main").getByRole("button", { name: "العربية" })).toBeVisible();
    if (locale === "ar") await snap(page, "kiosk-ar-language");
  });
}
